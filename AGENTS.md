# BusinessSense

Web system for invoice management and purchasing intelligence for SMEs and medium-scale shops. The participating hardware shop is the worked example that proves feasibility on a real invoice archive.

## Spec

Product behaviour, stack, agents, models, fail-safes, and screens come from the ModelX Phase 1 proposal (`final prop.docx`). Follow the Cursor rules in `.cursor/rules/` on every change.

## Layers (do not blur)

1. **Agent pipeline** — read invoices and match products (with human confirmation).
2. **Deterministic rules** — math audit, due dates, balances, purchasing cash requirement.
3. **Predictive models** — Model 1 (item price) and Model 2 (purchasing spend); run only on verified, product-linked history.

Agents do not forecast prices or cash. Models do not replace the math audit or the cash planner rules.
