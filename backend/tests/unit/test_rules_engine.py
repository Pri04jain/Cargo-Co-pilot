"""
Unit tests for the YAML rules engine.

Tests cover:
  - safe_eval: allowed operators, field references, pct_diff function
  - safe_eval: blocked constructs (import, exec, method calls)
  - build_context: correct mapping from pipeline outputs
  - evaluate_rules: correct rule priority ordering and first-match semantics
  - validate_rules_yaml: valid and invalid YAML inputs
"""

import pytest

from app.rules.engine import (
    SecurityError,
    _DotDict,
    _pct_diff,
    build_context,
    safe_eval,
    validate_rules_yaml,
)


# ── _pct_diff ─────────────────────────────────────────────────────────────────

def test_pct_diff_basic():
    assert _pct_diff(110, 100) == pytest.approx(10.0)
    assert _pct_diff(90, 100) == pytest.approx(10.0)
    assert _pct_diff(100, 100) == pytest.approx(0.0)


def test_pct_diff_none():
    assert _pct_diff(None, 100) == 0.0
    assert _pct_diff(100, None) == 0.0
    assert _pct_diff(100, 0) == 0.0


# ── DotDict ───────────────────────────────────────────────────────────────────

def test_dotdict_nested_access():
    d = _DotDict({"validation": {"error_count": 3, "warning_count": 1}})
    assert d.validation.error_count == 3
    assert d.validation.warning_count == 1


def test_dotdict_missing_key_returns_none():
    d = _DotDict({"a": {"b": 1}})
    assert d.missing is None
    assert d.a.missing is None


# ── safe_eval: allowed expressions ───────────────────────────────────────────

class TestSafeEvalAllowed:
    def ctx(self):
        return {
            "validation": {"error_count": 2, "warning_count": 1},
            "invoice": {"total_amount": 15500.0, "currency": "USD"},
            "po": {"total_amount": 15000.0, "currency": "USD"},
            "doc": {"grounding_rate": 0.85, "overall_confidence": 0.9},
        }

    def test_simple_comparison(self):
        assert safe_eval("validation.error_count > 0", self.ctx()) is True
        assert safe_eval("validation.error_count > 5", self.ctx()) is False

    def test_equality(self):
        assert safe_eval("invoice.currency == po.currency", self.ctx()) is True

    def test_inequality(self):
        assert safe_eval("invoice.currency != po.currency", {
            "invoice": {"currency": "USD"},
            "po": {"currency": "EUR"},
        }) is True

    def test_and(self):
        assert safe_eval(
            "validation.error_count == 0 and doc.grounding_rate > 0.5",
            {**self.ctx(), "validation": {"error_count": 0, "warning_count": 0}},
        ) is True

    def test_or(self):
        assert safe_eval("validation.error_count > 5 or validation.warning_count > 0", self.ctx()) is True

    def test_pct_diff_function(self):
        # 15500 vs 15000 = 3.33%
        result = safe_eval("pct_diff(invoice.total_amount, po.total_amount) <= 5", self.ctx())
        assert result is True

    def test_pct_diff_over_threshold(self):
        ctx = {
            "invoice": {"total_amount": 20000.0},
            "po": {"total_amount": 15000.0},
        }
        result = safe_eval("pct_diff(invoice.total_amount, po.total_amount) > 10", ctx)
        assert result is True

    def test_true_literal(self):
        assert safe_eval("true", {}) is True

    def test_null_check(self):
        ctx = {"po": {"total_amount": None}}
        assert safe_eval("po.total_amount == null", ctx) is True

    def test_arithmetic(self):
        ctx = {"doc": {"grounding_rate": 0.85}}
        assert safe_eval("doc.grounding_rate * 100 > 80", ctx) is True

    def test_abs_function(self):
        assert safe_eval("abs(-5) == 5", {}) is True


# ── safe_eval: blocked constructs ────────────────────────────────────────────

class TestSafeEvalBlocked:
    def test_import_blocked(self):
        with pytest.raises((SecurityError, ValueError, SyntaxError)):
            safe_eval("__import__('os')", {})

    def test_exec_blocked(self):
        # exec is a statement, not an expression — should fail at parse
        with pytest.raises((SecurityError, ValueError, SyntaxError)):
            safe_eval("exec('pass')", {})

    def test_lambda_blocked(self):
        with pytest.raises((SecurityError, ValueError, SyntaxError)):
            safe_eval("(lambda: 42)()", {})

    def test_attribute_method_blocked(self):
        with pytest.raises((SecurityError, ValueError, SyntaxError)):
            safe_eval("validation.error_count.bit_length()", {
                "validation": {"error_count": 3}
            })

    def test_unknown_function_blocked(self):
        with pytest.raises(SecurityError):
            safe_eval("open('/etc/passwd')", {})

    def test_list_comprehension_blocked(self):
        with pytest.raises((SecurityError, ValueError, SyntaxError)):
            safe_eval("[x for x in range(10)]", {})


