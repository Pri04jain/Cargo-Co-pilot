"""
YAML Rules Engine — evaluates routing rules against document context.

Design:
  - Rules are stored in YAML files, not Python code.
  - The engine loads rules, builds an evaluation context from extraction
    and validation results, and finds the first matching rule.
  - Expressions are evaluated with a SAFE evaluator — NOT eval() on raw strings.
  - The safe evaluator supports: comparisons, arithmetic, boolean operators,
    field references, and a small whitelist of functions (pct_diff, abs, round).

Why not eval()?
  eval() on untrusted input is a security risk. A malicious YAML rule like
  `when: "__import__('os').system('rm -rf /')"` would execute arbitrary code.
  Our evaluator parses the expression into an AST and only allows whitelisted
  node types and function names.

Rule format (see rules/default_rules.yaml):
  - id: unique string
  - priority: integer (lower = higher priority, evaluated first)
  - when: expression string
  - then: {decision: str, reason: str}
"""

from __future__ import annotations

import ast
import operator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml


def _get_logger():
    """Lazy logger so this module is importable without structlog (for local tests)."""
    try:
        from app.core.logging import get_logger
        return get_logger(__name__)
    except ImportError:
        import logging
        return logging.getLogger(__name__)


logger = _get_logger()
RULES_DIR = Path(__file__).parent.parent.parent.parent / "rules"
if not RULES_DIR.exists():
    RULES_DIR = Path("/app/rules")

DEFAULT_RULES_FILE = "default_rules.yaml"


@dataclass
class Rule:
    id: str
    priority: int
    when: str
    decision: str
    reason: str


@dataclass
class RuleEvaluationResult:
    decision: str
    rule_id: str
    reason: str
    rules_file: str
    evaluation_log: list[dict]  # which rules were tested and why they matched/didn't


# ── Context builder ───────────────────────────────────────────────────────────

def build_context(
    validation_results: list[dict],
    extraction_data: dict,
    po_data: Optional[dict] = None,
) -> dict:
    """
    Build the evaluation context dict from pipeline outputs.

    Context variables available in rule expressions:
      validation.error_count      int
      validation.warning_count    int
      invoice.total_amount        float | None
      invoice.currency            str | None
      po.total_amount             float | None
      po.currency                 str | None
      doc.grounding_rate          float
      doc.overall_confidence      float
    """
    error_count = sum(1 for r in validation_results if r.get("status") == "fail" and r.get("severity") == "error")
    warning_count = sum(1 for r in validation_results if r.get("status") == "fail" and r.get("severity") == "warning")

    # Extract invoice total and currency from extraction data
    invoice_total = None
    invoice_currency = None

    # extraction_data is the model_dump() of the invoice schema
    total_field = extraction_data.get("total_amount", {})
    if isinstance(total_field, dict) and total_field.get("value"):
        try:
            invoice_total = float(total_field["value"].replace(",", ""))
        except (ValueError, AttributeError):
            pass

    currency_field = extraction_data.get("currency", {})
    if isinstance(currency_field, dict):
        invoice_currency = currency_field.get("value")

    return {
        "validation": {
            "error_count": error_count,
            "warning_count": warning_count,
        },
        "invoice": {
            "total_amount": invoice_total,
            "currency": invoice_currency,
        },
        "po": {
            "total_amount": po_data.get("total_amount") if po_data else None,
            "currency": po_data.get("currency") if po_data else None,
        },
        "doc": {
            "grounding_rate": extraction_data.get("_grounding_rate", 0.0),
            "overall_confidence": extraction_data.get("_overall_confidence", 0.0),
        },
    }


# ── Rules loader ──────────────────────────────────────────────────────────────

def load_rules(rules_file: str = DEFAULT_RULES_FILE) -> list[Rule]:
    """Load and validate rules from a YAML file. Raises on invalid format."""
    path = RULES_DIR / rules_file
    if not path.exists():
        raise FileNotFoundError(f"Rules file not found: {path}")

    raw = yaml.safe_load(path.read_text())

    if not isinstance(raw, dict) or "rules" not in raw:
        raise ValueError(f"Rules file must have a top-level 'rules' key: {path}")

    rules = []
    for r in raw["rules"]:
        _validate_rule_schema(r)
        rules.append(Rule(
            id=r["id"],
            priority=r["priority"],
            when=r["when"],
            decision=r["then"]["decision"],
            reason=r["then"]["reason"],
        ))

    # Sort by priority (ascending — lower number = higher priority)
    rules.sort(key=lambda r: r.priority)

    logger.debug("rules_loaded", file=rules_file, count=len(rules))
    return rules


def _validate_rule_schema(r: dict) -> None:
    """Raise ValueError if a rule dict is missing required keys."""
    required = ["id", "priority", "when", "then"]
    for key in required:
        if key not in r:
            raise ValueError(f"Rule is missing required key '{key}': {r}")
    if not isinstance(r["then"], dict):
        raise ValueError(f"Rule 'then' must be a dict: {r}")
    for key in ["decision", "reason"]:
        if key not in r["then"]:
            raise ValueError(f"Rule 'then' is missing key '{key}': {r}")


# ── Rules evaluator ───────────────────────────────────────────────────────────

