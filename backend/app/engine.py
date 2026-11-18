from __future__ import annotations

import os
import re
from typing import Any

from rapidfuzz import fuzz
from rapidfuzz.process import extract

from .knowledge import load_knowledge_base

try:
    from pythainlp.tokenize import word_tokenize
except ImportError:  # local offline use still works before optional NLP packages install
    word_tokenize = lambda text, **_: text.split()  # type: ignore[assignment]


HANDOFF_TEXT = "ขอเช็กกับเจ้าหน้าที่ก่อน แล้วเดี๋ยวส่งกลับนะครับ"
NEED_MODEL_TEXT = "ขอทราบชื่อรุ่นก่อนนะครับ เพื่อให้ค้นข้อมูลได้ตรงรุ่นครับ"
SENSITIVE_TOPICS = {
    "payment": ("ชำระเงิน", "จ่ายเงิน", "โอนเงิน", "คืนเงิน", "เงินคืน", "ยอดชำระ", "payment", "refund"),
    "warranty": ("ประกัน", "เคลม", "การรับประกัน", "warranty", "claim"),
    "tax_invoice": ("ใบกำกับภาษี", "ใบกำกับ", "tax invoice", "vat invoice"),
}


def _tokens(text: str) -> list[str]:
    return [token.casefold().strip() for token in word_tokenize(text, engine="newmm", keep_whitespace=False) if token.strip()]


def _score(query: str, candidate: str) -> float:
    q = " ".join(_tokens(query))
    c = " ".join(_tokens(candidate))
    if not q or not c:
        return 0.0
    if q in c:
        return 100.0
    return float(max(fuzz.token_set_ratio(q, c), fuzz.partial_ratio(q, c)))


def _source_answer(source: dict[str, Any], answer: str, *, matched_question: str | None = None) -> dict[str, Any]:
    return {
        "status": "answered",
        "answer": answer,
        "handoff_mock": False,
        "handoff_sent": False,
        "matched_question": matched_question,
        "sources": [source],
    }


def _handoff(reason: str, topic: str | None = None) -> dict[str, Any]:
    return {
        "status": "handoff_mock",
        "intent": "handoff" if topic is None else topic,
        "reason": reason,
        "answer": HANDOFF_TEXT,
        "handoff_mock": True,
        "handoff_sent": False,
        "matched_question": None,
        "sources": [],
    }


