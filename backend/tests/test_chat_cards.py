import unittest
from unittest.mock import patch

from backend.app import main


class CardChatFlowTests(unittest.TestCase):
    def test_exact_card_turn_uses_chat_engine_with_mongo_context_and_kb_model(self):
        listing = {
            "item_id": "123456789",
            "name": "Black Shark GS3 Smart Watch",
            "description": "รายการจาก Mongo: รองรับโหมดว่ายน้ำ",
            "image_url": "https://example.test/gs3.webp",
            "images": [],
        }
        products = [{"name": "Black Shark GS3", "description":
                     "ประเภทสินค้า: smart watch\nชื่อรุ่นสินค้าย่อ: GS3\nกันน้ำ 5ATM", "source": {"file": "kb.xlsx"}}]

        class FakeStore:
            def __init__(self):
                self.saved = []

            def chat_action(self, guest, action, session_id=None, payload=None):
                if action == "load_session":
                    return []
                self.saved.append((action, payload or {}))
                return {"id": "message-id"}

            def knowledge(self):
                return {"products": products, "faqs": [], "support_models": []}

        class EngineSpy:
            def __init__(self):
                self.call = None

            def answer(self, question, **kwargs):
                self.call = (question, kwargs)
                return {"status": "answered", "intent": "compatibility", "answer": "ตรวจแล้วครับ",
                        "product_name": "Black Shark GS3", "sources": [{"file": "kb.xlsx"}]}

        fake_store, engine_spy = FakeStore(), EngineSpy()
        request = main.ChatRequest(
            session_id="11111111-1111-4111-8111-111111111111",
            question="ใส่ว่ายน้ำได้ไหม",
            event={"type": "item", "item_id": "123456789", "text": "ใส่ว่ายน้ำได้ไหม"},
        )
        with patch.object(main, "store", fake_store), patch.object(main, "engine", engine_spy), \
             patch.object(main, "lookup_shopee_item", return_value=listing):
            reply = main.chat(request, chat_guest="g" * 40)

        self.assertEqual(engine_spy.call[1]["product_name"], "Black Shark GS3")
        self.assertEqual(engine_spy.call[1]["card_context"][0]["description"], listing["description"])
        self.assertEqual(reply["product_cards"][0]["product_name"], "Black Shark GS3")
        self.assertEqual(reply["source_type"], "shopee_exact_item")

    def test_multi_product_history_from_database_reaches_chat_engine(self):
        class FakeStore:
            def __init__(self):
                self.saved = []

            def chat_action(self, guest, action, session_id=None, payload=None):
                if action == "load_session":
                    return [{"role": "user", "content": "GS3 กับ GS3 Ultra ต่างกันยังไง", "payload": {}},
                            {"role": "assistant", "content": "เทียบรุ่นให้แล้วครับ", "payload": {
                        "product_name": None,
                        "intent_analysis": {"product_names": ["Black Shark GS3", "Black Shark GS3 Ultra"]},
                    }}]
                self.saved.append((action, payload or {}))
                return {"id": "message-id"}

        class EngineSpy:
            def __init__(self):
                self.call = None

            def answer(self, question, **kwargs):
                self.call = (question, kwargs)
                return {"status": "answered", "intent": "product_info", "answer": "ในเอกสารมีรุ่น Global ครับ"}

        fake_store, engine_spy = FakeStore(), EngineSpy()
        request = main.ChatRequest(session_id="11111111-1111-4111-8111-111111111111",
                                   question="ที่คุยเมื่อกี้เป็น Global Version ทั้งคู่ไหม")
        with patch.object(main, "store", fake_store), patch.object(main, "engine", engine_spy):
            main.chat(request, chat_guest="g" * 40)

        self.assertEqual(engine_spy.call[1]["history"][0]["product_scope"]["product_names"],
                         ["Black Shark GS3", "Black Shark GS3 Ultra"])


if __name__ == "__main__":
    unittest.main()
