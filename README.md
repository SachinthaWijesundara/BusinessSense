# BusinessSense

**Invoice management and purchasing intelligence for SMEs and medium-scale shops.**
ModelX 2.0 · Team DeepForge.

BusinessSense turns a shop's paper supplier invoices into a checked, searchable purchasing history,
and then looks ahead: what a product should cost on the next invoice, how much the shop is likely
to spend, and how much cash to set aside for supplier bills. A participating hardware shop is the
worked example that proves the idea on a real invoice archive.

The system supports the owner's decisions; it never makes them. Every invoice read by the AI is a
draft until the owner verifies it, every payment is unconfirmed until the owner confirms it, and a
flagged price is a reminder to check the invoice, never a claim that a supplier overcharged.

---

## Contents

1. [What it does](#what-it-does)
2. [How it works](#how-it-works)
3. [The screens](#the-screens)
4. [The AI agent pipeline](#the-ai-agent-pipeline)
5. [Business rules](#business-rules)
6. [Predictive models](#predictive-models)
7. [Tech stack](#tech-stack)
8. [Project structure](#project-structure)
9. [Database](#database)
10. [API](#api)
11. [Run it locally](#run-it-locally)
12. [Train the models](#train-the-models)
13. [Tests and CI](#tests-and-ci)
14. [Deploy](#deploy)
15. [Settings reference](#settings-reference)
16. [Running a shop](#running-a-shop)
17. [Status](#status)
18. [Privacy](#privacy)

---

## What it does

| Problem in a shop with paper invoices | What BusinessSense gives the owner |
|---|---|
| Old invoices are hard to find | A searchable invoice register (supplier, date, number, status, payment) with the photo beside the values |
| Typing invoices takes time and mistakes slip through | Photos are read by an AI agent; every line and total is checked by a math audit before anything counts |
| The same item is printed many different ways | Each printed line is linked to one catalogue product, confirmed by the owner |
| No easy way to compare prices over time | Each product's price history, with an expected price range and flags for unusual prices (Model 1) |
| Supplier payments are missed or forgotten | Due dates from each supplier's credit days, payments recorded from receipts, reminders for unconfirmed payments |
| Unclear how much cash purchasing will need | A purchasing spend forecast (Model 2) and a dated cash planner with the cash to set aside |

---

## How it works

Three parts, each with one job (as in the proposal):

| Part | What it is | Runs on |
|---|---|---|
| [`frontend/`](frontend/README.md) | Next.js owner app: login and the five screens. It only sends HTTPS GET/POST requests to the backend; it holds no keys and does no business logic. | Vercel |
| [`backend/`](backend/README.md) | FastAPI: the invoice, supplier, payment and analytics API, the OpenAI Agents SDK pipeline, the business rules, the predictive models, owner login, and every read and write to Supabase. Holds all secrets. | Render |
| [`database/`](database/) | The Supabase PostgreSQL schema, storage buckets and row-level security, as three SQL files applied in order. | Supabase |

```
 Owner's browser                Backend (FastAPI on Render)                     Supabase (one project per shop)
 ───────────────                ───────────────────────────                     ───────────────────────────────
 Next.js app on Vercel  ─HTTPS─▶ login check (Supabase Auth token)  ──SQL──────▶ Postgres + pgvector (all tables)
   login page                    agent pipeline ──HTTPS──▶ OpenAI   ──HTTP──────▶ Storage: invoice-images, models
   5 screens                     business rules                     ──HTTP──────▶ Auth: owner logins (hashed passwords)
                                 Model 1 + Model 2 (loaded from Storage)
```

**Three layers, never blurred:**

1. **Agent pipeline:** reads invoices and matches products, with human confirmation.
2. **Deterministic rules:** math audit, due dates, balances, the purchasing cash requirement. Plain
   Python that can be checked by hand.
3. **Predictive models:** Model 1 (item price) and Model 2 (purchasing spend). They run only on
   verified, product-linked history.

Agents do not forecast prices or cash. Models do not replace the math audit or the cash planner rules.

**Login.** The browser never talks to Supabase. The owner's email and password go to the backend
(`POST /auth/login`), which checks them with Supabase Auth (passwords are stored hashed by Supabase)
and returns a short-lived access token. Every other request carries that token; the backend checks
it and finds the shop it belongs to (`business_users`), so a login only ever sees its own shop's
data. There is no sign-up: the team creates each owner's login.

**One deployment per shop.** Each shop gets its own Supabase project (its own database, storage and
logins), with the same frontend and backend code pointed at it. The team enters the shop's invoice
history and trains its models; the owner then uses the app day to day.

---

## The screens

| Screen | What the owner sees | What the owner does |
|---|---|---|
| **Login** | Email and password (no sign-up) | Logs in to their shop |
| **Dashboard** | Invoices waiting for review, verified invoices, supplier payments due in 30 days, payments waiting for confirmation (with a reminder after two days), cash going out by day, purchases vs paid by month, spend by supplier and by item, and the next-30-day purchasing forecast beside its baseline | Opens whatever needs attention first |
| **Invoice Manager** | The invoice register, 100 per page, with filters; upload a photo (invoice or receipt) or type an invoice in; the review screen shows the photo, the math audit, low-confidence values, "Is this the same as [product]?" with candidates, and a price check on each line | Corrects values, confirms or unlinks products, creates products, verifies or rejects, marks paid |
| **Supplier Profile** | Each supplier's details and usual credit days, totals (outstanding, paid, waiting), invoices, items bought, payments, and a supplier dashboard with charts | Records credit days, records payments or uploads receipts, confirms / corrects / rejects payments |
| **Purchase Analytics** | Each product's price history with Model 1's expected range (chart), prices to check, the expected next price, and spend by month and supplier | Checks a flagged price against the invoice and, if needed, the supplier |
| **Purchasing Cash Planner** | For 14 / 30 / 60 / 90 days: supplier bills falling due by date, Model 2's expected purchases beside the baseline, and the recommended purchasing cash reserve, with unconfirmed payments shown as an assumed scenario | Sets cash aside for coming bills |

---

## The AI agent pipeline

`backend/agentic/`, built with the OpenAI Agents SDK. The order is fixed in code (no LLM hand-offs),
so it is easy to test and debug.

```
photo ─▶ image clean-up ─▶ InvoiceVisionAgent (+ output guardrail)
           (CLAHE, rotation,       │
            zoomed crops)          ├─ invoice ─▶ math audit (Python) ─▶ save draft ─▶ SkuMatchAgent (0.82 gate) ─▶ owner review
                                   └─ receipt ─▶ one unconfirmed supplier payment per invoice it settles ─▶ owner confirms
```

* **InvoiceVisionAgent** reads one photo into a strict Pydantic schema: supplier, invoice number,
  dates, lines (code, description, quantity, rate, discount, line total), special discount, tax and
  total, or for a receipt: receipt number, date, cash/cheque, the invoices it settles, cheque details.
  It only reads; it never judges prices or decides anything.
* **Output guardrail:** an invoice with no line items or no date (or an empty receipt) is refused,
  nothing is saved, and the owner is asked to retake the photo or type it in.
* **Math audit:** quantity × rate − discount = line total, and lines − special discount + tax = total,
  within Rs 1 (exact decimal arithmetic). A failing invoice is saved as a draft with the problems
  highlighted and is not matched until the owner fixes it.
* **SkuMatchAgent** links each printed line to a catalogue product using the `find_sku_candidates`
  tool (exact names first, then text-embedding similarity in pgvector). Only matches at or above the
  **0.82** similarity gate (checked in code) are linked automatically; the rest go to the owner as
  "Is this the same as …?". Every link the owner confirms is remembered, and a wrong link can be
  **unlinked**, which takes the line out of that product's history until it is linked again.
* Every raw agent output is kept (`extraction_runs`) and every correction, confirmation, unlink,
  verification and payment is written to the audit log (`audit_log`).

---

## Business rules

Plain Python in `backend/services/`, no LLM and no model:

* **Verification:** an invoice counts only once it is verified, and it can be verified only when the
  supplier, number and date are set, the math audit passes, the dates are plausible and every line is
  linked to a product.
* **Due date:** invoice date + the supplier's credit days (cash suppliers: the invoice date).
* **Payments:** typed in or read from a receipt photo; each stays *unconfirmed* until the owner
  confirms it. Confirming is refused (with reasons) until the supplier, amount and date are set, the
  invoice is verified and the payment is not more than is still owed. An invoice is *paid* once
  confirmed payments cover its total; *still owed* = total − confirmed payments.
* **Reminders:** a payment unconfirmed for more than two days is listed on the Dashboard; it still
  stays unconfirmed until the owner answers.
* **Purchasing cash requirement** (Cash Planner): bills still owed that fall due in the period
  (overdue ones included) + the part of the expected new purchases that falls due inside it, by the
  suppliers' credit days = the recommended cash reserve. Opening cash, inflows and other expenses are
  not entered, so it is the purchasing requirement only.

---

## Predictive models

`backend/ml/`. Both models learn only from **verified** invoices, are tested in time order (each
period predicted only from earlier ones), and a learner is used **only if it beats the simple
baselines**; otherwise the better baseline is shown, labelled as a baseline.

| | Model 1: Item Price Intelligence | Model 2: Purchasing Expenditure Forecast |
|---|---|---|
| Question | What should this product cost on the next invoice, and is a new price unusual? | How much will the shop spend on purchases per month? |
| Data | Verified, product-linked invoice lines; unit price = line total ÷ quantity | Verified invoice totals per month |
| Cleaning | Drops lines with no date, quantity or amount, future dates and duplicates; prices far from their neighbours are left out of training | Drops drafts, rejected, undated and zero invoices; empty months count as 0 |
| Baselines | Latest price; median of the last 5 prices | Same month last year (seasonal naive); mean of the last 3 months |
| Learners (scikit-learn) | Ridge regression; gradient boosting (predicting the change from the last price from 9 features) | Exponential smoothing (Holt-Winters, damped trend); ridge regression on lagged spend |
| Fair test | Rolling origin, last 12 months; MAE and mean % error | Rolling origin, last 12 months; MAE and weighted % error |
| Range | 2.5–97.5% spread of past errors; a price outside it is flagged | 10–90% spread of past errors |
| Thin history | Fewer than 3 earlier purchases: median of earlier prices, labelled baseline | Too few months: mean of the last 3 months, labelled baseline |
| Shown on | Purchase Analytics, invoice review price check | Cash Planner, Dashboard forecast |

Both are trained per shop on the team's laptop (`python -m ml.train`, see
[Train the models](#train-the-models)), saved as joblib files in the shop's **private `models`
Storage bucket**, and loaded by the backend on first use. Their predictions are kept in the
`model_predictions` table so they can later be compared with what actually happened. The training
command prints the test results for the evaluation report.

---

## Tech stack

| Part | Technology |
|---|---|
| Frontend | Next.js 16 (App Router), React 19, TypeScript, Tailwind CSS 4; charts drawn as plain SVG |
| Backend | Python 3.13, FastAPI, Uvicorn, Pydantic 2 |
| AI agents | OpenAI Agents SDK 0.24 (gpt-4o-mini today, gpt-4o per the proposal), text-embedding-3-small, OpenCV for image clean-up |
| Models | scikit-learn 1.9, NumPy, joblib |
| Database | Supabase: PostgreSQL + pgvector, Storage, Auth; psycopg 3 with a connection pool |
| Tests | pytest with scripted agent models and a throwaway Postgres + pgvector (PGlite, via Node) |
| CI | GitHub Actions (backend tests, frontend lint + build) |
| Hosting | Vercel (frontend), Render (backend), Supabase (data) |

---

## Project structure

```
BusinessSense/
├── README.md                     this file
├── AGENTS.md                     notes for AI coding assistants (the three layers, privacy)
├── render.yaml                   Render Blueprint for the backend (build, start, settings to fill in)
├── .gitignore                    keeps secrets, build output and trained models out of Git
├── .github/workflows/ci.yml      CI: backend tests and frontend lint + build on every push
│
├── database/                     Supabase schema, applied in order in the SQL editor
│   ├── 01_schema.sql             all tables, pgvector, the alias-search function; safe to re-run and
│   │                             to apply on a project that already has the data-entry app's tables
│   ├── 02_storage.sql            private Storage buckets: invoice-images and models
│   └── 03_row_level_security.sql row-level security: a logged-in owner reads only their shop's rows
│
├── backend/                      FastAPI on Render (see backend/README.md)
│   ├── main.py                   settings (from environment), shared services, the app, CORS, /health
│   ├── requirements.txt          pinned Python packages (Render installs these)
│   ├── .env.example              every setting the backend reads, with comments
│   ├── pytest.ini                test settings
│   ├── api/                      HTTP routes: read the request, call a service, return JSON
│   │   ├── auth.py               login, refresh, logout, and the login check every data route runs
│   │   ├── invoices.py           upload (invoice or receipt), type in, search, review, fix, confirm /
│   │   │                         unlink product, verify, reject, mark paid, audit log; product catalogue
│   │   ├── suppliers.py          suppliers, credit days, Supplier Profile; supplier payments
│   │   ├── dashboard.py          GET /dashboard (incl. reminders and the 30-day forecast)
│   │   └── analytics.py          Purchase Analytics, invoice price check, cash overview, Cash Planner
│   ├── agentic/                  the AI agent pipeline (OpenAI Agents SDK)
│   │   ├── pipeline.py           the fixed order: photo -> agent -> audit -> save -> match (or receipt)
│   │   ├── vision_agent.py       image clean-up + InvoiceVisionAgent
│   │   ├── guardrails.py         output guardrail ("retake the photo") and missing-field lists
│   │   ├── schemas.py            the strict form the agent fills in (invoice or receipt)
│   │   ├── sku_match_agent.py    SkuMatchAgent and the 0.82 gate
│   │   └── tools.py              find_sku_candidates, the agent's catalogue search tool
│   ├── services/                 business rules in plain Python
│   │   ├── invoice_service.py    math audit, dates, due dates, payment status, the invoice workflow
│   │   ├── cash_flow_service.py  what is owed, payments due, supplier payments, the Cash Planner rules
│   │   ├── purchase_analysis_service.py  purchases by month/supplier, Purchase Analytics with Model 1
│   │   └── alias_service.py      product catalogue search (exact names, then embeddings)
│   ├── ml/                       the predictive models
│   │   ├── features.py           Model 1 data: cleaning and the 9 inputs
│   │   ├── train.py              trains Model 1 and Model 2 for one shop and uploads them
│   │   ├── inference.py          uses Model 1: expected price, range, the price check
│   │   └── spend.py              Model 2: monthly spend, baselines, learners, test, forecast
│   ├── database/
│   │   └── client.py             every read and write to Supabase Postgres, Storage and Auth
│   └── tests/                    pytest suite (131 tests)
│       ├── conftest.py           throwaway database, fakes for OpenAI / Storage / Auth, fictional data
│       ├── test_rules.py         math audit, dates, cash flow, analytics, dashboard
│       ├── test_agents.py        image preparation, InvoiceVisionAgent, SkuMatchAgent
│       ├── test_database.py      database client, SQL files, row-level security, Storage and Auth clients
│       ├── test_api.py           every endpoint end to end, logged in
│       ├── test_ml.py            Model 1
│       ├── test_planner.py       Model 2 and the Cash Planner rules
│       └── pglite/               a throwaway Postgres + pgvector for the tests (Node)
│
└── frontend/                     Next.js on Vercel (see frontend/README.md)
    ├── package.json              libraries and the dev / build / lint commands
    ├── .env.example              NEXT_PUBLIC_API_URL: the backend's address (no keys)
    ├── next.config.ts, tsconfig.json, eslint.config.mjs   framework, TypeScript and lint settings
    ├── public/businesssense-icon.png                      the app icon (top of the sidebar)
    ├── app/                      one folder per screen
    │   ├── layout.tsx            fonts, global styles, the sidebar shell around every screen
    │   ├── globals.css           colours, layout, cards, tables, badges, charts
    │   ├── login/page.tsx        Login
    │   ├── page.tsx              Dashboard
    │   ├── invoice-manager/      Invoice Manager: list (page.tsx), type in (new/), review ([invoiceId]/)
    │   ├── supplier-profile/     Supplier Profile
    │   ├── purchase-analytics/   Purchase Analytics
    │   └── cash-planner/         Purchasing Cash Planner
    ├── components/
    │   ├── AppShell.tsx          sidebar, login check, log out
    │   ├── DocumentUpload.tsx    photo drop zone (invoices and receipts)
    │   ├── InvoiceList.tsx       filters, invoice table and pages
    │   ├── InvoiceForm.tsx       invoice header and lines, as printed
    │   ├── InvoiceReview.tsx     review screen: photo, audit, products, price check, verify / reject / paid
    │   ├── ItemAutocomplete.tsx  product picker
    │   ├── SupplierForm.tsx      add a supplier
    │   ├── SupplierDashboard.tsx, SupplierTableSheet.tsx, SkuPurchasesTable.tsx   Supplier Profile dashboard
    │   ├── PaymentForm.tsx, PaymentList.tsx                record, check, confirm, reject payments
    │   ├── DashboardCharts.tsx   Dashboard charts
    │   ├── PriceChart.tsx        a product's price history with the expected range
    │   └── ImagePreviewModal.tsx photo preview
    └── lib/
        ├── api.ts                the ONLY file that sends requests: one function per endpoint, and the login session
        ├── types.ts              shapes of the backend's JSON
        ├── money.ts              number display and input parsing (no business maths)
        └── supplierMetrics.ts    Supplier Profile chart figures from the backend's data
```

Not in Git: `backend/.env`, `frontend/.env.local` (secrets and addresses), `backend/.venv/`,
`node_modules/`, `.next/`, and `backend/ml/output/` (trained models, which are built from a shop's
private invoices).

---

## Database

Apply the three files **in order** in the Supabase SQL editor (Supabase > SQL Editor > New query):

| File | What it creates |
|---|---|
| `01_schema.sql` | `businesses`, `business_users` (which login belongs to which shop), `suppliers`, `products` (master catalogue), `product_aliases` (printed names + pgvector embeddings), `invoices`, `invoice_items`, `supplier_payments`, `expenses`, `cash_records`, `model_predictions`, `extraction_runs`, `audit_log`, and `match_product_aliases()` for the catalogue search |
| `02_storage.sql` | Private Storage buckets: `invoice-images` (invoice and receipt photos) and `models` (trained models) |
| `03_row_level_security.sql` | Row-level security on every table: a logged-in owner may read only their shop's rows; the public key reads nothing |

Every row carries a `business_id`, and every query filters on it. The files are safe to run again,
and safe to apply on a project that already holds the team's data-entry tables (`items`, `receipts`,
`receipt_payments`, `receipt_cheques`), which are kept as they are. The backend connects as the
database owner (`DATABASE_URL`) and does its own per-shop filtering; row-level security protects the
data from anything else.

---

## API

The backend's live, interactive docs are at `/docs` (e.g. http://localhost:8765/docs). Only GET and
POST are used. Errors come back as `{"error_type": ..., "message": ...}` with the HTTP status. Every
route except `/health` and `/auth/*` needs `Authorization: Bearer <access token>`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Is the API up, and what is configured |
| POST | `/auth/login` | `{email, password}` → access + refresh token and the shop |
| POST | `/auth/refresh` | `{refresh_token}` → a renewed session |
| POST | `/auth/logout` | End the session |
| GET | `/dashboard` | Review queue, monthly purchases, supplier spend, payments due, payments to confirm, reminders, 30-day forecast |
| GET | `/invoices` | Search: `supplier_id`, `status`, `payment_status`, `invoice_number`, `date_from`, `date_to`, `limit`, `offset` (total in `X-Total-Count`) |
| POST | `/invoices/upload` | Photo → audited, matched draft invoice; a receipt photo → unconfirmed payments |
| POST | `/invoices` | Invoice typed in by hand → audited, matched draft |
| GET | `/invoices/{id}` | Invoice, lines, product matches, photo link |
| GET | `/invoices/{id}/events` | Audit log |
| POST | `/invoices/{id}/fix` | Owner corrections (re-runs the audit) |
| POST | `/invoices/{id}/confirm-sku` | Link a line to a product, or to a new one |
| POST | `/invoices/{id}/unlink-sku` | Undo a wrong product link (a verified invoice goes back to draft) |
| POST | `/invoices/{id}/verify` | Mark verified (sets the due date); 409 with reasons until ready |
| POST | `/invoices/{id}/reject` | Keep a draft out of the ledger, with a reason |
| POST | `/invoices/{id}/payment` | Mark a verified invoice paid / unpaid by hand |
| GET / POST | `/products` | Master catalogue |
| GET / POST | `/suppliers`, `/suppliers/{id}` | Suppliers; change details such as credit days |
| GET | `/suppliers/{id}/profile` | Supplier totals, invoices, item lines and payments |
| GET / POST | `/payments`, `/payments/{id}` | Supplier payments (list, record, one payment) |
| POST | `/payments/{id}/fix`, `/confirm`, `/reject` | Correct, confirm or reject a payment |
| GET | `/analytics/overview` | Products with their expected next price, prices to check, spend |
| GET | `/analytics/products/{id}` | One product's price history with the expected range |
| GET | `/analytics/invoices/{id}/price-check` | Each line's price against its expected range |
| GET | `/analytics/cash-overview` | Dashboard charts: cash going out by day, purchases vs paid, spend shares |
| GET | `/analytics/cash-plan?days=30` | Cash Planner for 14, 30, 60 or 90 days |

---

## Run it locally

### Requirements

* **Python 3.13** (Render uses 3.13.7) and **Node.js 22**
* A **Supabase** project (free tier is fine)
* An **OpenAI API key** (needed only to read photos; everything else works without it)
* Windows commands are shown; on macOS / Linux use `.venv/bin/...` and `cp` instead of `copy`.

### 1. Database (Supabase)

1. Create a Supabase project.
2. SQL Editor → run `database/01_schema.sql`, then `02_storage.sql`, then `03_row_level_security.sql`.
3. Collect for the backend:
   * `DATABASE_URL`: **Connect → Connection String → URI → Transaction pooler** (port 6543), with
     your database password filled in. If the password has characters such as `@ # / ? :`, write
     them percent-encoded (`@` → `%40`) or reset the password to letters and digits.
   * `SUPABASE_URL`: **Project Settings → Data API → Project URL**.
   * `SUPABASE_SERVICE_ROLE_KEY`: **Project Settings → API Keys** → the secret key (`sb_secret_…`).

### 2. Backend (port 8765)

```bash
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
```

Fill in `backend/.env` (see [Settings reference](#settings-reference)): at least `DATABASE_URL`,
`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` and `OPENAI_API_KEY`. Then:

```bash
.venv\Scripts\uvicorn main:app --reload --port 8765
```

Check http://localhost:8765/health and the API docs at http://localhost:8765/docs.

### 3. Frontend (port 3001)

```bash
cd frontend
npm install
copy .env.example .env.local
npm run dev
```

`frontend/.env.local` only needs `NEXT_PUBLIC_API_URL=http://localhost:8765`. Open
http://localhost:3001: the login page opens.

### 4. Create the owner's login

There is no sign-up page. In Supabase:

1. **Authentication → Users → Add user** (email + password, tick *Auto Confirm User*).
2. SQL Editor: name the shop and link the login to it.

```sql
update businesses set name = 'Your shop name';           -- 01_schema.sql created one shop
insert into business_users (business_id, user_id, role)
select b.id, u.id, 'owner'
from businesses b, auth.users u
where u.email = 'owner@example.com';
```

3. Log in at http://localhost:3001 with that email and password.

Recommended: **Authentication → Sign In / Providers → turn off "Allow new users to sign up"**, so
accounts can only be created by the team.

---

## Train the models

Run by the team, on a laptop, per shop, once the shop's invoices are entered and verified (and again
when more have been verified). From `backend/`, with `backend/.env` pointing at that shop's project:

```bash
.venv\Scripts\python -m ml.train                         # train Model 1 and Model 2, save and upload
.venv\Scripts\python -m ml.train --model item_price      # only Model 1
.venv\Scripts\python -m ml.train --model purchase_spend  # only Model 2
.venv\Scripts\python -m ml.train --no-upload             # save locally only (backend/ml/output/)
```

It prints the cleaning report and the test results (each method's error, which one was chosen and
why, how well the range held), saves `item_price.joblib` / `purchase_spend.joblib` and their reports
to `backend/ml/output/<business id>/`, and uploads them to the shop's private `models` bucket.
**Restart the backend afterwards** (on Render: Manual Deploy → Restart service) so it loads the new
models. Until a shop is trained, the screens show the baselines, labelled as such.

---

## Tests and CI

```bash
cd backend\tests\pglite
npm install
cd ..\..
.venv\Scripts\python -m pytest
```

* 131 tests: the business rules, the agents (through the real Agents SDK runner with scripted models,
  so no OpenAI calls), the database client and SQL files on a real Postgres + pgvector (PGlite, started
  by the tests), every API endpoint end to end as a logged-in owner, login, Model 1, Model 2 and the
  Cash Planner.
* No secrets are needed and the real Supabase project is never touched.
* `RUN_LIVE_TESTS=1` with `OPENAI_API_KEY` also runs two small live OpenAI calls.

Frontend checks:

```bash
cd frontend
npm run lint
npm run build
```

**CI** (`.github/workflows/ci.yml`) runs the backend tests and the frontend lint + production build on
every push and pull request, on Ubuntu 24.04 with Python 3.13.7 and Node 22. Render and Vercel deploy
by themselves from `main`.

---

## Deploy

Order: Supabase → Render (backend) → Vercel (frontend) → back to Render to allow the Vercel address.

### Supabase

Create the shop's project and apply the three SQL files, as in [Run it locally](#1-database-supabase).

### Render (backend)

**With the Blueprint (recommended):** Render → **New + → Blueprint** → connect the GitHub repository
→ branch `main`. Render reads `render.yaml` (root folder `backend`, build
`pip install -r requirements.txt`, start `uvicorn main:app --host 0.0.0.0 --port $PORT`, health check
`/health`, Python 3.13.7, models `gpt-4o-mini`) and asks for the secrets: `OPENAI_API_KEY`,
`DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `ALLOWED_ORIGINS` (and optionally
`ALLOWED_ORIGIN_REGEX`).

**Or as a Web Service:** New + → Web Service → the repository, then:

| Setting | Value |
|---|---|
| Root Directory | `backend` |
| Runtime | Python 3 |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `uvicorn main:app --host 0.0.0.0 --port $PORT` |
| Health Check Path | `/health` |
| Instance Type | Free |

and add the environment variables from the [Settings reference](#settings-reference), plus
`PYTHON_VERSION=3.13.7`.

The first build takes about 5–10 minutes. Then `https://<service>.onrender.com/health` should show
`"ok": true` and `"login_required": true`.

### Vercel (frontend)

Vercel → **Add New → Project** → import the repository:

| Setting | Value |
|---|---|
| Root Directory | `frontend` |
| Framework Preset | Next.js (automatic) |
| Environment variable | `NEXT_PUBLIC_API_URL` = the Render address, no trailing `/` |

After changing `NEXT_PUBLIC_API_URL`, redeploy (it is built into the site).

### Connect them

In Render → Environment, set `ALLOWED_ORIGINS` to the Vercel address (e.g.
`https://businesssense.vercel.app`, no trailing `/`; add `,http://localhost:3001` to keep local
testing). Without it the browser blocks requests (CORS).

### Notes

* Render's free plan sleeps after ~15 minutes idle; the first request then takes 30–60 seconds.
* Every merge to `main` redeploys both.
* After retraining the models, restart the Render service.

---

## Settings reference

**Backend** (`backend/.env` locally, Render → Environment in production; see `backend/.env.example`):

| Name | Required | Meaning |
|---|---|---|
| `DATABASE_URL` | yes | Supabase Postgres, Transaction pooler (port 6543) |
| `SUPABASE_URL` | yes | The shop's Supabase project URL (login and Storage) |
| `SUPABASE_SERVICE_ROLE_KEY` | yes | The project's secret key (server only) |
| `OPENAI_API_KEY` | for photos | OpenAI key for the agents and embeddings |
| `INVOICE_VISION_MODEL` | no | Default `gpt-4o`; `gpt-4o-mini` while the key allows only that |
| `SKU_MATCH_MODEL` | no | Default `gpt-4o-mini` |
| `EMBEDDING_MODEL` | no | Default `text-embedding-3-small` |
| `ALLOWED_ORIGINS` | yes in production | The website addresses allowed to call the API (comma-separated) |
| `ALLOWED_ORIGIN_REGEX` | no | Pattern for Vercel preview addresses |
| `AUTH_REQUIRED` | no | Default `true`; `false` only for local tests without Supabase |
| `SKU_MATCH_THRESHOLD` | no | Default `0.82` |
| `MATH_AUDIT_TOLERANCE` | no | Default `1.0` (rupees) |
| `MAX_UPLOAD_MB` | no | Default `10` |
| `SUPABASE_INVOICE_BUCKET` | no | Default `invoice-images` |
| `AGENTS_TRACE_SENSITIVE_DATA` | no | Default `false` (keeps invoice values out of OpenAI traces) |
| `PYTHON_VERSION` | Render only | `3.13.7` |

**Frontend** (`frontend/.env.local` locally, Vercel → Environment Variables in production):

| Name | Meaning |
|---|---|
| `NEXT_PUBLIC_API_URL` | The backend's address (`http://localhost:8765` locally, the Render URL in production). Not a secret; the frontend holds no keys. |

---

## Running a shop

1. **Set up:** a Supabase project for the shop, the SQL files, the backend and frontend pointed at it,
   and the owner's login linked to the shop.
2. **History:** the team enters the shop's past invoices (typed in, or photographed and checked) and
   the owner's or team's checks verify them; suppliers get their credit days.
3. **Models:** the team runs `python -m ml.train` and restarts the backend.
4. **Daily use:** the owner uploads new invoices and receipts, confirms products and payments,
   watches prices to check and payments due, and plans cash with the Cash Planner.
5. **Over time:** retrain the models every month or so as more invoices are verified.

---

## Status

Built: the agent pipeline, invoice review and verification, product matching with confirmation and
unlinking, suppliers, payments and receipts with confirmation and reminders, owner login with
per-shop data, the Dashboard, Purchase Analytics with Model 1, the Cash Planner with Model 2, the
Dashboard forecast, CI.

Not yet built: password-reset emails (needs email sending set up in Supabase), the optional opening
cash / inflows / other expenses in the Cash Planner, the Tesseract OCR baseline and the full
evaluation report. The OpenAI project currently allows only `gpt-4o-mini` and blocks
`text-embedding-3-small`, so photos are read with `gpt-4o-mini` and products are matched by exact
name until those models are enabled.

The demonstration database uses **synthetic** data shaped like the shop's archive (one fictional
hardware shop, about 880 invoices); results shown on it are labelled as synthetic and are not the
shop's real figures.

---

## Privacy

This repository is public. Never commit the participating shop's real name, supplier names,
invoices or photos; all test and demonstration data is fictional. Keys live only in `backend/.env`
(not in Git) and in Render; the frontend holds none. Trained models are built from a shop's private
invoices and are kept in that shop's private Storage bucket, never in Git.
