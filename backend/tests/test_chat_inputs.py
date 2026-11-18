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


if __name__ == "__main__":
    unittest.main()
