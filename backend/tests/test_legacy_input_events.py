import unittest
from unittest.mock import patch

from backend.app import main
from backend.app.chat_inputs import NormalizedChatInput


class LegacyInputEventTests(unittest.TestCase):
    def test_generated_shopee_sticker_media_host_is_allowed_for_vision_download(self):
        self.assertTrue(main._trusted_media_url(
            "https://deo.shopeemobile.com/shopee/shopee-sticker-live-th/packs/p/1@1x.png"))

    def test_rating_card_is_not_sent_to_the_answer_model(self):
        scope = {"platform": "shopee", "shop_id": "shop-1", "shop_name": "Shop A"}
        knowledge = {"products": [{"name": "Model A", "description": "spec"}], "faqs": []}
        event = NormalizedChatInput(message_type="rating_card")
        message = {"event": event, "media": [], "created_timestamp": None}
        conversation = {"legacy_shop_name": "Shop A", "customer_id": "customer-1"}
        model_questions = []

        class TrackingEngine:
            def __init__(self, store):
                pass

            def answer(self, question, **_kwargs):
                model_questions.append(question)
                return {"status": "answered", "answer": "ตอบจากโมเดล"}

        request = main.TicketAnswerRequest(conversation_id="conv-1", message_id="msg-1",
                                           shop_id="shop-1", platform="shopee")
        with patch.object(main, "_shop_context", return_value=(scope, knowledge)), \
             patch.object(main.legacy_data, "get_inbound_message", return_value=message), \
             patch.object(main.legacy_data, "get_conversation", return_value=conversation), \
             patch.object(main.legacy_data, "history_for_conversation", return_value=[]), \
             patch.object(main, "_describe_legacy_media", return_value=[]), \
             patch.object(main, "ProductChatEngine", TrackingEngine), \
             patch.object(main.legacy_data, "shop_alias_is_unique", return_value=False):
            result = main._answer_legacy_message(request)

        self.assertEqual(model_questions, [])
        self.assertEqual(result["reason"], "rating_card_received")
        self.assertFalse(result["handoff_required"])

    def test_faq_liveagent_returns_handoff_flag_without_calling_the_answer_model(self):
        scope = {"platform": "shopee", "shop_id": "shop-1", "shop_name": "Shop A"}
        event = NormalizedChatInput(message_type="faq_liveagent", text="ขอคุยกับเจ้าหน้าที่")
        message = {"event": event, "media": [], "created_timestamp": None}
        conversation = {"legacy_shop_name": "Shop A", "customer_id": "customer-1"}

        request = main.TicketAnswerRequest(conversation_id="conv-1", message_id="msg-1",
                                           shop_id="shop-1", platform="shopee")
        with patch.object(main, "_shop_context", return_value=(scope, {"products": [], "faqs": []})), \
             patch.object(main.legacy_data, "get_inbound_message", return_value=message), \
             patch.object(main.legacy_data, "get_conversation", return_value=conversation), \
             patch.object(main, "ProductChatEngine", side_effect=AssertionError("must not call the answer model")):
            result = main._answer_legacy_message(request)

        self.assertTrue(result["handoff_required"])
        self.assertEqual(result["status"], "handoff_required")
        self.assertEqual(result["handoff_reason"], "customer_requested_agent")
        self.assertIn("ขอคุย", result["handoff_explanation"])

    def test_temporary_provider_failure_returns_handoff_flag_and_readable_reason(self):
        scope = {"platform": "shopee", "shop_id": "shop-1", "shop_name": "Shop A"}
        event = NormalizedChatInput(message_type="text", text="ถามสเปกสินค้า")
        message = {"event": event, "media": [], "created_timestamp": None}
        conversation = {"legacy_shop_name": "Shop A", "customer_id": "customer-1"}

        class FailedEngine:
            def __init__(self, store):
                pass

            def answer(self, question, **_kwargs):
                return {"status": "temporarily_unavailable", "reason": "llm_rate_limited",
                        "answer": "ระบบ AI ขัดข้องชั่วคราว"}

        request = main.TicketAnswerRequest(conversation_id="conv-1", message_id="msg-1",
                                           shop_id="shop-1", platform="shopee")
        with patch.object(main, "_shop_context", return_value=(scope, {"products": [], "faqs": []})), \
             patch.object(main.legacy_data, "get_inbound_message", return_value=message), \
             patch.object(main.legacy_data, "get_conversation", return_value=conversation), \
             patch.object(main.legacy_data, "history_for_conversation", return_value=[]), \
             patch.object(main, "_describe_legacy_media", return_value=[]), \
             patch.object(main, "ProductChatEngine", FailedEngine), \
             patch.object(main.legacy_data, "shop_alias_is_unique", return_value=False):
            result = main._answer_legacy_message(request)

        self.assertTrue(result["handoff_required"])
        self.assertEqual(result["handoff_reason"], "llm_rate_limited")
        self.assertIn("จำกัด", result["handoff_explanation"])

    def test_first_clarification_does_not_handoff(self):
        scope = {"platform": "shopee", "shop_id": "shop-1", "shop_name": "Shop A"}
        event = NormalizedChatInput(message_type="text", text="หมายถึงนาฬิกาที่ซื้อไปครับ")
        message = {"event": event, "media": [], "created_timestamp": None}
        conversation = {"legacy_shop_name": "Shop A", "customer_id": "customer-1"}

        class ClarifyingEngine:
            def __init__(self, store):
                pass

            def answer(self, question, **_kwargs):
                return {"status": "need_model", "reason": "order_product_ambiguous",
                        "answer": "ช่วยบอกรุ่นหรือเลขคำสั่งซื้ออีกนิดครับ"}

        request = main.TicketAnswerRequest(conversation_id="conv-1", message_id="msg-1",
                                           shop_id="shop-1", platform="shopee")
        with patch.object(main, "_shop_context", return_value=(scope, {"products": [], "faqs": []})), \
             patch.object(main.legacy_data, "get_inbound_message", return_value=message), \
             patch.object(main.legacy_data, "get_conversation", return_value=conversation), \
             patch.object(main.legacy_data, "history_for_conversation", return_value=[]), \
             patch.object(main, "_describe_legacy_media", return_value=[]), \
             patch.object(main, "ProductChatEngine", ClarifyingEngine), \
             patch.object(main.legacy_data, "shop_alias_is_unique", return_value=False):
            result = main._answer_legacy_message(request)

        self.assertEqual(result["status"], "need_model")
        self.assertFalse(result["handoff_required"])
        self.assertIsNone(result["handoff_reason"])


if __name__ == "__main__":
    unittest.main()
