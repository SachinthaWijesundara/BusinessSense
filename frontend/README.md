# BusinessSense frontend

Owner product UI. Shares the same Supabase project as `purchase-entry`.

Config lives in the **repo-root** `BusinessSense/.env` only (loaded by `next.config.ts`).

```powershell
cd BusinessSense
copy .env.example .env
# fill NEXT_PUBLIC_SUPABASE_* and OPENAI_* in .env

cd frontend
npm install
npm run dev
```

App: http://localhost:3001

Extract API: http://localhost:8765 (see `../backend`).
