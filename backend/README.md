# BusinessSense extract API

FastAPI service for InvoiceVisionAgent (Instructor + OpenAI `gpt-4o-mini-2024-07-18`, CLAHE preprocess).

## Setup

```powershell
cd BusinessSense/backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
# Set OPENAI_API_KEY in .env
.\.venv\Scripts\uvicorn main:app --reload --host 127.0.0.1 --port 8001
```

- `GET /health` — liveness
- `POST /extract` — multipart `file` image → invoice/receipt JSON, or `422` / `502` with `error_type`

The BusinessSense frontend will call this via `NEXT_PUBLIC_EXTRACT_API_URL` (purchase-entry is a separate teammate app and is not modified here).
