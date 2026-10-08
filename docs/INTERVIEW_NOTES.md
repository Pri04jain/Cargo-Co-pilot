# Cargo Copilot — Interview Notes

## One-minute project pitch

Cargo Copilot is an agentic document-processing platform for logistics teams.
It ingests shipping documents — Bills of Lading, Commercial Invoices, Packing Lists —
extracts structured data using an LLM, shows reviewers exactly where on the page each
value came from, validates the extraction against business rules and Purchase Orders,
and routes each document to auto-approve, manager review, or exception handling.
A chat interface (Ask Copilot) lets anyone query processed documents in plain English.
I built it as a portfolio project to demonstrate full-stack AI engineering: async Python
APIs, a LangGraph pipeline, structured LLM output with evidence grounding, a React UI
with PDF overlays, pgvector RAG, and a real evaluation harness that produces measurable
accuracy numbers.

---

## Main trade-offs and design decisions

### 1. LangGraph over a plain function pipeline
**Why**: LangGraph gives each node a typed state object, conditional edges (for the retry
loop), and makes the pipeline easy to visualise and test node-by-node. The trade-off is
added dependency weight and a learning curve. For a simpler project, a plain chain of
async functions would be fine.

### 2. Evidence grounding via snippet matching (not coordinate extraction)
**Why**: Asking the LLM to return a supporting text snippet per field is more reliable
than asking it to return coordinates — LLMs understand text, not pixel positions. We then
match the snippet to the parsed word bounding boxes ourselves. The trade-off: if the LLM
slightly paraphrases the snippet, matching fails and we mark the field ungrounded.
A fuzzy match (edit distance) helps but adds latency.

### 3. Deterministic validators, not LLM-based
**Why**: Math checks (do line items sum to total?), format checks (ISO 6346 container
number), and cross-document checks (invoice vs PO) are fully deterministic. Doing them
in code is faster, cheaper, more reliable, and fully explainable. The LLM's job is
extraction only — it never makes business decisions.

### 4. YAML rules engine over hardcoded routing
**Why**: Business rules change constantly in logistics. Storing them in YAML means a
business analyst can update them without a code deployment. The engine is a small
expression evaluator — we avoid `eval()` on raw strings for security.

### 5. Celery + Redis over serverless functions
**Why**: PDF parsing and OCR are CPU-intensive and can take 10–30 seconds. A background
worker with a message queue keeps the HTTP response fast and provides retry semantics.
Serverless functions (Lambda) could work but cold starts and timeout limits make them
awkward for long-running CPU tasks.

### 6. Simple JWT auth, no refresh tokens
**Why**: Scope. Adding refresh token rotation is ~200 more lines of non-portfolio code.
The auth pattern (JWT, bcrypt, tenant_id in every query) is the same; only the token
lifecycle differs. I note this as a known limitation.

### 7. Hybrid retrieval for Ask Copilot (SQL + vector)
**Why**: Structured questions ("What's the total on invoice 1042?") are better answered
by a SQL query on extracted fields than by vector search. Unstructured questions
("Are there any shipments with weight mismatches?") benefit from vector retrieval.
Combining both gives better coverage than either alone.

---

## Three failure cases found during evaluation

### Failure 1: Container number misidentified as vessel IMO number
**What happened**: On some Bills of Lading, the container number appears directly below
the vessel IMO number with no label. The LLM returned the IMO number for `container_numbers`.
**How fixed**: Added a format-based re-prompt: "Container numbers match the pattern
[4 letters][7 digits][1 check digit], e.g. MSCU1234567. Vessel IMO numbers are 7 digits
prefixed with 'IMO'. Re-extract only `container_numbers`."

### Failure 2: Invoice totals failing math check on multi-currency documents
**What happened**: Some invoices list line items in USD and a total in EUR (converted).
The line-item sum validator compared USD line items to EUR total and always flagged an error.
**How fixed**: Added a currency-per-line-item field to the extraction schema and made
the math validator skip the check when line-item and total currencies differ (logging a
warning instead).

