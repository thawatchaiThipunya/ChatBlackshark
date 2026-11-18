from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .engine import ProductChatEngine, get_catalog


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
    allow_headers=["Content-Type"],
)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    product_name: str | None = None
    model_name: str | None = None


engine = ProductChatEngine()


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/catalog")
def catalog() -> dict:
    return get_catalog()


@app.post("/api/chat")
def chat(request: ChatRequest) -> dict:
    question = request.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="question must not be blank")

    if request.product_name and request.model_name:
        raise HTTPException(status_code=422, detail="Select either product_name or model_name")
    return engine.answer(question, product_name=request.product_name, model_name=request.model_name)
