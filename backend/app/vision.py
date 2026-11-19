"""Gemini image/video descriptions; descriptions are observations, not product facts."""
from __future__ import annotations

import os
import json
import logging
import tempfile
import time
from dataclasses import dataclass
from typing import Callable

from google import genai
from google.genai import types
from .openrouter import complete as openrouter_complete, media_part


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


def describe_media_batch(media: list[tuple[bytes, str]], question: str, key: str,
                         model: str | None = None) -> list[VisionResult]:
    """Describe up to five attachments in one Gemini request, preserving their order."""
    if not media or len(media) > 5:
        raise ValueError("Vision batch must contain 1 to 5 attachments")
    model = model or os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    prompt = (
        "อธิบายภาพ/วิดีโอแต่ละรายการแยกกันตามลำดับเป็นภาษาไทย ตอบ JSON เท่านั้น "
        'รูปแบบ {"descriptions":["คำอธิบายรายการ 1",...]}. '
        "บอกสิ่งที่เห็นจริง ชื่อรุ่น/ข้อความที่อ่านได้ และอาการที่มองเห็น "
        "ห้ามเดาสเปค รุ่น หรือสาเหตุปัญหา จำนวนคำอธิบายต้องเท่าจำนวนไฟล์ "
        f"คำถามลูกค้า: {question[:1000]}"
    )
    with genai.Client(api_key=key) as client:
        contents = []
        for data, mime_type in media:
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
                contents.append(uploaded)
            else:
                contents.append(types.Part.from_bytes(data=data, mime_type=mime_type))
        contents.append(prompt)
        response = client.models.generate_content(model=model, contents=contents,
            config=types.GenerateContentConfig(temperature=0, max_output_tokens=1600,
                response_mime_type="application/json"))
    try:
        descriptions = json.loads(response.text or "")["descriptions"]
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError("Gemini returned invalid batch description") from exc
    if not isinstance(descriptions, list) or len(descriptions) != len(media) or not all(
        isinstance(item, str) and item.strip() for item in descriptions
    ):
        raise RuntimeError("Gemini returned incomplete batch description")
    return [VisionResult(item.strip()[:4000], model) for item in descriptions]


def describe_openrouter_batch(media: list[tuple[bytes, str]], question: str,
                              key: str, model: str) -> list[VisionResult]:
    if not media or len(media) > 5:
        raise ValueError("Vision batch must contain 1 to 5 attachments")
    prompt = (
        "อธิบายภาพ/วิดีโอแต่ละรายการแยกตามลำดับเป็นภาษาไทย ตอบ JSON เท่านั้น "
        'รูปแบบ {"descriptions":["คำอธิบายรายการ 1",...]}. '
        "บอกสิ่งที่เห็นจริง ชื่อรุ่น/ข้อความที่อ่านได้ และอาการที่มองเห็น "
        "ห้ามเดาสเปค รุ่น หรือสาเหตุปัญหา จำนวนคำอธิบายต้องเท่าจำนวนไฟล์ "
        f"คำถามลูกค้า: {question[:1000]}"
    )
    content = [{"type": "text", "text": prompt}] + [media_part(data, mime) for data, mime in media]
    raw = openrouter_complete(key, model, content, max_tokens=1800)
    try:
        parsed = json.loads(raw)
    except ValueError:
        import re
        match = re.search(r"\{.*\}", raw, re.S)
        parsed = json.loads(match.group()) if match else {}
    descriptions = parsed.get("descriptions")
    if not isinstance(descriptions, list) or len(descriptions) != len(media) or not all(
        isinstance(item, str) and item.strip() for item in descriptions
    ):
        raise RuntimeError("OpenRouter returned incomplete batch description")
    return [VisionResult(item.strip()[:4000], model) for item in descriptions]


def describe_batch_with_key_rotation(
    media: list[tuple[bytes, str]], question: str,
    next_key: Callable[[], tuple[str | None, int | None, int]],
    describe: Callable[[list[tuple[bytes, str]], str, str], list[VisionResult]] = describe_media_batch,
) -> list[VisionResult]:
    key, key_id, count = next_key()
    if not key or not count:
        raise RuntimeError("Gemini key unavailable")
    attempted: set[int | None] = set()
    last_error: Exception | None = None
    for _ in range(count):
        if key and key_id not in attempted:
            attempted.add(key_id)
            try:
                return describe(media, question, key)
            except Exception as exc:
                logger.warning("Gemini vision batch failed key_id=%s type=%s code=%s", key_id,
                               type(exc).__name__, getattr(exc, "code", None))
                last_error = exc
                if len(attempted) >= min(count, 3):
                    break
        key, key_id, _ = next_key()
    if last_error:
        raise last_error
    raise RuntimeError("Gemini vision key rotation failed")
