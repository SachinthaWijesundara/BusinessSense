# BusinessSense frontend

Owner product UI (five screens). Shares the same Supabase project as `purchase-entry`.

```powershell
cd BusinessSense/frontend
npm install
copy .env.example .env.local
# Use the same NEXT_PUBLIC_SUPABASE_* values as purchase-entry
npm run dev
```

App: http://localhost:3001

Extract API (separate process): http://localhost:8001 — set `OPENAI_API_KEY` in `BusinessSense/backend/.env`.
