import unittest

from backend.app.history_context import recent_context


class HistoryContextTests(unittest.TestCase):
    def test_five_pairs_and_two_customer_scopes(self):
        rows = []
        for index in range(7):
            rows.extend([
                {"role": "user", "content": f"question {index}", "payload": {}},
                {"role": "assistant", "content": f"answer {index}", "payload": {
                    "intent_analysis": {"product_names": [f"model {index}"]}}},
            ])
        rows[-1]["payload"]["intent_analysis"] = {
            "product_names": [f"watch {i}" for i in range(14)],
            "category": "smart watch", "list_all": True}
        history, scopes = recent_context(rows)
        self.assertEqual(len(history), 10)
        self.assertEqual(history[0]["content"], "question 2")
        self.assertEqual(scopes, [
            {"question": "question 5", "product_names": ["model 5"]},
            {"question": "question 6", "category": "smart watch"},
        ])

    def test_image_description_is_included_without_image_url(self):
        rows = [{"role": "user", "content": "ดูรูปนี้", "payload": {
            "vision_descriptions": ["นาฬิกาหน้าจอแตก"], "image_url": "https://private.example/image"}},
            {"role": "assistant", "content": "ลองรีสตาร์ท", "payload": {}}]
        history, _ = recent_context(rows)
        self.assertEqual(history[0]["vision_descriptions"], ["นาฬิกาหน้าจอแตก"])
        self.assertNotIn("image_url", str(history))

    def test_long_answer_survives_old_700_character_limit(self):
        history, _ = recent_context([
            {"role": "user", "content": "สเปค", "payload": {}},
            {"role": "assistant", "content": "a" * 1200, "payload": {}},
        ])
        self.assertEqual(len(history[1]["content"]), 1200)


if __name__ == "__main__":
    unittest.main()
