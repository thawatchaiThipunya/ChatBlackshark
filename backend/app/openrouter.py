"""Small OpenRouter chat-completions client for text and multimodal requests."""
from __future__ import annotations

import base64
import json
import urllib.request


URL = "https://openrouter.ai/api/v1/chat/completions"


def complete(key: str, model: str, content: str | list[dict], max_tokens: int = 1200) -> str:
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0,
        "max_tokens": max_tokens,
    }, ensure_ascii=False).encode()
    request = urllib.request.Request(URL, data=payload, method="POST", headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://chat-blackshark-web.vercel.app",
        "X-Title": "Black Shark Chat",
    })
    with urllib.request.urlopen(request, timeout=75) as response:
        data = json.load(response)
    message = data["choices"][0]["message"]["content"]
    if isinstance(message, list):
        message = "".join(part.get("text", "") for part in message if isinstance(part, dict))
    if not isinstance(message, str) or not message.strip():
        raise RuntimeError("OpenRouter returned no content")
    return message.strip()


def media_part(data: bytes, mime_type: str) -> dict:
    if len(data) > 12 * 1024 * 1024:
        raise ValueError("OpenRouter media must be 12 MB or smaller")
    url = f"data:{mime_type};base64,{base64.b64encode(data).decode()}"
    if mime_type.startswith("video/"):
        return {"type": "video_url", "video_url": {"url": url}}
    return {"type": "image_url", "image_url": {"url": url}}
