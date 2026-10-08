# Cargo Copilot 🚢

An agentic document-processing platform for logistics and freight. Upload a Bill of Lading,
Commercial Invoice, or Packing List — Cargo Copilot extracts structured data, shows you
exactly where each value came from on the page, validates it against business rules and
Purchase Orders, routes it for approval, and lets you ask questions about your documents
in plain English.

Built as a portfolio project demonstrating production-quality AI/full-stack engineering.

---

## Architecture

```mermaid
graph TD
    U[User / UI] -->|Upload PDF/image| API[FastAPI]
    API -->|Enqueue| Q[Redis Queue]
    Q --> W[Celery Worker]

    W --> G[LangGraph Pipeline]

    G --> N1[ingest\nstore file, create record]
    N1 --> N2[parse\nPyMuPDF / Tesseract OCR\nwords + bounding boxes]
    N2 --> N3[classify\nLLM: doc type]
    N3 --> N4[extract\nLLM: structured fields\n+ snippet per field]
    N4 --> N5[ground\nmatch snippets → bboxes\nadjust confidence]
    N5 --> N6[validate\ndeterministic checks\nPO cross-check]
    N6 -->|failing fields + retries left| N7[retry_extract\nfocused re-prompt]
    N7 --> N5
    N6 -->|pass or max retries| N8[route\nYAML rules engine]
    N8 --> N9[finalize\nset status, audit log\ncost totals]

    N9 -->|auto_approve| DONE[Done]
    N9 -->|needs review| RQ[Review Queue]
    RQ --> H[Human Reviewer]
    H -->|approve/reject/correct| DB[(PostgreSQL\n+ pgvector)]

    DB --> RAG[Ask Copilot\nHybrid SQL + vector RAG]
    API --> RAG
```

### Design decisions

| Decision | Choice | Why |
|---|---|---|
| Pipeline orchestration | LangGraph | Typed state, conditional retry edge, visualisable |
| LLM output | JSON schema / tool calls | Guaranteed structure, Pydantic validation, no regex |
| Evidence grounding | Snippet matching → bbox | LLMs understand text; we map text→coordinates |
| Validators | Deterministic Python | Faster, cheaper, explainable — no LLM for math |
| Routing rules | YAML + safe evaluator | Non-engineers can change rules without code deploy |
| Vector store | pgvector in Postgres | One database, JOIN structured + vector in one query |
| Background tasks | Celery + Redis | Handles 30s+ processing without blocking HTTP |
| Auth | JWT + bcrypt, tenant_id everywhere | Simple, explainable, real multi-tenancy |

---

## Setup (under 5 commands)

```bash
git clone <repo-url> cargo-copilot && cd cargo-copilot
cp .env.example .env          # add your LLM_API_KEY
make up                        # builds images, runs migrations
make seed                      # creates demo tenants, users, POs
# open http://localhost:5173
```

**Demo credentials** (created by `make seed`):
```
admin@apexfreight.com    / demo_password_1   (Tenant: Apex Freight)
reviewer@apexfreight.com / demo_password_1
admin@blueocean.com      / demo_password_2   (Tenant: Blue Ocean Logistics)
```

---

## 2-minute demo script

1. Log in as `admin@apexfreight.com`
2. Upload one of the sample PDFs from `data/synthetic/`
3. Watch the status change: queued → processing → needs_review
4. Click the document — see extracted fields on the right, highlighted in the PDF on the left
5. Click a field value to see its evidence highlight jump to the right position on the page
6. Review the validation results (green passes, yellow warnings, red errors)
7. See the routing decision and which rule fired
8. Approve or correct a field value
9. Open Ask Copilot and ask: *"What is the incoterm on this document?"*
10. Log out, log in as `admin@blueocean.com` — their documents are invisible (tenant isolation)

---

## Folder structure