def evaluate_rules(
    context: dict,
    rules_file: str = DEFAULT_RULES_FILE,
) -> RuleEvaluationResult:
    """
    Evaluate all rules against the context and return the first match.
    Rules are sorted by priority — lowest number wins.
    """
    rules = load_rules(rules_file)
    evaluation_log = []

    for rule in rules:
        try:
            matched = safe_eval(rule.when, context)
        except Exception as e:
            logger.warning("rule_eval_error rule=%s error=%s", rule.id, str(e))
            evaluation_log.append({
                "rule_id": rule.id,
                "when": rule.when,
                "matched": False,
                "error": str(e),
            })
            continue

        evaluation_log.append({
            "rule_id": rule.id,
            "when": rule.when,
            "matched": bool(matched),
            "decision": rule.decision if matched else None,
        })

        if matched:
            logger.info(
                "rule_matched",
                rule_id=rule.id,
                decision=rule.decision,
            )
            return RuleEvaluationResult(
                decision=rule.decision,
                rule_id=rule.id,
                reason=rule.reason,
                rules_file=rules_file,
                evaluation_log=evaluation_log,
            )

    # Fallback if no rule matched (shouldn't happen with a default rule)
    return RuleEvaluationResult(
        decision="exception_queue",
        rule_id="fallback",
        reason="No rules matched — defaulting to exception queue",
        rules_file=rules_file,
        evaluation_log=evaluation_log,
    )


# ── Safe expression evaluator ─────────────────────────────────────────────────

# Allowed AST node types (everything else raises SecurityError)
ALLOWED_NODES = {
    ast.Expression, ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Compare,
    ast.Constant, ast.Name, ast.Attribute, ast.Call, ast.IfExp,
    ast.And, ast.Or, ast.Not,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.Is, ast.IsNot, ast.In, ast.NotIn,
    ast.USub, ast.UAdd,
    ast.Load,
}

# Whitelisted function names
ALLOWED_FUNCTIONS = {"pct_diff", "abs", "round", "min", "max", "len"}


def safe_eval(expr: str, context: dict) -> Any:
    """
    Safely evaluate a rule expression against a context dict.

    Supports:
      - Dot notation for nested context: validation.error_count
      - Comparisons: >, <, >=, <=, ==, !=
      - Boolean: and, or, not
      - Arithmetic: +, -, *, /
      - Null check: == null, != null
      - Functions: pct_diff(a, b), abs(x), round(x, n)
      - Literals: numbers, strings, true/false/null

    Raises SecurityError on any disallowed construct.
    """
    # Normalise "null" → "None" and "true"/"false" → Python booleans
    expr_py = (
        expr
        .replace(" null", " None")
        .replace("(null", "(None")
        .replace("null)", "None)")
        .replace("true", "True")
        .replace("false", "False")
    )

    try:
        tree = ast.parse(expr_py, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"Invalid expression syntax: {expr!r} — {e}")

    _check_ast_safety(tree)

    # Flatten context for eval (support dot notation via dotdict)
    flat_context = _DotDict(context)
    flat_context["pct_diff"] = _pct_diff
    flat_context["abs"] = abs
    flat_context["round"] = round
    flat_context["min"] = min
    flat_context["max"] = max
    flat_context["len"] = len
    flat_context["None"] = None
    flat_context["True"] = True
    flat_context["False"] = False

    return eval(compile(tree, "<rule>", "eval"), {"__builtins__": {}}, flat_context)  # noqa: S307


def _check_ast_safety(node: ast.AST) -> None:
    """Recursively check that every AST node is in the allowed set."""
    for child in ast.walk(node):
        if type(child) not in ALLOWED_NODES:
            raise SecurityError(
                f"Expression contains disallowed construct: {type(child).__name__}"
            )
        # Check function call names
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name) and func.id not in ALLOWED_FUNCTIONS:
                raise SecurityError(f"Function '{func.id}' is not allowed in rules")
            if isinstance(func, ast.Attribute):
                raise SecurityError("Method calls are not allowed in rules")


class SecurityError(ValueError):
    """Raised when a rule expression tries to use a disallowed construct."""


class _DotDict(dict):
    """
    Dict subclass that supports dot-notation attribute access for nested dicts.
    Allows `validation.error_count` to resolve to context["validation"]["error_count"].
    Wraps nested dicts lazily so chained access works: validation.error_count
    """
    def __getattr__(self, name: str):
        try:
            val = self[name]
            if isinstance(val, dict):
                return _DotDict(val)
            return val
        except KeyError:
            return None  # Missing keys return None (not AttributeError)

    def __getitem__(self, key):
        val = super().__getitem__(key)
        if isinstance(val, dict):
            return _DotDict(val)
        return val


def _pct_diff(a: Optional[float], b: Optional[float]) -> float:
    """Percentage difference: |a - b| / b * 100. Returns 0 if b is None/0."""
    if a is None or b is None or b == 0:
        return 0.0
    return abs(a - b) / abs(b) * 100


def validate_rules_yaml(content: str) -> tuple[bool, str]:
    """
    Parse and validate a YAML rules file content string.
    Returns (is_valid, error_message).
    Used by the UI rule editor for live validation.
    """
    try:
        raw = yaml.safe_load(content)
        if not isinstance(raw, dict) or "rules" not in raw:
            return False, "Missing top-level 'rules' key"
        for r in raw["rules"]:
            _validate_rule_schema(r)
            # Test that the expression parses without errors
            expr = r["when"]
            if expr.strip().lower() != "true":
                try:
                    safe_eval(expr, {
                        "validation": {"error_count": 0, "warning_count": 0},
                        "invoice": {"total_amount": 0.0, "currency": "USD"},
                        "po": {"total_amount": 0.0, "currency": "USD"},
                        "doc": {"grounding_rate": 1.0, "overall_confidence": 1.0},
                    })
                except (SecurityError, ValueError) as e:
                    return False, f"Rule '{r['id']}' has invalid expression: {e}"
        return True, ""
    except yaml.YAMLError as e:
        return False, f"YAML parse error: {e}"
    except ValueError as e:
        return False, str(e)
