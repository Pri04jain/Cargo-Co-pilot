"""Import all models here so Alembic can discover them for autogenerate."""

from app.models.base import Base  # noqa: F401
from app.models.tenant import Tenant  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.document import Document, DocumentPage  # noqa: F401
from app.models.extraction import Extraction, ExtractedField  # noqa: F401
from app.models.validation import ValidationResult  # noqa: F401
from app.models.routing import RoutingDecision  # noqa: F401
from app.models.review import ReviewAction  # noqa: F401
from app.models.purchase_order import PurchaseOrder  # noqa: F401
from app.models.audit import AuditLog  # noqa: F401
from app.models.chunk import Chunk  # noqa: F401
from app.models.llm_call import LLMCall  # noqa: F401
