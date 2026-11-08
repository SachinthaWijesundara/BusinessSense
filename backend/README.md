# BusinessSense API (FastAPI)

Owns all business logic and secrets: the invoice API, the agent pipeline (OpenAI Agents SDK),
the deterministic rules, and access to Supabase (Postgres, pgvector, Storage).

## Layout (proposal Figure 6)

```
main.py                     app, CORS, routers, /health
config.py                   settings from environment variables
api/                        HTTP routes (thin): invoices.py, catalogue.py, extract.py (legacy)
agentic/
  pipeline.py               invoice_ingest: code-controlled order, no LLM handoffs
  preprocess.py regions.py  CLAHE clean-up + four zoomed crops (EXIF-rotated, capped at 2048 px)
  vision_agent.py           InvoiceVisionAgent: Agent(output_type=ExtractedDocument) + output guardrail
  guardrails.py             "no line items / no date" tripwire; missing-field list
  sku_match_agent.py        SkuMatchAgent + the 0.82 similarity gate in code
  tools.py                  find_sku_candidates function tool (business_id from the request context)
  schemas.py                Pydantic output schema (strict JSON schema for structured outputs)
services/
  invoice_service.py        math_audit (Decimal, +/- Rs 1), date checks, due dates
  invoice_workflow.py       owner steps: fix (PendingFix), confirm-sku (HumanConfirm), verify
  alias_service.py          catalogue search: exact alias, then pgvector cosine similarity
  embeddings.py             text-embedding-3-small (OpenAI) or an offline test embedder
database/
  postgres.py               Supabase Postgres (psycopg 3); every write is one transaction
  repository.py             the data contract + in-memory implementation for tests
  storage.py                Supabase Storage: private bucket, signed URLs
tests/                      pytest suite (see below)
```

## The pipeline

`POST /invoices/upload` runs, in this fixed order:

1. CLAHE + crops, then **InvoiceVisionAgent** (`gpt-4o`) returns `ExtractedDocument`, validated by
   the SDK against the strict schema.
2. **Output guardrail**: no line items or no date: 422 "retake the photo", nothing saved.
3. **math_audit** (plain Python): qty x rate - discount = line total; lines - special discount +
   tax = total, within Rs 1. Failing invoices are saved as drafts with `math_ok = false` and are
   not matched until fixed.
4. Save the draft invoice; store the original photo and CLAHE copy in the private bucket; log the
   raw agent output (`extraction_runs`).
5. **SkuMatchAgent** (`gpt-4o-mini`) calls `find_sku_candidates` for each line; code links a line
   only when the chosen product's tool similarity is >= 0.82 (exact confirmed aliases link
   directly). Everything else waits for the owner.

Owner steps: `POST /invoices/{id}/fix` re-runs the audit (and matching once it passes),
`POST /invoices/{id}/confirm-sku` links a line and remembers the printed text as an alias,
`POST /invoices/{id}/verify` is refused (409, with reasons) until supplier, number, date, math,
dates and every product link are done, and then sets `due_date` from the supplier's credit days.
Every correction, confirmation and verification is in `GET /invoices/{id}/events`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness + which repository/storage is active |
| POST | `/invoices/upload` | photo (+ optional `supplier_id`) -> audited, matched draft |
| GET | `/invoices` | search: `supplier_id`, `status`, `invoice_number`, `date_from`, `date_to` |
| GET | `/invoices/{id}` | invoice, lines, matches, signed photo URL |
| GET | `/invoices/{id}/events` | audit log |
| POST | `/invoices/{id}/fix` | owner corrections |
| POST | `/invoices/{id}/confirm-sku` | `{line_id, product_id}` or `{line_id, new_product}` |
| POST | `/invoices/{id}/verify` | mark verified |
| GET/POST | `/suppliers`, GET/PATCH `/suppliers/{id}` | suppliers, credit days |
| GET/POST | `/products` | master catalogue |
| POST | `/extract` | legacy: read a photo without saving (used by the current frontend) |

Interactive docs at `/docs` when the server runs.

## Run locally

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements-dev.txt
copy .env.example .env
.venv\Scripts\uvicorn main:app --reload --port 8765
```

Without `DATABASE_URL` the API uses an in-memory store (nothing is saved) and, without an OpenAI
key, an offline embedder; agent calls then fail with a clear "OPENAI_API_KEY is not set" error.

## Deploy (Render)

`render.yaml` at the repo root defines the service (root dir `backend`, start command
`uvicorn main:app --host 0.0.0.0 --port $PORT`, health check `/health`). In the Render dashboard set
`OPENAI_API_KEY`, `DATABASE_URL` (Supabase transaction pooler string), `SUPABASE_URL`,
`SUPABASE_SERVICE_ROLE_KEY` and `ALLOWED_ORIGINS` (the Vercel URL). Apply the migrations in
`../supabase/migrations` to the Supabase project first.

## Tests

```bash
.venv\Scripts\python -m pytest
```

* Agents run through the real Agents SDK `Runner` with the SDK's `ScriptedModel`, so schemas,
  the guardrail, tool calls and the 0.82 gate are exercised without calling OpenAI.
* Repository and migration tests run on real Postgres + pgvector: run `npm install` once in
  `tests/pglite` (an in-memory Postgres compiled to WebAssembly) and the tests start it
  themselves. Otherwise they are skipped. `TEST_DATABASE_URL` can point at a disposable Postgres
  instead (it is wiped; set `TEST_DATABASE_RESET_OK=1`). Never point it at Supabase.
* `RUN_LIVE_TESTS=1` plus `OPENAI_API_KEY` also runs two small live calls (`-m live`).

Test data is fictional. Never commit the shop's real invoices, supplier names or photos: the
owner's permission letter forbids publishing them.

## Not done yet

* Owner login: `get_business_id` (api/deps.py) returns `DEFAULT_BUSINESS_ID`; resolve it from the
  Supabase Auth token when login is added, then apply the RLS migration.
* The frontend still writes to Supabase directly and calls `/extract`; move it to these endpoints.
* Receipts / supplier payments endpoints, the Tesseract OCR baseline, Model 1, Model 2 and the
  cash planner.
