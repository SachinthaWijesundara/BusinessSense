# BusinessSense frontend (Next.js on Vercel)

The owner's web app: the five screens from the proposal. It is only a user interface. It never
talks to Supabase or OpenAI and holds no keys; every action is one GET or POST request to the
FastAPI backend, sent from `lib/api.ts`. Totals, balances, due dates and the math audit are all
calculated by the backend and only displayed here.

## Run locally

```bash
npm install
copy .env.example .env.local
npm run dev
```

Open http://localhost:3001. The backend must be running (default http://localhost:8765).

| Variable | Meaning |
|---|---|
| `NEXT_PUBLIC_API_URL` | Address of the backend. Locally `http://localhost:8765`; on Vercel, the Render URL |

## Folders and files

| Path | What it does |
|---|---|
| `app/layout.tsx` | Root layout: font, global styles, sidebar shell around every screen |
| `app/globals.css` | Colours, layout, cards, forms, tables, badges |
| `app/login/page.tsx` | **Login**: email and password, sent to the backend (`POST /auth/login`); no sign-up |
| `app/page.tsx` | **Dashboard**: review queue, payments due, payments to confirm and 2-day reminders, monthly purchases, supplier spend, forecast slot (`GET /dashboard`) |
| `app/invoice-manager/page.tsx` | **Invoice Manager**: photo upload, search, invoice list |
| `app/invoice-manager/new/page.tsx` | Type in an invoice by hand (`GET /suppliers`, `POST /invoices`) |
| `app/invoice-manager/[invoiceId]/page.tsx` | Review screen for one invoice |
| `app/supplier-profile/page.tsx` | **Supplier Profile**: suppliers, credit days, totals, invoices, payments, items; payments waiting for confirmation (`/suppliers...`, `/payments`) |
| `app/purchase-analytics/page.tsx` | **Purchase Analytics**: each product's prices over time with Model 1's expected range, prices to check, spend by month and supplier (`/analytics/overview`, `/analytics/products/{id}`) |
| `app/cash-planner/page.tsx` | **Purchasing Cash Planner**: bills falling due by date, Model 2's expected purchases beside the baseline, and the cash to set aside for 14/30/60/90 days (`/analytics/cash-plan`) |
| `components/AppShell.tsx` | Sidebar navigation, the logged-in shop and Log out; opens the login page when logged out |
| `components/DocumentUpload.tsx` | Photo drop zone for invoices and payment receipts (`POST /invoices/upload`) |
| `components/InvoiceList.tsx` | Filters and invoice table (`GET /invoices`) |
| `components/InvoiceForm.tsx` | Editable invoice header and lines, values typed as printed |
| `components/InvoiceReview.tsx` | Photo beside the fields, audit result, supplier, product matching, verify / reject / paid (`/invoices/{id}...`, `/products`) |
| `components/SupplierForm.tsx` | Add-supplier form (`POST /suppliers`) |
| `components/PaymentForm.tsx` | Record a payment or correct one read from a receipt (`POST /payments`, `/payments/{id}/fix`) |
| `components/PaymentList.tsx` | Payments table: check, confirm, reject (`/payments/{id}/confirm`, `/payments/{id}/reject`) |
| `components/PriceChart.tsx` | A product's unit prices over time with the expected range and the next expected price (plain SVG) |
| `components/ItemAutocomplete.tsx` | Product picker over the catalogue |
| `lib/api.ts` | The only file that sends requests: one function per backend endpoint, plus the login session (token on every request, renewed before it expires) |
| `lib/types.ts` | Shapes of the backend's JSON |
| `lib/money.ts` | Number display and input parsing (no business maths) |
| `package.json`, `package-lock.json` | Libraries and the `dev` / `build` / `lint` commands |
| `next.config.ts`, `tsconfig.json`, `eslint.config.mjs` | Next.js, TypeScript and lint settings |
| `next-env.d.ts`, `AGENTS.md` | Written by Next.js itself; leave them as they are |

Generated folders (not in Git): `node_modules/` (installed libraries) and `.next/` (build output).

## Checks

```bash
npm run lint
npm run build
```
