import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from backend.app import main
from backend.app.chat_inputs import normalize_chat_input


class KBOnlyProductSourceTests(unittest.TestCase):
    def setUp(self):
        self.token = "t" * 40
        self.scope = {"platform": "shopee", "shop_id": "111", "shop_name": "Shop A"}
        self.knowledge = {
            "products": [{"name": "Acme Watch X", "description": "จอ AMOLED 1.4 นิ้ว", "source": {"file": "a.xlsx"}}],
            "faqs": [],
        }

    def run_test_answer(self, payload, card_map=None, vision=None, engine_type=None):
        with patch.dict(os.environ, {"LEGACY_ENGINE_TOKEN": self.token}), \
             patch.object(main, "_shop_context", return_value=(self.scope, self.knowledge)), \
             patch.object(main.legacy_data, "shop_alias_is_unique", return_value=True), \
             patch.object(main.legacy_data, "resolve_legacy_shop_name", return_value="Shop A"), \
             patch.object(main.legacy_data, "product_identities_by_ids", return_value=card_map or {}), \
             patch.object(main, "_describe_legacy_media", return_value=vision or []), \
             patch.object(main, "ProductChatEngine", engine_type or CapturingEngine):
            return main.legacy_engine_test_answer(
                main.LegacyEngineTestAnswerRequest.model_validate(payload),
                authorization=f"Bearer {self.token}",
            )

    def test_ordinary_product_question_does_not_query_mongo_catalog_or_stock(self):
        engine = CapturingEngine
        with patch.object(main.legacy_data, "_products_collection") as products_collection:
            self.run_test_answer({"question": "มีนาฬิกา Acme แนะนำไหม", "platform": "shopee", "shop_id": "111"},
                                engine_type=engine)
        products_collection.assert_not_called()
        self.assertEqual(engine.call_kwargs["commerce_lookup"]("product_search", ["Acme Watch X"], []), {})
        self.assertEqual(engine.call_kwargs["commerce_lookup"]("stock_availability", ["Acme Watch X"], []),
                         {"unavailable": True})

    def test_card_identity_is_mapped_to_kb_and_mongo_facts_are_not_sent_to_llm(self):
        engine = CapturingEngine
        mongo_listing = {
            "item_id": "123", "name": "Acme Watch X", "description": "MONGO ONLY CLAIM",
            "models": [{"model_name": "Red", "available": 99}], "tier_variation": [{"options": ["Red"]}],
        }
        self.run_test_answer({"question": "จอกี่นิ้ว", "platform": "shopee", "shop_id": "111",
                              "item_ids": ["123"]},
                             card_map={"123": mongo_listing}, engine_type=engine)
        self.assertEqual(engine.call_kwargs["card_context"], [
            {"item_id": "123", "name": "Acme Watch X", "product_name": "Acme Watch X"}
        ])
        self.assertEqual(engine.call_kwargs["product_name"], "Acme Watch X")
        self.assertEqual(engine.call_kwargs["commerce_lookup"]("product_info", ["Acme Watch X"],
                                                                  engine.call_kwargs["card_context"]), {})

    def test_bundle_maps_every_item_to_its_own_kb_product(self):
        engine = CapturingEngine
        rows = {
            "456": {"item_id": "456", "name": "Acme Charger Y", "description": "external"},
            "123": {"item_id": "123", "name": "Acme Watch X", "description": "external"},
        }
        self.knowledge["products"].append({"name": "Acme Charger Y", "description": "USB-C", "source": {}})
        self.run_test_answer({"question": "สองชิ้นนี้ใช้ด้วยกันไหม", "platform": "shopee", "shop_id": "111",
                              "item_ids": ["123", "456"]}, card_map=rows, engine_type=engine)
        self.assertEqual(engine.call_kwargs["card_context"], [
            {"item_id": "123", "name": "Acme Watch X", "product_name": "Acme Watch X"},
            {"item_id": "456", "name": "Acme Charger Y", "product_name": "Acme Charger Y"},
        ])

    def test_card_with_no_exact_kb_match_uses_existing_handoff_response_shape(self):
        engine = CapturingEngine
        engine.called = False
        body = self.run_test_answer({"question": "รุ่นนี้มีอะไรเด่น", "platform": "shopee", "shop_id": "111",
                                     "item_ids": ["456"]},
                                    card_map={"456": {"item_id": "456", "name": "Acme Special Edition"}},
                                    engine_type=engine)
        self.assertEqual(body["status"], "handoff_required")
        self.assertTrue(body["handoff_required"])
        self.assertEqual(body["handoff_reason"], "card_not_in_shop_kb")
        self.assertIn("KB", body["handoff_explanation"])
        self.assertEqual(body["unresolved_item_ids"], ["456"])
        self.assertFalse(engine.called)

    def test_image_description_is_passed_into_answer_engine(self):
        engine = CapturingEngine
        body = self.run_test_answer({"question": "ช่วยดูภาพนี้", "platform": "shopee", "shop_id": "111",
                                     "images": ["https://cf.shopee.co.th/watch.jpg"]},
                                    vision=["เห็นนาฬิกาหน้าจอมีเส้นสีขาว"], engine_type=engine)
        self.assertEqual(engine.call_kwargs["vision_evidence"], ["เห็นนาฬิกาหน้าจอมีเส้นสีขาว"])
        self.assertEqual(body["image_description"], "เห็นนาฬิกาหน้าจอมีเส้นสีขาว")

    def test_ten_legacy_images_are_described_as_two_batches_and_returned_to_answer(self):
        batch_sizes = []

        def describe(media, question, key, model):
            batch_sizes.append(len(media))
            offset = sum(batch_sizes[:-1])
            return [SimpleNamespace(description=f"ภาพ {offset + index + 1}") for index in range(len(media))]

        images = [{"url": f"https://cf.shopee.co.th/{index}.jpg"} for index in range(10)]
        with patch.object(main, "_download_legacy_media", return_value=(b"image", "image/jpeg")), \
             patch.object(main.store, "runtime_config", return_value={"provider": "openrouter",
                 "openrouter_key": "test-key", "openrouter_vision_model": "test-vision"}), \
             patch.object(main, "describe_openrouter_batch", side_effect=describe):
            descriptions = main._describe_legacy_media(images, "ช่วยดูภาพ")

        self.assertEqual(batch_sizes, [5, 5])
        self.assertEqual(descriptions, [f"ภาพ {index}" for index in range(1, 11)])

    def test_botworker_engine_path_uses_card_id_only_for_identity_and_kb_for_answer(self):
        engine = CapturingEngine
        mongo_card = {"item_id": "123", "name": "Acme Watch X", "description": "EXTERNAL FACT",
                      "models": [{"model_name": "Blue", "available": 8}]}
        request = main.TicketAnswerRequest(conversation_id="conv", message_id="msg", platform="shopee", shop_id="111")
        event = normalize_chat_input({"message_type": "item", "item_id": "123", "text": "จอกี่นิ้ว"})
        message = {"event": event, "media": [], "created_timestamp": None}
        conversation = {"legacy_shop_name": "Shop A", "customer_id": "cust"}
        with patch.object(main, "_shop_context", return_value=(self.scope, self.knowledge)), \
             patch.object(main.legacy_data, "get_inbound_message", return_value=message), \
             patch.object(main.legacy_data, "get_conversation", return_value=conversation), \
             patch.object(main.legacy_data, "history_for_conversation", return_value=[]), \
             patch.object(main.legacy_data, "shop_alias_is_unique", return_value=True), \
             patch.object(main.legacy_data, "product_identities_by_ids", return_value={"123": mongo_card}), \
             patch.object(main, "ProductChatEngine", engine):
            body = main._answer_legacy_message(request)

        self.assertEqual(body["status"], "answered")
        self.assertEqual(engine.call_kwargs["card_context"], [
            {"item_id": "123", "name": "Acme Watch X", "product_name": "Acme Watch X"}
        ])
        self.assertEqual(engine.call_kwargs["commerce_lookup"]("product_info", ["Acme Watch X"],
                                                                 engine.call_kwargs["card_context"]), {})

    def test_botworker_card_bundle_fails_closed_if_any_id_lacks_kb_identity(self):
        engine = CapturingEngine
        request = main.TicketAnswerRequest(conversation_id="conv", message_id="msg", platform="shopee", shop_id="111")
        event = normalize_chat_input({"message_type": "bundle_message", "item_ids": ["123", "456"],
                                      "text": "ใช้คู่กันได้ไหม"})
        message = {"event": event, "media": [], "created_timestamp": None}
        conversation = {"legacy_shop_name": "Shop A", "customer_id": "cust"}
        with patch.object(main, "_shop_context", return_value=(self.scope, self.knowledge)), \
             patch.object(main.legacy_data, "get_inbound_message", return_value=message), \
             patch.object(main.legacy_data, "get_conversation", return_value=conversation), \
             patch.object(main.legacy_data, "history_for_conversation", return_value=[]), \
             patch.object(main.legacy_data, "shop_alias_is_unique", return_value=True), \
             patch.object(main.legacy_data, "product_identities_by_ids", return_value={
                 "123": {"item_id": "123", "name": "Acme Watch X"},
                 "456": {"item_id": "456", "name": "Unknown Charger Z"},
             }), patch.object(main, "ProductChatEngine", engine):
            body = main._answer_legacy_message(request)

        self.assertEqual(body["status"], "handoff_required")
        self.assertTrue(body["handoff_required"])
        self.assertEqual(body["handoff_reason"], "card_not_in_shop_kb")
        self.assertIn("KB", body["handoff_explanation"])
        self.assertEqual(body["unresolved_item_ids"], ["456"])
        self.assertFalse(engine.called)

    def test_botworker_order_status_lookup_is_scoped_to_conversation_customer_and_shop(self):
        engine = CapturingEngine
        request = main.TicketAnswerRequest(conversation_id="conv", message_id="msg", platform="shopee", shop_id="111")
        event = normalize_chat_input({"message_type": "text", "text": "ORDER-123 สถานะถึงไหนแล้ว"})
        message = {"event": event, "media": [], "created_timestamp": None}
        conversation = {"legacy_shop_name": "Shop A", "customer_id": "buyer-7"}
        with patch.object(main, "_shop_context", return_value=(self.scope, self.knowledge)), \
             patch.object(main.legacy_data, "get_inbound_message", return_value=message), \
             patch.object(main.legacy_data, "get_conversation", return_value=conversation), \
             patch.object(main.legacy_data, "history_for_conversation", return_value=[]), \
             patch.object(main.legacy_data, "shop_alias_is_unique", return_value=True), \
             patch.object(main.legacy_data, "list_customer_orders") as list_orders, \
             patch.object(main.legacy_data, "find_customer_order", return_value={"order_sn": "ORDER-123"}) as find_order, \
             patch.object(main, "_describe_legacy_media", return_value=[]), \
             patch.object(main, "ProductChatEngine", engine):
            main._answer_legacy_message(request)
            result = engine.call_kwargs["commerce_lookup"]("order_status", [], [], [])

        find_order.assert_called_once_with("buyer-7", "Shop A", "ORDER-123")
        list_orders.assert_not_called()
        self.assertEqual(result, {"orders": [{"order_sn": "ORDER-123"}]})

    def test_botworker_order_lookup_requests_clarification_for_multiple_unmatched_orders(self):
        engine = CapturingEngine
        request = main.TicketAnswerRequest(conversation_id="conv", message_id="msg", platform="shopee", shop_id="111")
        event = normalize_chat_input({"message_type": "text", "text": "ออเดอร์ที่สั่งส่งถึงไหนแล้ว"})
        message = {"event": event, "media": [], "created_timestamp": None}
        conversation = {"legacy_shop_name": "Shop A", "customer_id": "buyer-7"}
        orders = [{"order_sn": "ORDER-1", "items": []}, {"order_sn": "ORDER-2", "items": []}]
        with patch.object(main, "_shop_context", return_value=(self.scope, self.knowledge)), \
             patch.object(main.legacy_data, "get_inbound_message", return_value=message), \
             patch.object(main.legacy_data, "get_conversation", return_value=conversation), \
             patch.object(main.legacy_data, "history_for_conversation", return_value=[]), \
             patch.object(main.legacy_data, "shop_alias_is_unique", return_value=True), \
             patch.object(main.legacy_data, "list_customer_orders", return_value=orders) as list_orders, \
             patch.object(main, "_describe_legacy_media", return_value=[]), \
             patch.object(main, "ProductChatEngine", engine):
            main._answer_legacy_message(request)
            result = engine.call_kwargs["commerce_lookup"]("order_status", [], [], [])

        list_orders.assert_called_once_with("buyer-7", "Shop A", 20)
        self.assertEqual(result, {"orders": [], "needs_clarification": True})

    def test_botworker_troubleshooting_can_read_customer_orders_only_for_product_identity(self):
        engine = CapturingEngine
        request = main.TicketAnswerRequest(conversation_id="conv", message_id="msg", platform="shopee", shop_id="111")
        event = normalize_chat_input({"message_type": "text", "text": "จอมีปัญหาครับ"})
        message = {"event": event, "media": [], "created_timestamp": None}
        conversation = {"legacy_shop_name": "Shop A", "customer_id": "buyer-7"}
        orders = [{"order_sn": "ORDER-1", "items": [{"name": "Acme Watch X"}]}]
        with patch.object(main, "_shop_context", return_value=(self.scope, self.knowledge)), \
             patch.object(main.legacy_data, "get_inbound_message", return_value=message), \
             patch.object(main.legacy_data, "get_conversation", return_value=conversation), \
             patch.object(main.legacy_data, "history_for_conversation", return_value=[]), \
             patch.object(main.legacy_data, "shop_alias_is_unique", return_value=True), \
             patch.object(main.legacy_data, "list_customer_orders", return_value=orders) as list_orders, \
             patch.object(main, "_describe_legacy_media", return_value=[]), \
             patch.object(main, "ProductChatEngine", engine):
            main._answer_legacy_message(request)
            lookup = engine.call_kwargs["commerce_lookup"]
            identity = lookup("troubleshooting", [], [], [])
            facts = lookup("troubleshooting", ["Acme Watch X"], [], [])

        list_orders.assert_called_once_with("buyer-7", "Shop A", 20)
        self.assertEqual(identity, {"orders": orders})
        self.assertEqual(facts, {})


class CapturingEngine:
    called = False
    call_kwargs = {}

    def __init__(self, store):
        type(self).called = False
        type(self).call_kwargs = {}

    def answer(self, question, **kwargs):
        type(self).called = True
        type(self).call_kwargs = kwargs
        return {"status": "answered", "intent": "product_info", "answer": "คำตอบจาก KB",
                "sources": [{"file": "a.xlsx"}]}


if __name__ == "__main__":
    unittest.main()
