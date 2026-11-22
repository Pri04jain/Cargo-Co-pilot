# Cargo Copilot — Full-Stack Interview Study Guide

Companion to `INTERVIEW_NOTES.md`. This is the end-to-end walkthrough: architecture,
every subsystem, design decisions, failure stories, likely Q&A, and weak spots to own.

---

## 1. The 30-second pitch

> Cargo Copilot is a document-processing platform for logistics teams. You upload a
> Bill of Lading, Commercial Invoice, or Packing List. A background pipeline parses the
> PDF (PyMuPDF or Tesseract OCR), an LLM classifies the doc type and extracts structured
> fields as JSON, every field gets grounded back to its location on the page so a
> reviewer can see exactly where the value came from, deterministic validators check
> math and cross-check against Purchase Orders, and a YAML rules engine routes the
> document to auto-approve, manager review, or an exception queue. There's also a RAG
> chat interface for querying documents in plain English. It's multi-tenant, async,
> and has a real evaluation harness — 96% classification accuracy, 82% field accuracy,
> $0.025 per document.

## 2. Tech stack

| Layer | Tech |
|---|---|
| API | Python 3.11, FastAPI, Pydantic v2 (async SQLAlchemy) |
| Workers | Celery (sync sessions), Redis broker |
| DB | PostgreSQL 16 + pgvector, Alembic migrations |
| Pipeline | Typed-state node pipeline (LangGraph structure) |
| LLM | OpenAI / Anthropic / any OpenAI-compatible endpoint, env-swappable |
| Parsing | PyMuPDF (digital), Tesseract via pytesseract (scans) |
| Observability | structlog, Langfuse (optional tracing + cost) |
| Frontend | React 18, TypeScript, Vite, Tailwind, Zustand, TanStack Query, react-pdf |
| Infra | Docker Compose, Makefile targets (up, seed, eval...) |

## 3. End-to-end document flow

```
Upload (202, JWT+tenant scoped, rate-limited)
  → file saved to uploads/{tenant_id}/{doc_id}.ext (aiofiles)
  → Document row (status=QUEUED) + audit log
  → Celery task dispatched (process_document.delay)
Worker (sync session):
  parse      PyMuPDF/Tesseract → pages: raw_text + WordBox[x0,y0,x1,y1]
  classify   LLM → doc type (prompt classify_v1, temp 0)
  extract    LLM, JSON-schema strict output → typed Pydantic schema per doc type
             each field: value, confidence, snippet, page
  ground     snippet → sliding-window token match over WordBoxes → union bbox
             exact: no penalty | fuzzy(≥80% overlap): −0.1 | none: ungrounded, conf×0.5
  validate   deterministic: math (sum of lines == total), ISO 6346 container format,
             cross-doc (invoice vs PO), currency checks → errors/warnings/infos
  retry      failing fields re-prompted (focused, format hints), re-ground, max retries
  route      YAML rules engine → auto_approve | manager_approval | exception_queue
  finalize   status, cost/token/confidence totals, audit log, RAG chunk indexing
UI: review queue → approve / reject / correct field (all audit-logged)
Ask Copilot: hybrid SQL + pgvector retrieval → cited answer or refusal
```

## 4. Key files to be able to talk about

- `backend/app/pipeline/graph.py` — node runner + finalize (always runs, even on failure)
- `backend/app/pipeline/state.py` — typed dataclass state, every node's contract
- `backend/app/parsers/grounder.py` — the evidence grounding algorithm
- `backend/app/llm/provider.py` — retry, cost table, structured output, Langfuse
- `backend/app/rules/engine.py` — AST-whitelist safe evaluator, YAML loading/validation
- `backend/app/rag/retriever.py` — hybrid SQL + vector merge with boost
- `backend/app/rag/chat_service.py` — anti-hallucination chat flow
- `backend/app/api/routes/documents.py` — upload, list, fields, validation, routing,
  audit, review actions, page data, batch upload
- `backend/app/core/security.py` — JWT, bcrypt, hashed refresh tokens
- `frontend/src/lib/api.ts` — silent refresh interceptor with in-flight lock + queue
- `rules/default_rules.yaml` — the actual routing policy
- `eval/` — accuracy/grounding/cost/latency harness + dashboard

## 5. Design decisions (with the "why")

1. **Snippet grounding, not coordinate extraction** — LLMs understand text, not pixels.
   Trade-off: paraphrased snippets fail to match → ungrounded → conservative routing.
