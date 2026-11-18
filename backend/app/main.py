from __future__ import annotations

import os
import hmac

from fastapi import FastAPI, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .engine_v2 import ProductChatEngine, get_catalog
from .store import SupabaseStore


app = FastAPI(
    title="Black Shark Product Chat API",
    version="1.0.0",
    description="Answers only from the two supplied Black Shark workbooks.",
)

allowed_origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Admin-Password"],
)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    product_name: str | None = None
    model_name: str | None = None


engine = ProductChatEngine()
store = engine.store


class KeyRequest(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    key: str = Field(min_length=10, max_length=300)


def require_admin(password: str | None) -> None:
    expected = os.getenv("ADMIN_PASSWORD", "")
    if not expected or not password or not hmac.compare_digest(password, expected):
        raise HTTPException(status_code=401, detail="admin_unauthorized")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/catalog")
def catalog() -> dict:
    try:
        return get_catalog(store)
    except Exception:
        raise HTTPException(status_code=503, detail="knowledge_unavailable")


@app.post("/api/chat")
def chat(request: ChatRequest) -> dict:
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="question must not be blank")

    if request.product_name and request.model_name:
        raise HTTPException(status_code=422, detail="Select either product_name or model_name")
    return engine.answer(question, product_name=request.product_name, model_name=request.model_name)


@app.get("/api/llm/keys")
def list_llm_keys(x_admin_password: str | None = Header(default=None)) -> dict:
    require_admin(x_admin_password)
    try:
        return {"keys": store.list_keys()}
    except Exception:
        raise HTTPException(status_code=503, detail="key_store_unavailable")


@app.post("/api/llm/keys")
def add_llm_key(request: KeyRequest, x_admin_password: str | None = Header(default=None)) -> dict:
    require_admin(x_admin_password)
    try:
        return store.add_key(request.label, request.key)
    except Exception:
        raise HTTPException(status_code=503, detail="key_store_unavailable")


@app.delete("/api/llm/keys/{key_id}")
def delete_llm_key(key_id: int, x_admin_password: str | None = Header(default=None)) -> dict:
    require_admin(x_admin_password)
    try:
        return store.delete_key(key_id)
    except Exception:
        raise HTTPException(status_code=503, detail="key_store_unavailable")