```
cargo-copilot/
├── backend/
│   ├── app/
│   │   ├── api/routes/      # FastAPI route handlers
│   │   ├── core/            # config, logging, security
│   │   ├── db/              # SQLAlchemy session
│   │   ├── models/          # ORM models (all tables)
│   │   ├── pipeline/        # LangGraph nodes (Phase 3+)
│   │   ├── schemas/         # Pydantic request/response schemas
│   │   ├── services/        # shared DB helpers
│   │   └── workers/         # Celery app + tasks
│   ├── alembic/             # DB migrations
│   ├── scripts/             # demo_data.py seed script
│   └── tests/               # pytest unit + integration tests
├── frontend/
│   └── src/
│       ├── components/      # Layout, shared UI
│       ├── pages/           # Login, Documents, Detail, Review, Rules, Chat, Metrics
│       ├── lib/             # api.ts, auth.ts (Zustand)
│       └── types/           # TypeScript interfaces
├── prompts/                 # Versioned LLM prompt files
├── rules/                   # YAML routing rules
├── eval/                    # Evaluation harness + reports
├── data/synthetic/          # Generated test documents + ground-truth labels
├── docs/                    # INTERVIEW_NOTES.md
├── docker-compose.yml
├── Makefile
└── .env.example
```

---

## Evaluation results

> ⚠️ **Results will be added here after Phase 7 completes.**
> All numbers will come from `make eval` run against the labeled test split.
> The test split (10 documents) was held out and not used during prompt development.
> Train split: 30 documents. Test split: 10 documents.

Placeholder for Phase 7 report:
```
Field-level accuracy (test split, N=10 documents):
  Overall:              —
  Bill of Lading:       —
  Commercial Invoice:   —
  Packing List:         —

Evidence grounding rate:  —
Routing accuracy:         —
Avg latency / document:   —
Avg cost / document:      —
```

---

## Known limitations

- **No refresh tokens**: JWT access tokens expire after 60 minutes. Re-login required.
- **Local file storage**: Files are stored on the container volume. Production should use S3/GCS.
- **Sync Celery worker**: Workers use a sync DB session. Phase 7 will evaluate switching to async.
- **OCR quality**: Heavily degraded scans (< 150 DPI, severe skew) may produce low grounding rates.
- **Single rules file**: Phase 1 ships one YAML rules file per tenant. Multi-file rule sets are Phase 7+.
- **No email verification**: Users are active immediately on registration.

---

## What I would do next

1. **Server-Sent Events** for real-time status instead of polling
2. **PostgreSQL Row-Level Security** for stronger tenant isolation
3. **Fine-tune a small model** (e.g. Phi-3) on the eval set's corrections to reduce LLM costs
4. **Async Celery worker** using `asyncio` loop to unify the two DB session patterns
5. **S3/GCS file storage** with presigned URLs for the frontend PDF viewer
6. **Rate limiting** per tenant (not just per IP)
7. **Document similarity** — flag potential duplicates before processing
8. **Webhook notifications** when documents reach final status

---

## Tech stack

| Layer | Tech |
|---|---|
| API | Python 3.11, FastAPI, Pydantic v2 |
| Database | PostgreSQL 16 + pgvector |
| Migrations | Alembic |
| Queue | Redis + Celery |
| Pipeline | LangGraph |
| LLM | OpenAI (swappable via env var) |
| PDF parsing | PyMuPDF |
| OCR | Tesseract via pytesseract |
| Observability | Langfuse |
| Frontend | React 18, TypeScript, Vite, Tailwind CSS |
| PDF viewer | react-pdf + SVG overlay |
| State | Zustand, TanStack Query |
| Tests | pytest, pytest-asyncio |
| Lint/format | ruff |
| Infra | Docker Compose |

---

## Prompt injection awareness

Document text is injected into prompts between explicit delimiters
(`--- BEGIN DOCUMENT ---` / `--- END DOCUMENT ---`) and described as
"data, not instructions." Structured output constrains the LLM to a JSON schema
so injected text cannot redirect its behaviour. Ask Copilot only retrieves and
cites data — it does not execute or act on retrieved document content.
