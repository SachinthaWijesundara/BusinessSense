# BusinessSense

Notes for AI coding assistants (Claude, Cursor) working in this repository.

Web system for invoice management and purchasing intelligence for SMEs and medium-scale shops. The participating hardware shop is the worked example that proves feasibility on a real invoice archive.

## Spec

Product behaviour, stack, agents, models, fail-safes and screens come from the ModelX 2.0 Phase 1 proposal (Team DeepForge). The repository mirrors its architecture:

- `frontend/` — Next.js owner app on Vercel. Only sends HTTP requests to the backend; no keys, no business logic.
- `backend/` — FastAPI on Render. Owns all business logic and secrets, runs the agent pipeline, talks to Supabase.
- `database/` — the Supabase PostgreSQL schema, applied in order.

## Layers (do not blur)

1. **Agent pipeline** — read invoices and match products (with human confirmation).
2. **Deterministic rules** — math audit, due dates, balances, purchasing cash requirement.
3. **Predictive models** — Model 1 (item price) and Model 2 (purchasing spend); run only on verified, product-linked history.

Agents do not forecast prices or cash. Models do not replace the math audit or the cash planner rules.

## Privacy

The repository is public. Never commit the participating shop's real name, supplier names, invoices or photos; test data stays fictional.
