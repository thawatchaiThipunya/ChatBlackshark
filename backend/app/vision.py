"""Gemini image/video descriptions; descriptions are observations, not product facts."""
from __future__ import annotations

import os
import tempfile
import time
from dataclasses import dataclass

from google import genai
from google.genai import types


@dataclass(frozen=True)
class VisionResult:
    description: str
    model: str


def describe_media(data: bytes, mime_type: str, question: str, key: str) -> VisionResult:
    model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    prompt = ("อธิบายสิ่งที่เห็นในภาพหรือวิดีโอนี้เป็นภาษาไทยอย่างเป็นกลาง ระบุข้อความหรือรุ่นที่อ่านได้ "
              "ห้ามเดาสเปคสินค้าหรือวิธีแก้ปัญหาจากภาพ ถ้าดูไม่ชัดให้บอกว่าไม่ชัด "
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
