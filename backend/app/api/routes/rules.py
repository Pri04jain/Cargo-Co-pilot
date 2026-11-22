"""
Rules API — view, validate, and dry-run YAML routing rules.

Endpoints:
  GET  /rules             — list available rules files
  GET  /rules/{file}      — get the content of a rules file
  POST /rules/validate    — validate a YAML rules string (UI live validation)
  POST /rules/dry-run     — test a document against a rules file
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, DB
from app.rules.engine import (
    RuleEvaluationResult,
    build_context,
    evaluate_rules,
    load_rules,
    validate_rules_yaml,
)

router = APIRouter()

RULES_DIR = Path("/app/rules")
if not RULES_DIR.exists():
    RULES_DIR = Path(__file__).parent.parent.parent.parent.parent / "rules"


class ValidateRulesRequest(BaseModel):
    content: str


class ValidateRulesResponse(BaseModel):
    valid: bool
    error: str = ""
    rule_count: int = 0


class DryRunRequest(BaseModel):
    document_id: str
    rules_file: str = "default_rules.yaml"


class DryRunResponse(BaseModel):
    decision: str
    rule_id: str
    reason: str
    evaluation_log: list[dict]


@router.get("")
async def list_rules_files(current_user: CurrentUser) -> dict:
    """List available YAML rules files."""
    if not RULES_DIR.exists():
        return {"files": []}
    files = [f.name for f in sorted(RULES_DIR.glob("*.yaml"))]
    return {"files": files}


@router.get("/{filename}")
async def get_rules_file(filename: str, current_user: CurrentUser) -> dict:
    """Get the content of a rules file."""
    # Security: only allow .yaml files, no path traversal
    if "/" in filename or "\\" in filename or not filename.endswith(".yaml"):
        raise HTTPException(status_code=400, detail="Invalid filename")

    path = RULES_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Rules file '{filename}' not found")

    return {"filename": filename, "content": path.read_text()}


@router.post("/validate", response_model=ValidateRulesResponse)
async def validate_rules(
    payload: ValidateRulesRequest,
    current_user: CurrentUser,
) -> ValidateRulesResponse:
    """
    Validate a YAML rules string without saving it.
    Used by the UI rule editor for live syntax + schema checking.
    """
    is_valid, error_msg = validate_rules_yaml(payload.content)
    rule_count = 0
    if is_valid:
        import yaml
        raw = yaml.safe_load(payload.content)
        rule_count = len(raw.get("rules", []))

    return ValidateRulesResponse(valid=is_valid, error=error_msg, rule_count=rule_count)


@router.post("/dry-run", response_model=DryRunResponse)
async def dry_run_rules(
    payload: DryRunRequest,
    current_user: CurrentUser,
    db: DB,
) -> DryRunResponse:
    """
    Test a document against a rules file without committing any changes.
    Shows which rule fired and why — useful for debugging rules.
    """
    import uuid
    from sqlalchemy import select
    from app.models.document import Document
    from app.models.validation import ValidationResult
    from app.models.extraction import ExtractedField
    from app.models.purchase_order import PurchaseOrder

    doc_id = uuid.UUID(payload.document_id)

    # Scope to tenant
    doc = (await db.execute(
        select(Document).where(
            Document.id == doc_id,
            Document.tenant_id == current_user.tenant_id,
        )
    )).scalar_one_or_none()

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Fetch validation results
    val_results = (await db.execute(
        select(ValidationResult).where(
            ValidationResult.document_id == doc_id,
            ValidationResult.tenant_id == current_user.tenant_id,
        )
    )).scalars().all()

    val_dicts = [
        {
            "check_name": v.check_name,
            "status": v.status,
            "severity": v.severity,
            "message": v.message,
            "field_name": v.field_name,
        }
        for v in val_results
    ]

    # Fetch extracted fields (build minimal extraction dict)
    fields = (await db.execute(
        select(ExtractedField).where(
            ExtractedField.document_id == doc_id,
            ExtractedField.tenant_id == current_user.tenant_id,
        )
    )).scalars().all()

    extraction_data: dict = {}
    po_data = None

    for f in fields:
        if f.field_name == "total_amount":
            extraction_data["total_amount"] = {"value": f.value}
        elif f.field_name == "currency":
            extraction_data["currency"] = {"value": f.value}
        elif f.field_name == "po_reference" and f.value:
            po = (await db.execute(
                select(PurchaseOrder).where(
                    PurchaseOrder.tenant_id == current_user.tenant_id,
                    PurchaseOrder.po_number == f.value.strip(),
                )
            )).scalar_one_or_none()
            if po:
                po_data = {"total_amount": po.total_amount, "currency": po.currency}

    extraction_data["_grounding_rate"] = doc.grounding_rate or 0.0
    extraction_data["_overall_confidence"] = doc.overall_confidence or 0.0

    context = build_context(
        validation_results=val_dicts,
        extraction_data=extraction_data,
        po_data=po_data,
    )

    try:
        result = evaluate_rules(context, rules_file=payload.rules_file)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Rules file '{payload.rules_file}' not found",
        )

    return DryRunResponse(
        decision=result.decision,
        rule_id=result.rule_id,
        reason=result.reason,
        evaluation_log=result.evaluation_log,
    )
