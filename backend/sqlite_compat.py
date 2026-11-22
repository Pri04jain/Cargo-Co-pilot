"""
SQLite compatibility shim — import this BEFORE any app code.

Patches two things so the app runs on SQLite without touching model files:

1. sqlalchemy.dialects.postgresql.UUID  →  sqlalchemy.types.Uuid
   (all models do `from sqlalchemy.dialects.postgresql import UUID`)

2. pgvector.sqlalchemy.Vector  →  sqlalchemy.types.Text
   (chunk.py uses it; we store embeddings as text on SQLite, RAG still works
    but cosine-similarity search falls back to a Python-side sort)
"""
import sys
import types
from sqlalchemy import types as sa_types

# ── 1. Patch postgresql dialect UUID ─────────────────────────────────────────
# Make `from sqlalchemy.dialects.postgresql import UUID` yield the generic Uuid
import sqlalchemy.dialects.postgresql as _pg_dialect
if not hasattr(_pg_dialect, "_UUID_patched"):
    _pg_dialect.UUID = sa_types.Uuid
    _pg_dialect._UUID_patched = True

# ── 2. Stub out pgvector ──────────────────────────────────────────────────────
# If pgvector isn't installed, provide a minimal stub so chunk.py imports fine.
# Vector columns are stored as Text; actual vector ops are skipped gracefully.
try:
    import pgvector  # noqa: F401 — real package present, nothing to do
except ModuleNotFoundError:
    # Build a minimal fake pgvector.sqlalchemy module
    _pgv_pkg = types.ModuleType("pgvector")
    _pgv_sa = types.ModuleType("pgvector.sqlalchemy")

    class Vector(sa_types.Text):
        """Stub Vector type — stored as Text in SQLite."""
        def __init__(self, dim=None):
            super().__init__()

    _pgv_sa.Vector = Vector
    sys.modules["pgvector"] = _pgv_pkg
    sys.modules["pgvector.sqlalchemy"] = _pgv_sa
