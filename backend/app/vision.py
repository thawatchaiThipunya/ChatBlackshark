"""Gemini image/video descriptions; descriptions are observations, not product facts."""
from __future__ import annotations

import os
import logging
import tempfile
import time
from dataclasses import dataclass
from typing import Callable

from google import genai
from google.genai import types


logger = logging.getLogger("blackshark.vision")


@dataclass(frozen=True)
class VisionResult:
    description: str
    model: str


def describe_media(data: bytes, mime_type: str, question: str, key: str) -> VisionResult:
    model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    prompt = ("อธิบายภาพหรือวิดีโอนี้ตามคำถามลูกค้าเป็นภาษาไทย โดยเริ่มจากวัตถุหรือสินค้าที่เห็น "
              "ระบุชื่อรุ่น ตัวหนังสือ ป้าย หรือรหัสที่อ่านได้ และอาการผิดปกติที่มองเห็นจริงถ้ามี "
              "แยกสิ่งที่เห็นชัดกับสิ่งที่ยังไม่ชัด ห้ามเดาสเปค รุ่น หรือสาเหตุปัญหาจากภาพ "
              f"คำถามลูกค้า: {question[:1000]}")
    with genai.Client(api_key=key) as client:
        if mime_type.startswith("video/"):
            suffix = {"video/mp4": ".mp4", "video/webm": ".webm", "video/quicktime": ".mov"}[mime_type]
            with tempfile.NamedTemporaryFile(suffix=suffix) as file:
                file.write(data)
                file.flush()
                uploaded = client.files.upload(file=file.name)
                for _ in range(24):
                    if uploaded.state and uploaded.state.name == "ACTIVE":
                        break
                    if uploaded.state and uploaded.state.name == "FAILED":
                        raise RuntimeError("Gemini video processing failed")
                    time.sleep(2)
                    uploaded = client.files.get(name=uploaded.name)
                else:
                    raise RuntimeError("Gemini video processing timed out")
                contents = [uploaded, prompt]
                response = client.models.generate_content(model=model, contents=contents,
                    config=types.GenerateContentConfig(temperature=0, max_output_tokens=500))
        else:
            response = client.models.generate_content(model=model,
                contents=[types.Part.from_bytes(data=data, mime_type=mime_type), prompt],
                config=types.GenerateContentConfig(temperature=0, max_output_tokens=500))
    description = (response.text or "").strip()
    if not description:
        raise RuntimeError("Gemini returned no media description")
    return VisionResult(description[:4000], model)


def describe_with_key_rotation(
    data: bytes, mime_type: str, question: str,
    next_key: Callable[[], tuple[str | None, int | None, int]],
    describe: Callable[[bytes, str, str, str], VisionResult] = describe_media,
) -> VisionResult:
    key, key_id, count = next_key()
    if not key or not count:
        raise RuntimeError("Gemini key unavailable")
    attempted: set[int | None] = set()
    last_error: Exception | None = None
    for _ in range(count):
        if key and key_id not in attempted:
            attempted.add(key_id)
            try:
                return describe(data, mime_type, question, key)
            except Exception as exc:
                logger.warning("Gemini vision failed key_id=%s type=%s code=%s", key_id,
                               type(exc).__name__, getattr(exc, "code", None))
                last_error = exc
                if len(attempted) >= min(count, 3):
                    break
        key, key_id, _ = next_key()
    if last_error:
        raise last_error
    raise RuntimeError("Gemini vision key rotation failed")
