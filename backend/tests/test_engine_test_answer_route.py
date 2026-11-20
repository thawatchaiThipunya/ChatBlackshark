import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import main


class EngineTestAnswerRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def test_test_answer_requires_the_engine_bearer_token(self):
        response = self.client.post("/api/engine/test-answer", json={
            "question": "รุ่นนี้กันน้ำไหม",
            "platform": "shopee",
            "shop_id": "111",
        })
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "legacy_engine_unauthorized")

    def test_test_answer_uses_shop_scope_card_and_vision_evidence(self):
        token = "t" * 40
        scope = {"platform": "shopee", "shop_id": "111", "shop_name": "Black Shark Thailand"}
        knowledge = {"products": [{"name": "Black Shark GS3", "description": "กันน้ำ 5ATM", "source": {}}],
                     "faqs": []}
        card = {"item_id": "56067346193", "name": "BLACK SHARK GS3", "models": []}
        captured = {}

        class FakeEngine:
            def __init__(self, store):
                captured["store"] = store

            def answer(self, question, **kwargs):
                captured["question"] = question
                captured["kwargs"] = kwargs
                return {"status": "answered", "intent": "product_info", "answer": "รองรับ 5ATM ครับ",
                        "products": [card], "sources": []}

        payload = {
            "question": "ใส่ว่ายน้ำได้ไหม",
            "platform": "shopee",
            "shop_id": "111",
            "shop_name": "Black Shark Thailand",
            "history": [{"role": "assistant", "content": "ก่อนหน้านี้คุยเรื่อง GS3"}],
            "images": ["https://cf.shopee.co.th/photo.jpg"],
            "item_ids": ["56067346193"],
        }

        with patch.dict(os.environ, {"LEGACY_ENGINE_TOKEN": token}), \
             patch.object(main, "_shop_context", return_value=(scope, knowledge)), \
             patch.object(main, "_describe_legacy_media", return_value=["ภาพนาฬิกาสีดำ"]), \
             patch.object(main.legacy_data, "shop_alias_is_unique", return_value=True), \
             patch.object(main.legacy_data, "_products_by_ids", return_value={card["item_id"]: card}), \
             patch.object(main, "ProductChatEngine", FakeEngine):
            response = self.client.post("/api/engine/test-answer", json=payload,
                                        headers={"Authorization": f"Bearer {token}"})

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["answer"], "รองรับ 5ATM ครับ")
        self.assertEqual(body["status"], "answered")
        self.assertEqual(body["product_cards"][0]["item_id"], "56067346193")
        self.assertEqual(body["image_desc"], "ภาพนาฬิกาสีดำ")
        self.assertEqual(captured["question"], payload["question"])
        self.assertEqual(captured["kwargs"]["history"][0]["content"], "ก่อนหน้านี้คุยเรื่อง GS3")
        self.assertEqual(captured["kwargs"]["vision_evidence"], ["ภาพนาฬิกาสีดำ"])
        self.assertEqual(captured["kwargs"]["card_context"][0]["product_name"], "Black Shark GS3")


if __name__ == "__main__":
    unittest.main()
