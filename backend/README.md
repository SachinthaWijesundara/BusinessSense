# BusinessSense API (FastAPI on Render)

The backend owns all business logic and every secret. The frontend only sends it GET and POST
requests; only the backend talks to OpenAI and to Supabase (Postgres, pgvector, Storage).

## Folders and files

```
main.py                         Settings (from env), the shared services bundle, the FastAPI app, CORS, /health
requirements.txt                pinned Python packages (Render installs these)
pytest.ini                      test settings
.env.example                    every setting the API reads, with comments

api/                            HTTP routes: read the request, call a service, return JSON
  invoices.py                   Invoice Manager: upload, type in, get, search, fix, confirm product,
                                verify, reject, mark paid, audit log; product catalogue
  dashboard.py                  GET /dashboard (review queue, purchases, supplier spend, payments due)
  suppliers.py                  suppliers, credit days, Supplier Profile figures

agentic/                        the AI agent pipeline (OpenAI Agents SDK)
  pipeline.py                   invoice_ingest: the fixed order photo -> agent -> audit -> save -> match
  vision_agent.py               image preparation (CLAHE, rotation, crops) + InvoiceVisionAgent
  guardrails.py                 output guardrail: no line items or no date -> "retake the photo"
  schemas.py                    the form InvoiceVisionAgent fills in (strict Pydantic schema)
  sku_match_agent.py            SkuMatchAgent + the 0.82 similarity gate, checked in code
  tools.py                      find_sku_candidates, the agent's catalogue search tool

services/                       business rules in plain Python (no LLM)
  invoice_service.py            math_audit (+/- Rs 1), date checks, due dates, the owner's invoice
                                workflow (type in, fix, confirm product, verify, reject, mark paid)
  cash_flow_service.py          payables: outstanding balance, payments due, supplier totals
  purchase_analysis_service.py  monthly purchases, supplier spend, a supplier's verified item lines
  alias_service.py              product catalogue search (exact name, then embeddings in pgvector)

database/
  client.py                     every read and write to Supabase Postgres, and the private photo bucket

tests/                          pytest suite (see "Tests")
  conftest.py                   test database, fakes for OpenAI/Storage, fictional invoices, a running API
  test_rules.py                 math audit, dates, cash flow, purchase analysis, dashboard
  test_agents.py                image preparation, InvoiceVisionAgent, SkuMatchAgent
  test_database.py              database/client.py and the ../database/*.sql files
  test_api.py                   every endpoint end to end
  pglite/                       a throwaway in-memory Postgres + pgvector for the tests
```

The tables are created by the SQL files in the repo-root `database/` folder (apply 01, 02, 03 in
order in the Supabase SQL editor).

## How an uploaded photo is handled

1. `POST /invoices/upload` (api/invoices.py) checks the file and calls `invoice_ingest`.
2. `vision_agent.py` cleans the photo and InvoiceVisionAgent (`gpt-4o`) fills in `ExtractedDocument`.
3. The output guardrail refuses a read with no line items or no date: 422, nothing saved.
4. `math_audit` checks qty x rate - discount = line total and lines - special discount + tax = total.
5. The draft is saved (status `draft`), with the photo in the private bucket and the raw agent output.
6. If the math passed, SkuMatchAgent (`gpt-4o-mini`) searches the catalogue for each line; a line is
   linked only when the tool's similarity is at least 0.82. The rest wait for the owner.
7. The owner fixes values (`/fix`), confirms products (`/confirm-sku`) and verifies (`/verify`).
   Verify is refused (409, with reasons) until supplier, number, date, math, dates and every product
   link are done; it then sets the due date from the supplier's credit days.

## Endpoints (GET and POST only)

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | is the API up, what is configured |
| GET | `/dashboard` | review queue, monthly purchases, supplier spend, payments due |
| GET | `/invoices` | search: `supplier_id`, `status`, `payment_status`, `invoice_number`, `date_from`, `date_to`, `limit` |
| POST | `/invoices/upload` | photo (+ optional `supplier_id`) -> audited, matched draft |
| POST | `/invoices` | an invoice typed in by hand -> audited, matched draft |
| GET | `/invoices/{id}` | invoice, lines, product matches, photo link |
| GET | `/invoices/{id}/events` | audit log |
| POST | `/invoices/{id}/fix` | owner corrections |
| POST | `/invoices/{id}/confirm-sku` | `{line_id, product_id}` or `{line_id, new_product}` |
| POST | `/invoices/{id}/verify` | mark verified |
| POST | `/invoices/{id}/reject` | `{reason}`: exclude a draft (kept and logged) |
| POST | `/invoices/{id}/payment` | `{payment_status: "paid" or "unpaid"}` (verified invoices only) |
| GET / POST | `/products` | master catalogue |
| GET / POST | `/suppliers` | suppliers |
| GET / POST | `/suppliers/{id}` | one supplier / change fields such as `credit_days` |
| GET | `/suppliers/{id}/profile` | totals, invoices and verified item lines |

Errors come back as `{"error_type": ..., "message": ...}` with the HTTP status. Interactive docs
are at `/docs` while the API runs.

## Run locally (port 8765, which the frontend expects)

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt pytest==9.2.0
copy .env.example .env
.venv\Scripts\uvicorn main:app --reload --port 8765
```

`DATABASE_URL` is required. Without an OpenAI key the API still starts; photo reading then fails
with "OPENAI_API_KEY is not set" and product matching waits for the owner.

## Deploy on Render

`../render.yaml` defines the service. In the Render dashboard set `OPENAI_API_KEY`, `DATABASE_URL`
(the Supabase transaction-pooler string), `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` and
`ALLOWED_ORIGINS` (the Vercel address). Apply `../database/*.sql` to Supabase first.

## Tests

```bash
cd tests\pglite && npm install && cd ..\..
.venv\Scripts\python -m pytest
```

* The agents run through the real Agents SDK `Runner` with the SDK's `ScriptedModel`, so the schema,
  the guardrail, tool calls and the 0.82 gate are tested without calling OpenAI.
* Everything that touches the database runs on a real Postgres with pgvector (`tests/pglite`,
  started by the tests). `TEST_DATABASE_URL` can point at a disposable Postgres instead; it is
  wiped, so it also needs `TEST_DATABASE_RESET_OK=1`. Never point it at Supabase.
* `RUN_LIVE_TESTS=1` with `OPENAI_API_KEY` also runs two small live OpenAI calls.

Test data is fictional. Never commit the shop's real invoices, supplier names or photos: the
owner's permission letter forbids publishing them, and this repository is public.

## Not built yet

Owner login (every request uses `DEFAULT_BUSINESS_ID`; see `AppServices.business_id_for` in
main.py), receipts and supplier-payment records, expenses, the Tesseract OCR baseline, Model 1
(item price), Model 2 (spend forecast) and the cash planner projection.
