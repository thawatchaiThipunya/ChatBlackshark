import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from backend.app import main


ENGINE_TOKEN = "local-engine-test-token-longer-than-thirty-two-characters"


class StubProductChatEngine:
    received_store_label = None
    received_history = None

    def __init__(self, store):
        self.store = store

    def answer(self, question, **kwargs):
        type(self).received_store_label = kwargs.get("store_label")
        type(self).received_history = kwargs.get("history")
        return {
            "status": "answered",
            "intent": "product_info",
            "answer": f"ตอบ: {question}",
            "products": [],
            "reason": "kb_grounded",
        }


class EngineTestAnswerTests(unittest.TestCase):
    def setUp(self):
        StubProductChatEngine.received_store_label = None
        StubProductChatEngine.received_history = None

    def test_requires_matching_server_to_server_token(self):
        request = main.LegacyEngineTestAnswerRequest(
            question="ถามสเปก", platform="shopee", shop_id="shop-1")
        with patch.dict(os.environ, {"LEGACY_ENGINE_TOKEN": ENGINE_TOKEN}):
            with self.assertRaises(HTTPException) as raised:
                main.legacy_engine_test_answer(request, authorization="Bearer wrong-token")
        self.assertEqual(raised.exception.status_code, 401)

    def test_answers_with_the_requested_shop_scope_and_history(self):
        scope = {
            "platform": "shopee",
            "shop_id": "shop-1",
            "shop_name": "ร้านทดสอบ",
        }
        knowledge = {"products": [{"name": "สินค้า A", "description": "ข้อมูลทดสอบ"}]}
        request = main.LegacyEngineTestAnswerRequest(
            question="สินค้า A ใช้ยังไง",
            platform="shopee",
            shop_id="shop-1",
            history=[{"role": "user", "text": "สวัสดี"},
                     {"role": "model", "text": "สวัสดีครับ"}],
        )
        with patch.dict(os.environ, {"LEGACY_ENGINE_TOKEN": ENGINE_TOKEN}), \
             patch.object(main, "_shop_context", return_value=(scope, knowledge)), \
             patch.object(main, "_describe_legacy_media", return_value=[]), \
             patch.object(main, "ProductChatEngine", StubProductChatEngine):
            response = main.legacy_engine_test_answer(
                request, authorization=f"Bearer {ENGINE_TOKEN}")

        self.assertEqual(response["answer"], "ตอบ: สินค้า A ใช้ยังไง")
        self.assertEqual(response["source"], "chatblackshark")
        self.assertFalse(response["handoff_required"])
        self.assertEqual(StubProductChatEngine.received_store_label, "ร้านทดสอบ")
        self.assertEqual([turn["role"] for turn in StubProductChatEngine.received_history],
                         ["user", "assistant"])


if __name__ == "__main__":
    unittest.main()