### Failure 3: Grounding failure on rotated scanned documents
**What happened**: Scanned PDFs with 2–3° rotation caused Tesseract to insert incorrect
line breaks, making snippet matching fail on multi-word values (e.g. "SHANGHAI PORT OF
LOADING" split across lines).
**How fixed**: Added a pre-processing step that detects rotation (via Tesseract's OSD mode)
and deskews the image before OCR. Also loosened the snippet matcher to normalise whitespace
and try a sliding window on adjacent words.

---

## 20 likely interview questions with short answers

**Architecture**

1. *Walk me through what happens when a user uploads a document.*
   → HTTP POST → file saved → Document row created with status=queued → Celery task enqueued →
   LangGraph pipeline runs async: parse → classify → extract → ground → validate →
   [retry if needed] → route → finalize → status updated. Client polls `/status`.

2. *Why use LangGraph instead of plain async Python?*
   → Typed state object, conditional edges (retry loop), visualisable graph, node-level
   observability hooks. Overhead is minimal; benefit is structure as complexity grows.

3. *How do you ensure tenant isolation?*
   → `tenant_id` on every table, every DB query filters by it, JWT encodes tenant_id,
   files are stored in `/uploads/{tenant_id}/`. No RLS for simplicity but the pattern is identical.

4. *What happens if the Celery worker crashes mid-task?*
   → `task_acks_late=True` means the message isn't acknowledged until the task completes.
   Redis re-delivers it to another worker. `task_reject_on_worker_lost=True` puts it back
   in the queue. Document status stays `processing` until re-picked up; a monitoring alert
   would fire on documents stuck in `processing` > N minutes.

**LLM / Extraction**

5. *How do you prevent prompt injection from document text?*
   → Document text is injected between explicit `--- BEGIN DOCUMENT ---` / `--- END DOCUMENT ---`
   delimiters and described as "data, not instructions." Structured output (JSON schema /
   tool calls) means the LLM is constrained to a schema and can't act on injected instructions.
   Ask Copilot only retrieves and cites; it never executes retrieved content.

6. *What is evidence grounding and why does it matter?*
   → Each extracted field carries the exact text snippet the LLM cited as its source.
   We match that snippet to parsed word bounding boxes to produce a page number + bbox.
   The UI highlights it so a reviewer can verify instantly without reading the whole page.
   It also makes confidence meaningful: an ungrounded field is automatically less trusted.

7. *How do you handle fields the LLM gets wrong?*
   → Deterministic validators catch format/math errors. If errors remain, a retry prompt
   re-extracts only the failing fields with the failure reason included (max 2 retries).
   If they still fail, the document goes to exception_queue and a human corrects them.
   Corrections are stored as ground truth for the eval set.

8. *How do you measure extraction accuracy?*
   → The eval harness (`make eval`) runs the pipeline on a labeled test set (not used during
   prompt tuning). It reports exact-match and normalised-match rates per field, per document
   type, and overall. It also reports grounding rate, routing accuracy, latency, and cost per
   document. Numbers come from real runs — nothing is hardcoded.

9. *Why structured output (JSON schema / tool calls) instead of free-text LLM responses?*
   → Structured output guarantees the response matches the Pydantic schema — no regex parsing,
   no hallucinated keys, no missed fields. Pydantic validates the result immediately after
   the LLM call. If validation fails, the retry prompt is more focused.

10. *How do you keep LLM costs low?*
    → Use a small model (gpt-4o-mini) first; escalate to gpt-4o only on retry. Extract
    per-document-type schemas so prompts are as short as possible. Cache classification results.
    Track token counts and cost per document in the `llm_calls` table and show them in the UI
    so cost anomalies are visible.

**Validation & Routing**

11. *How does the YAML rules engine work?*
    → On startup, load and validate the YAML. At runtime, build an evaluation context dict
    from extraction/validation results and evaluate each rule's `when` expression using a
    safe parser (no `eval()` on raw strings). Rules are checked in priority order; first
    match wins. The fired rule ID and context snapshot are stored for audit.

12. *What's a safe expression evaluator and why not just use eval()?*
    → `eval()` on user-provided strings can execute arbitrary Python — a security risk since
    YAML files could be edited by non-engineers. A safe evaluator parses the expression into
    an AST and only allows a whitelist of operations (comparisons, arithmetic, named functions
    like `pct_diff`). Libraries like `simpleeval` or a hand-rolled parser work well.

13. *How do cross-document checks work?*
    → After extraction, we look up the PO by `po_reference` from the invoice. We compare
    supplier name (fuzzy), total amount (with tolerance), currency, and consignee against
    the Bill of Lading. Each comparison returns pass/fail/warning with a human-readable message.

**Frontend**

14. *How does evidence highlighting work in the UI?*
    → The PDF is rendered with `react-pdf`. Each extracted field carries `{page, bbox}`.
    When the user clicks a field, we draw a semi-transparent coloured rectangle on an
    SVG overlay layer positioned absolutely over the PDF canvas, scaled to match the
    rendered page dimensions.

15. *How does the UI know when processing is done?*
    → After upload, the frontend polls `GET /documents/{id}/status` every 3 seconds using
    React Query's `refetchInterval`. When status leaves `processing`, polling stops and
    the full document detail is fetched. Server-Sent Events would be more efficient for
    Phase 7.

**Data & Storage**

16. *Why PostgreSQL with pgvector instead of a dedicated vector store?*
    → We already have Postgres for structured data. pgvector adds vector similarity search
    in the same database, so Ask Copilot can JOIN structured fields with vector search
    results in one query. Fewer moving parts. At large scale (millions of documents) a
    dedicated vector store (Pinecone, Qdrant) would be faster.

17. *What's in the audit log and why is it append-only?*
    → Every state change, LLM call reference, routing decision, and human correction.
    Append-only means you can reconstruct the full history of a document and detect
    tampering. It's also useful for debugging — you can replay the pipeline decisions.

**Deployment & Operations**

18. *How would you deploy this cheaply for a demo?*
    → Render.com: web service (API) + worker service from the same Dockerfile, Postgres
    managed add-on, Redis managed add-on. Free/Starter tier covers demo traffic. One
    `render.yaml` file defines all services.

19. *What would you do differently with more time?*
    → (1) Server-Sent Events instead of polling. (2) Row-Level Security in Postgres for
    stronger tenant isolation. (3) Async Celery (using `asyncio` loop in worker) to avoid
    maintaining two DB sessions. (4) A proper test database fixture in pytest rather than
    hitting the live DB. (5) Fine-tune a small model on our labeled eval set.

20. *What's the biggest risk in this system and how would you mitigate it?*
    → LLM non-determinism: the same document can produce different extractions on different
    runs, making bugs hard to reproduce. Mitigation: set `temperature=0`, store every raw
    LLM response in the DB, version prompts, and run the eval harness on every prompt change
    before deploying. The retry loop handles transient failures; the human review queue
    handles systematic ones.
