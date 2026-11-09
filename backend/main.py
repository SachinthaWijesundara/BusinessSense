from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.extract import router as extract_router

# Single env file: BusinessSense/.env (repo root)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

app = FastAPI(title="BusinessSense Extract API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(extract_router)


@app.get("/health")
def health():
    return {"ok": True}