class ProductChatEngine:
    def __init__(self, threshold: int | None = None, gemini_key: str | None = None):
        self.data = load_knowledge_base()
        self.threshold = threshold if threshold is not None else int(os.getenv("MATCH_THRESHOLD", "78"))
        self.gemini_key = gemini_key if gemini_key is not None else os.getenv("GEMINI_API_KEY", "")
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    def _sensitive_topic(self, question: str) -> str | None:
        normalized = re.sub(r"\s+", "", question.casefold())
        for topic, phrases in SENSITIVE_TOPICS.items():
            if any(re.sub(r"\s+", "", phrase.casefold()) in normalized for phrase in phrases):
                return topic
        return None

    def answer(
        self,
        question: str,
        *,
        product_name: str | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        question = question.strip()
        if not question:
            return _handoff("empty_question")

        topic = self._sensitive_topic(question)
        if topic:
            return _handoff("sensitive_topic", topic)

        if product_name:
            return self._answer_product(question, product_name)
        if model_name:
            return self._answer_faq(question, model_name)
        return {
            "status": "need_model",
            "intent": "product_question",
            "reason": "model_required",
            "answer": NEED_MODEL_TEXT,
            "handoff_mock": False,
            "handoff_sent": False,
            "matched_question": None,
            "sources": [],
        }

    def _answer_faq(self, question: str, model_name: str) -> dict[str, Any]:
        if model_name not in self.data["support_models"]:
            return _handoff("model_not_found")
        if model_name.casefold().startswith(question.casefold()):
            return _handoff("faq_not_found")
        options = [item for item in self.data["faqs"] if item["model"] == model_name]
        exact = [item for item in options if item["question"].strip().casefold() == question.casefold()]
        if exact:
            if len({item["answer"] for item in exact}) > 1:
                return _handoff("faq_ambiguous")
            selected = exact[0]
            result = _source_answer(
                selected["source"],
                self._customer_wording(selected["answer"]),
                matched_question=selected["question"],
            )
            result.update({"intent": "troubleshooting", "reason": "faq_match", "match_score": 100.0})
            return result
        choices = [item["question"] for item in options]
        ranked = extract(question, choices, scorer=lambda q, c, **_: _score(q, c), limit=2)
        if not ranked or ranked[0][1] < self.threshold:
            return _handoff("faq_not_found")
        choice = ranked[0][0]
        if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < 5 and ranked[0][0] != ranked[1][0]:
            choice = self._gemini_pick(question, [item[0] for item in ranked]) or ""
            if not choice:
                return _handoff("faq_ambiguous")
        selected = next(item for item in options if item["question"] == choice)
        answer = self._customer_wording(selected["answer"])
        result = _source_answer(selected["source"], answer, matched_question=selected["question"])
        result.update({"intent": "troubleshooting", "reason": "faq_match", "match_score": round(ranked[0][1], 1)})
        return result

    def _answer_product(self, question: str, product_name: str) -> dict[str, Any]:
        product = next((item for item in self.data["products"] if item["name"] == product_name), None)
        if product is None:
            return _handoff("product_not_found")
        query_without_name = question.casefold().replace(product_name.casefold(), " ")
        if query_without_name.strip(" \t\n?!ฯ") in {
            "สเปกมีอะไรบ้าง", "สเปคมีอะไรบ้าง", "ขอสเปก", "ขอสเปค",
            "ข้อมูลสินค้า", "รายละเอียดสินค้า", "คุณสมบัติสินค้า",
        }:
            lines = [
                line.strip() for line in product["description"].splitlines()
                if line.strip() and not self._sensitive_topic(line)
            ][:15]
            if not lines:
                return _handoff("product_detail_not_found")
            result = _source_answer(product["source"], "\n".join(lines))
            result.update({"intent": "product_info", "reason": "product_overview", "product_name": product_name})
            return result
        terms = [term for term in re.findall(r"[a-z][a-z0-9.+-]{2,}", query_without_name) if term not in {"black", "shark"}]
        if terms:
            lines = [line.strip() for line in product["description"].splitlines() if line.strip()]
            matches = [line for line in lines if all(term in line.casefold() for term in terms)]
            if matches:
                answer = min(matches, key=len)
                result = _source_answer(product["source"], answer)
                result.update({"intent": "product_info", "reason": "product_detail_match", "product_name": product_name, "match_score": 100.0})
                return result
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n+", product["description"]) if part.strip()]
        ranked = extract(question, paragraphs, scorer=lambda q, c, **_: _score(q, c), limit=3)
        selected = [item for item in ranked if item[1] >= self.threshold]
        if not selected:
            return _handoff("product_detail_not_found")
        if len(selected) > 1 and selected[0][1] - selected[1][1] < 5:
            choice = self._gemini_pick(question, [item[0] for item in selected])
            if not choice:
                return _handoff("product_detail_ambiguous")
            selected = [item for item in selected if item[0] == choice]
        answer = "\n\n".join(item[0] for item in selected[:2])
        result = _source_answer(product["source"], answer)
        result.update({"intent": "product_info", "reason": "product_detail_match", "product_name": product_name, "match_score": round(selected[0][1], 1)})
        return result

    def _gemini_pick(self, question: str, candidates: list[str]) -> str | None:
        """Gemini may rank supplied source passages; it cannot author product facts."""
        if not self.gemini_key or len(candidates) < 2:
            return None
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(api_key=self.gemini_key)
            numbered = "\n".join(f"{index + 1}. {item[:1200]}" for index, item in enumerate(candidates))
            response = client.models.generate_content(
                model=self.gemini_model,
                contents=(
                    "Select the single source passage that best matches the customer's question. "
                    "Do not answer or add facts. Return only its number, or 0 if none match.\n"
                    f"Question: {question}\nSources:\n{numbered}"
                ),
                config=types.GenerateContentConfig(temperature=0, max_output_tokens=4),
            )
            match = re.fullmatch(r"\s*(\d+)\s*", response.text or "")
            if not match:
                return None
            index = int(match.group(1)) - 1
            return candidates[index] if 0 <= index < len(candidates) else None
        except Exception:
            return None

    @staticmethod
    def _customer_wording(answer: str) -> str:
        return (
            answer.replace("ให้ลูกค้า", "กรุณา")
            .replace("สอบถามลูกค้าว่า", "รบกวนแจ้งว่า")
            .replace("สอบถามลูกค้า", "รบกวนแจ้ง")
            .replace("แอดมิน", "เจ้าหน้าที่")
        )


def get_catalog() -> dict[str, Any]:
    data = load_knowledge_base()
    return {
        "products": [{"name": item["name"]} for item in data["products"]],
        "support_models": list(data["support_models"]),
        "counts": {"products": len(data["products"]), "support_models": len(data["support_models"]), "faq_entries": len(data["faqs"])},
    }
