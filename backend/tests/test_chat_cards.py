import unittest
from unittest.mock import patch

from backend.app import main


class CardChatFlowTests(unittest.TestCase):
    def test_followup_uses_kb_product_not_previous_recommendation_cards(self):
        session_id = "11111111-1111-4111-8111-111111111111"
        previous = [
            {"role": "user", "content": "GS3 Ultra กันน้ำระดับไหน", "payload": {}},
            {"role": "assistant", "content": "GS3 Ultra กันน้ำ 5ATM", "payload": {
                "product_name": "Black Shark GS3 Ultra",
                "intent_analysis": {"product_names": ["Black Shark GS3 Ultra"]},
                "product_cards": [{"item_id": "1", "name": "GS3 Ultra listing"},
                                  {"item_id": "2", "name": "GS3 listing"}],
            }},
        ]

        class FakeStore:
            def chat_action(self, guest, action, session_id=None, payload=None):
                if action == "load_session":
                    return previous
                if action == "product_anchor":
                    return None
                return {"id": "saved"}

        class EngineSpy:
            def answer(self, question, **kwargs):
                self.kwargs = kwargs
                return {"status": "answered", "intent": "compatibility", "answer": "ไม่แนะนำซาวน่าครับ",
                        "product_name": "Black Shark GS3 Ultra", "sources": []}

        engine_spy = EngineSpy()
        scope = {"platform": "shopee", "shop_id": "1191420560", "shop_name": "Black Shark Thailand"}
        knowledge = {"products": [{"name": "Black Shark GS3 Ultra", "description": "ไม่แนะนำซาวน่า"}], "faqs": []}
        request = main.ChatRequest(session_id=session_id, question="อาบน้ำอุ่นได้ไหม")
        with patch.object(main, "store", FakeStore()), \
             patch.object(main, "_shop_context", return_value=(scope, knowledge)), \
             patch.object(main, "ProductChatEngine", return_value=engine_spy):
            main.chat(request, chat_guest="g" * 40)

        self.assertEqual(engine_spy.kwargs["product_name"], "Black Shark GS3 Ultra")
        self.assertEqual(engine_spy.kwargs["card_context"], [])

    def test_old_recommendation_is_not_reused_after_unrelated_exchange(self):
        previous = [
            {"role": "user", "content": "แนะนำนาฬิกา", "payload": {}},
            {"role": "assistant", "content": "ลอง GS3", "payload": {"product_name": "Black Shark GS3"}},
            {"role": "user", "content": "ขอบคุณ", "payload": {}},
            {"role": "assistant", "content": "ยินดีครับ", "payload": {}},
        ]
        class FakeStore:
            def chat_action(self, guest, action, session_id=None, payload=None):
                if action == "load_session":
                    return previous
                if action == "product_anchor":
                    return None
                return {"id": "saved"}
        class EngineSpy:
            def answer(self, question, **kwargs):
                self.kwargs = kwargs
                return {"status": "need_model", "answer": "หมายถึงรุ่นไหนครับ", "sources": []}
        spy = EngineSpy()
        scope = {"platform": "shopee", "shop_id": "1191420560", "shop_name": "Black Shark Thailand"}
        knowledge = {"products": [{"name": "Black Shark GS3", "description": "นาฬิกา"}], "faqs": []}
        request = main.ChatRequest(session_id="11111111-1111-4111-8111-111111111111", question="แบตกี่วัน")
        with patch.object(main, "store", FakeStore()), \
             patch.object(main, "_shop_context", return_value=(scope, knowledge)), \
             patch.object(main, "ProductChatEngine", return_value=spy):
            main.chat(request, chat_guest="g" * 40)

        self.assertIsNone(spy.kwargs["product_name"])

    def test_public_chat_uses_only_configured_shop_kb_and_scopes_card_lookup(self):
        session_id = "11111111-1111-4111-8111-111111111111"
        scope = {"platform": "shopee", "shop_id": "1191420560", "shop_name": "Black Shark Thailand",
                 "files": ["blackshark.xlsx"]}
        global_products = [
            {"name": "Black Shark GS3", "description": "Black Shark facts", "source": {"file": "blackshark.xlsx"}},
            {"name": "Other Store Watch", "description": "Foreign shop facts", "source": {"file": "other.xlsx"}},
        ]
        scoped_knowledge = {"products": global_products[:1], "faqs": [], "support_models": []}
        listing = {"item_id": "123456789", "name": "Black Shark GS3", "description": "listing",
                   "image_url": "https://example.test/gs3.webp", "models": []}

        class FakeStore:
            def __init__(self):
                self.global_knowledge_calls = 0

            def chat_action(self, guest, action, session_id=None, payload=None):
                return [] if action == "load_session" else {"id": "saved"}

            def knowledge(self):
                self.global_knowledge_calls += 1
                return {"products": global_products, "faqs": [], "support_models": []}

            def shop_scopes(self):
                return [scope]

        engines = []

        class ScopedEngine:
            def __init__(self, store):
                self.store = store
                self.call = None
                engines.append(self)

            def answer(self, question, **kwargs):
                self.call = kwargs
                return {"status": "answered", "intent": "product_info", "answer": "GS3 ครับ",
                        "product_name": "Black Shark GS3", "sources": []}

        fake_store = FakeStore()
        request = main.ChatRequest(
            session_id=session_id,
            question="จอกี่นิ้ว",
            event={"type": "item", "item_id": "123456789", "text": "จอกี่นิ้ว"},
        )
        with patch.object(main, "store", fake_store), patch.object(main, "_shop_context", return_value=(scope, scoped_knowledge)), \
             patch.object(main, "ProductChatEngine", ScopedEngine), patch.object(main, "lookup_shopee_item", return_value=listing) as lookup:
            main.chat(request, chat_guest="g" * 40)

        self.assertEqual(len(engines), 1)
        self.assertEqual([row["name"] for row in engines[0].store.knowledge()["products"]], ["Black Shark GS3"])
        self.assertEqual(fake_store.global_knowledge_calls, 0)
        self.assertEqual(lookup.call_args.kwargs, {
            "platform": "shopee", "shop_id": "1191420560", "shop_name": "Black Shark Thailand"
        })

    def test_replaying_a_completed_turn_returns_saved_answer_without_saving_twice(self):
        session_id = "11111111-1111-4111-8111-111111111111"
        saved_reply = {"status": "answered", "intent": "product_info", "answer": "ตอบครั้งเดียวครับ",
                       "request_id": "turn-1", "sources": []}

        class FakeStore:
            def __init__(self):
                self.saved = []

            def chat_action(self, guest, action, session_id=None, payload=None):
                if action == "load_session":
                    return [{"role": "user", "content": "GS3 จอกี่นิ้ว", "payload": {"request_id": "turn-1"}},
                            {"role": "assistant", "content": "ตอบครั้งเดียวครับ", "payload": saved_reply}]
                self.saved.append((action, payload or {}))
                return {"id": "message-id"}

        class UnexpectedEngine:
            def answer(self, question, **kwargs):
                raise AssertionError("a completed request must return its stored answer")

        fake_store = FakeStore()
        request = main.ChatRequest(session_id=session_id, question="GS3 จอกี่นิ้ว", request_id="turn-1")
        with patch.object(main, "store", fake_store), patch.object(main, "engine", UnexpectedEngine()):
            reply = main.chat(request, chat_guest="g" * 40)

        self.assertEqual(reply, saved_reply)
        self.assertEqual(fake_store.saved, [])

    def test_exact_card_turn_uses_chat_engine_with_mongo_context_and_kb_model(self):
        listing = {
            "item_id": "123456789",
            "name": "(NewArrival) Official product listing",
            "description": "รายการจาก Mongo: รองรับโหมดว่ายน้ำ",
            "image_url": "https://example.test/gs3.webp",
            "images": [],
        }
        products = [{"name": "Black Shark GS3", "description":
                     "ประเภทสินค้า: smart watch\nชื่อรุ่นสินค้าย่อ: GS3\nกันน้ำ 5ATM",
                     "item_ids": [123456789], "source": {"file": "kb.xlsx"}}]

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
        scope = {"platform": "shopee", "shop_id": "1191420560", "shop_name": "Black Shark Thailand", "files": ["kb.xlsx"]}
        request = main.ChatRequest(
            session_id="11111111-1111-4111-8111-111111111111",
            question="ใส่ว่ายน้ำได้ไหม",
            event={"type": "item", "item_id": "123456789", "text": "ใส่ว่ายน้ำได้ไหม"},
        )
        with patch.object(main, "store", fake_store), patch.object(main, "_shop_context", return_value=(scope, fake_store.knowledge())), \
             patch.object(main, "ProductChatEngine", return_value=engine_spy), \
             patch.object(main, "lookup_shopee_item", return_value=listing):
            reply = main.chat(request, chat_guest="g" * 40)

        self.assertEqual(engine_spy.call[1]["product_name"], "Black Shark GS3")
        self.assertEqual(engine_spy.call[1]["card_context"], [{"item_id": "123456789",
            "name": "Black Shark GS3", "product_name": "Black Shark GS3"}])
        self.assertEqual(reply["product_cards"][0]["product_name"], "Black Shark GS3")
        self.assertEqual(reply["source_type"], "shopee_exact_item")
        saved_messages = [payload for action, payload in fake_store.saved if action == "save_message"]
        self.assertEqual(len(saved_messages), 2)
        self.assertEqual(saved_messages[0]["payload"]["product_cards"][0]["product_name"], "Black Shark GS3")
        user_turn_id = saved_messages[0]["payload"]["request_id"]
        assistant_turn_id = saved_messages[1]["payload"]["request_id"]
        self.assertTrue(user_turn_id)
        self.assertEqual(user_turn_id, assistant_turn_id)

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
        scope = {"platform": "shopee", "shop_id": "1191420560", "shop_name": "Black Shark Thailand", "files": ["kb.xlsx"]}
        request = main.ChatRequest(session_id="11111111-1111-4111-8111-111111111111",
                                   question="ที่คุยเมื่อกี้เป็น Global Version ทั้งคู่ไหม")
        with patch.object(main, "store", fake_store), patch.object(main, "_shop_context", return_value=(scope, {"products": [], "faqs": []})), \
             patch.object(main, "ProductChatEngine", return_value=engine_spy):
            main.chat(request, chat_guest="g" * 40)

        self.assertEqual(engine_spy.call[1]["history"][0]["product_scope"]["product_names"],
                         ["Black Shark GS3", "Black Shark GS3 Ultra"])

    def test_public_chat_does_not_read_mongo_for_stock_or_product_questions(self):
        listing = {"item_id": "123456789", "name": "Black Shark GS3", "description": "", "image_url": ""}
        products = [{"name": "Black Shark GS3", "description": "ประเภทสินค้า: smart watch", "source": {}}]
        scope = {"platform": "shopee", "shop_id": "1191420560", "shop_name": "Black Shark Thailand",
                 "files": ["kb.xlsx"]}

        class FakeStore:
            def chat_action(self, guest, action, session_id=None, payload=None):
                return [] if action == "load_session" else {"id": "saved"}

            def knowledge(self):
                return {"products": products, "faqs": [], "support_models": []}

            def knowledge_for_shop(self, platform, shop_id):
                return scope, {"products": products, "faqs": [], "support_models": []}

            def shop_scopes(self):
                return [scope]

        class EngineSpy:
            def __init__(self):
                self.call = None

            def answer(self, question, **kwargs):
                self.call = kwargs
                return {"status": "answered", "intent": "stock_availability", "answer": "มีครับ"}

        fake_store, engine_spy = FakeStore(), EngineSpy()
        scoped_knowledge = {"products": products, "faqs": [], "support_models": []}
        request = main.ChatRequest(
            session_id="11111111-1111-4111-8111-111111111111",
            question="สีเงินมีของไหม",
            event={"type": "item", "item_id": "123456789", "text": "สีเงินมีของไหม"},
        )
        scope = {"platform": "shopee", "shop_id": "1191420560", "shop_name": "Black Shark Thailand", "files": ["kb.xlsx"]}
        with patch.object(main, "store", fake_store), patch.object(main, "_shop_context", return_value=(scope, scoped_knowledge)), \
             patch.object(main, "ProductChatEngine", return_value=engine_spy), \
             patch.object(main, "lookup_shopee_item", return_value=listing), \
             patch.object(main.legacy_data, "resolve_legacy_shop_name", return_value="BlackShark"), \
             patch.object(main.legacy_data, "_products_collection") as products_collection:
            main.chat(request, chat_guest="g" * 40)
            self.assertTrue(callable(engine_spy.call["commerce_lookup"]))
            resolved = engine_spy.call["commerce_lookup"](
                "stock_availability", ["Black Shark GS3"], [{"item_id": "123456789"}])

        self.assertEqual(resolved, {"unavailable": True})
        products_collection.assert_not_called()


if __name__ == "__main__":
    unittest.main()