2. **Deterministic validators** — the LLM extracts; code decides. Math/format/cross-doc
   checks in Python: faster, cheaper, explainable.
3. **JSON-schema-strict LLM output** — guaranteed structure, Pydantic validation, no regex.
4. **YAML rules + AST-whitelist evaluator** — non-engineers change routing without deploys;
   no eval() RCE risk.
5. **pgvector in Postgres** — one DB; JOIN structured fields with vector search.
6. **Celery + Redis** — 2–30s CPU/LLM work off the HTTP path; retries with backoff
   (2^retries × 30s), horizontal worker scaling.
7. **Hybrid retrieval** — exact/structured questions via SQL on extracted fields (0.9 base
   score), semantic questions via cosine search (min 0.25); +0.2 boost when both hit.
8. **Hand-rolled LLM wrapper, not LangChain** — explicit retry/cost/tracing, defensible.
9. **Multi-tenancy via tenant_id in the JWT and in every query** — enforced at query level
   everywhere; demo: log in as second tenant, first tenant's docs are invisible.

## 6. Auth specifics

- Access JWT 60 min, carries sub + tenant_id + email (no per-request DB lookup).
- Refresh: opaque 64-byte hex, 30 days, **stored bcrypt-hashed**, rotated on each refresh.
- Frontend interceptor: single-flight refresh, queued waiting requests, redirect only on
  refresh failure. Auth endpoints excluded to avoid loops.
- Passwords: bcrypt via passlib.

## 7. Anti-hallucination & prompt-injection story

- Retrieval below 0.3 score → LLM never called; canned refusal.
- System prompt: answer only from context, cite [DOC:id:page], say INSUFFICIENT_CONTEXT.
- Context wrapped in --- BEGIN/END CONTEXT --- delimiters, declared DATA not instructions.
- Structured JSON output prevents the model acting on injected text.
- Citations reconstructed from real chunk IDs; if LLM omits them, built from top chunks.

## 8. Evaluation numbers (gpt-4o-mini, 25 test docs)

- Classification 96% | Field accuracy 82% | Grounding 89.5% | $0.025/doc | ~2.2s/doc
- Weak fields: total_packages 68%, seller.name 76%, port_of_loading 78%
- Cost split: extraction ~60%, classification ~20%, grounding ~15%, validation ~5%

### Failure stories (use these)
1. Container number vs IMO number confusion → fixed with format-hinted re-prompt
   (ISO 6346 pattern: 4 letters + 7 digits + check digit).
2. Multi-currency invoices broke the sum validator → per-line-item currency field;
   skip check (warn) when currencies differ.
3. Rotated scans degraded OCR/grounding → preprocessing/quality gating.

## 9. Likely Q&A

- **Why LangGraph but a sequential runner?** Typed state + visualizable nodes; Celery is
  sync so the loop is plain Python over the same state contract. Nodes are pure
  functions → unit-testable.
- **How do you prevent hallucinated values?** Schema-constrained output + grounding
  (confidence halved if not locatable) + deterministic validation + PO cross-check.
- **Prompt injection?** Delimiters, data-not-instructions, JSON schema, chat only
  retrieves/cites, never acts.
- **Scaling?** More workers; S3 + presigned URLs (files currently on volume); Redis
  pub/sub push instead of 2s polling; parameterize the vector literal in retriever SQL;
  index-based refresh-token lookup.
- **What next?** SSE, Postgres RLS, fine-tune small model on reviewer corrections,
  webhooks, per-tenant (not just per-IP) rate limits.

## 10. Weak spots to own proactively

- Refresh-token lookup is O(n) bcrypt compares (documented; fix: prefix index).
- `_sql_field_lookup` loads all fields and matches in Python (fine at demo scale).
- Vector literal string-interpolated into SQL — parameterize before production.
- Local file storage, single rules file, SSE via DB polling.

## 11. Two-minute demo script

1. Log in as admin@apexfreight.com
2. Upload a sample PDF from data/synthetic/
3. Watch queued → processing → needs_review
4. Open document: extracted fields right, PDF left, click a field → highlight jumps
5. Show validation results (green/yellow/red) and routing decision + fired rule
6. Correct a field value, approve
7. Ask Copilot: "What is the incoterm on this document?"
8. Log out, log in as admin@blueocean.com — tenant isolation