# ── build_context ─────────────────────────────────────────────────────────────

class TestBuildContext:
    def test_error_count_from_validation_results(self):
        val_results = [
            {"status": "fail", "severity": "error", "check_name": "c1"},
            {"status": "fail", "severity": "error", "check_name": "c2"},
            {"status": "fail", "severity": "warning", "check_name": "c3"},
            {"status": "pass", "severity": "info", "check_name": "c4"},
        ]
        ctx = build_context(val_results, {})
        assert ctx["validation"]["error_count"] == 2
        assert ctx["validation"]["warning_count"] == 1

    def test_invoice_total_extracted(self):
        extraction = {"total_amount": {"value": "15,250.00"}}
        ctx = build_context([], extraction)
        assert ctx["invoice"]["total_amount"] == pytest.approx(15250.0)

    def test_po_data_mapped(self):
        ctx = build_context([], {}, po_data={"total_amount": 15000.0, "currency": "USD"})
        assert ctx["po"]["total_amount"] == 15000.0
        assert ctx["po"]["currency"] == "USD"

    def test_no_po_data(self):
        ctx = build_context([], {})
        assert ctx["po"]["total_amount"] is None


# ── validate_rules_yaml ───────────────────────────────────────────────────────

class TestValidateRulesYaml:
    def test_valid_yaml(self):
        content = """
version: 1
rules:
  - id: test_rule
    priority: 1
    when: "validation.error_count > 0"
    then:
      decision: exception_queue
      reason: "Has errors"
  - id: default
    priority: 99
    when: "true"
    then:
      decision: auto_approve
      reason: "All checks passed"
"""
        valid, error = validate_rules_yaml(content)
        assert valid is True
        assert error == ""

    def test_missing_required_key(self):
        content = """
version: 1
rules:
  - id: test_rule
    priority: 1
    when: "true"
"""
        valid, error = validate_rules_yaml(content)
        assert valid is False
        assert "then" in error

    def test_invalid_yaml_syntax(self):
        valid, error = validate_rules_yaml("{ not valid yaml :")
        assert valid is False

    def test_invalid_expression_caught(self):
        content = """
version: 1
rules:
  - id: bad_rule
    priority: 1
    when: "open('/etc/passwd')"
    then:
      decision: auto_approve
      reason: "should not reach here"
"""
        valid, error = validate_rules_yaml(content)
        assert valid is False
        assert "bad_rule" in error


# ── evaluate_rules (integration with actual YAML file) ────────────────────────

class TestEvaluateRules:
    def test_rules_file_exists(self):
        """The default rules file should load without error."""
        from pathlib import Path
        from app.rules.engine import RULES_DIR
        rules_path = RULES_DIR / "default_rules.yaml"
        assert rules_path.exists(), f"Rules file not found at {rules_path}"

    def test_validation_errors_route_to_exception(self):
        from app.rules.engine import evaluate_rules
        context = {
            "validation": {"error_count": 2, "warning_count": 0},
            "invoice": {"total_amount": 15000.0, "currency": "USD"},
            "po": {"total_amount": 15000.0, "currency": "USD"},
            "doc": {"grounding_rate": 0.9, "overall_confidence": 0.9},
        }
        result = evaluate_rules(context)
        assert result.decision == "exception_queue"
        assert result.rule_id == "validation_errors"

    def test_exact_po_match_auto_approves(self):
        from app.rules.engine import evaluate_rules
        context = {
            "validation": {"error_count": 0, "warning_count": 0},
            "invoice": {"total_amount": 15000.0, "currency": "USD"},
            "po": {"total_amount": 15000.0, "currency": "USD"},
            "doc": {"grounding_rate": 0.9, "overall_confidence": 0.9},
        }
        result = evaluate_rules(context)
        assert result.decision == "auto_approve"

    def test_medium_mismatch_requires_manager(self):
        from app.rules.engine import evaluate_rules
        # 6% mismatch → manager_approval
        context = {
            "validation": {"error_count": 0, "warning_count": 0},
            "invoice": {"total_amount": 15900.0, "currency": "USD"},
            "po": {"total_amount": 15000.0, "currency": "USD"},
            "doc": {"grounding_rate": 0.9, "overall_confidence": 0.9},
        }
        result = evaluate_rules(context)
        assert result.decision == "manager_approval"

    def test_large_mismatch_goes_to_exception(self):
        from app.rules.engine import evaluate_rules
        # 20% mismatch → exception_queue
        context = {
            "validation": {"error_count": 0, "warning_count": 0},
            "invoice": {"total_amount": 18000.0, "currency": "USD"},
            "po": {"total_amount": 15000.0, "currency": "USD"},
            "doc": {"grounding_rate": 0.9, "overall_confidence": 0.9},
        }
        result = evaluate_rules(context)
        assert result.decision == "exception_queue"
