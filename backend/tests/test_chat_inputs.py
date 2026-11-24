import unittest

from backend.app.chat_inputs import normalize_chat_input


class ChatInputTests(unittest.TestCase):
    def test_extracts_multiple_item_ids_from_plain_text(self):
        event = normalize_chat_input(None, "46867428860\n56067346193")

        self.assertEqual(event.message_type, "item")
        self.assertEqual(event.item_ids, ("46867428860", "56067346193"))

    def test_extracts_multiple_ids_from_item_id_field(self):
        event = normalize_chat_input(
            {"type": "item", "item_id": "46867428860, 56067346193"}
        )

        self.assertEqual(event.message_type, "item")
        self.assertEqual(event.item_ids, ("46867428860", "56067346193"))

    def test_does_not_extract_numbers_embedded_in_a_question(self):
        event = normalize_chat_input(None, "GS3 Ultra กันน้ำ 5ATM ไหม")

        self.assertEqual(event.message_type, "text")
        self.assertEqual(event.item_ids, ())

    def test_keeps_ten_media_items_for_two_vision_batches(self):
        event = normalize_chat_input({"type": "text", "images": [f"https://cf.shopee.co.th/{i}.jpg" for i in range(10)]})

        self.assertEqual(len(event.media), 10)

    def test_reads_order_number_from_nested_shopee_source_content(self):
        event = normalize_chat_input({"message_type": "order", "content": {
            "source_content": {"order_sn": "240101ABCD1234"}}})

        self.assertEqual(event.order_sn, "240101ABCD1234")

    def test_normalizes_shopee_envelope_for_variation_and_order_cards(self):
        variation = normalize_chat_input({"data": {"content": {
            "message_type": "variation_card",
            "content": {"product_id": 56067346193, "shop_id": "shop-1"},
        }}})
        order = normalize_chat_input({"data": {"content": {
            "message_type": "order",
            "content": {"order_sn": "251212632RB8FT"},
        }}})

        self.assertEqual(variation.message_type, "variation_card")
        self.assertEqual(variation.item_ids, ("56067346193",))
        self.assertEqual(order.message_type, "order")
        self.assertEqual(order.order_sn, "251212632RB8FT")

    def test_keeps_rating_card_as_a_known_non_text_event(self):
        event = normalize_chat_input({"data": {"content": {
            "message_type": "rating_card", "content": {"rating": 5},
        }}}, "[rating_card]")

        self.assertEqual(event.message_type, "rating_card")
        self.assertEqual(event.text, "")


if __name__ == "__main__":
    unittest.main()
