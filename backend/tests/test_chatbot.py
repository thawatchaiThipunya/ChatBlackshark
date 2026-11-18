import unittest

from backend.app.engine import ProductChatEngine
from backend.app.main import app


class ProductChatRegressionTests(unittest.TestCase):
    def setUp(self):
        self.engine = ProductChatEngine()

    def test_payment_warranty_and_invoice_are_mock_handoffs(self):
        questions = ["ขอคืนเงินได้ไหม", "ขอเคลมประกัน", "ขอใบกำกับภาษี"]
        for question in questions:
            with self.subTest(question=question):
                result = self.engine.answer(question, model_name="Black Shark S1 Pro Smart Watch")
                self.assertEqual(result["status"], "handoff_mock")
                self.assertTrue(result["handoff_mock"])
                self.assertFalse(result["handoff_sent"])
                self.assertEqual(result["answer"], "ขอเช็กกับเจ้าหน้าที่ก่อน แล้วเดี๋ยวส่งกลับนะครับ")

    def test_unknown_product_uses_mock_handoff_without_source(self):
        result = self.engine.answer("มีสเปกอะไรบ้าง", product_name="Black Shark Not In Catalog")
        self.assertEqual(result["status"], "handoff_mock")
        self.assertTrue(result["handoff_mock"])
        self.assertFalse(result["handoff_sent"])
        self.assertEqual(result["reason"], "product_not_found")
        self.assertEqual(result["sources"], [])

    def test_local_dev_origins_are_allowed_by_cors(self):
        origins = app.user_middleware[0].kwargs["allow_origins"]
        self.assertIn("http://localhost:3000", origins)
        self.assertIn("http://127.0.0.1:3000", origins)

    def test_missing_model_asks_for_exact_model(self):
        result = self.engine.answer("นาฬิกาชาร์จไม่เข้า")
        self.assertEqual(result["status"], "need_model")
        self.assertEqual(result["sources"], [])

    def test_model_outside_source_files_uses_mock_handoff(self):
        result = self.engine.answer("ชาร์จไม่เข้า", model_name="Black Shark Unknown")
        self.assertEqual(result["status"], "handoff_mock")
        self.assertEqual(result["reason"], "model_not_found")

    def test_known_model_faq_returns_source_answer_and_cell(self):
        result = self.engine.answer("ไมโครโฟนไม่มีเสียง", model_name="Black Shark S1 Pro Smart Watch")
        self.assertEqual(result["status"], "answered")
        self.assertTrue(result["answer"])
        self.assertEqual(result["sources"][0]["file"], "วิธีแก้ปัญหารายรุ่น-Black Shark-เฉพาะติ๊กส่งลูกค้าได้-20261118-0114.xlsx")
        self.assertTrue(result["sources"][0]["cell"].startswith("C"))

    def test_exact_source_question_wins_over_fuzzy_tie(self):
        question = next(
            item["question"] for item in self.engine.data["faqs"]
            if item["model"] == "Black Shark A3 Smart Watch"
            and item["source"]["cell"] == "C25"
            and "หน้าจอสว่างวาบ" in item["question"]
        )
        result = self.engine.answer(question, model_name="Black Shark A3 Smart Watch")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["matched_question"], question)

    def test_model_name_alone_is_not_a_troubleshooting_question(self):
        result = self.engine.answer("Black Shark A3", model_name="Black Shark A3 Smart Watch")
        self.assertEqual(result["status"], "handoff_mock")
        self.assertEqual(result["sources"], [])

    def test_no_matching_source_does_not_invent_answer(self):
        result = self.engine.answer("รองรับการจ่ายผ่านบัตรเครดิตหรือไม่", model_name="Black Shark S1 Pro Smart Watch")
        self.assertEqual(result["status"], "handoff_mock")
        self.assertEqual(result["sources"], [])

    def test_product_spec_answer_is_a_verbatim_source_excerpt(self):
        result = self.engine.answer("Game Mode", product_name="Black Shark T11")
        self.assertEqual(result["status"], "answered")
        self.assertIn("Game Mode", result["answer"])
        self.assertEqual(result["sources"][0]["file"], "สินค้า - Black Shark Thailand (sellcenter).xlsx")

    def test_spoken_spec_question_finds_source_line(self):
        result = self.engine.answer("รองรับ Bluetooth เวอร์ชันอะไร", product_name="Black Shark T11")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["answer"], "Bluetooth : 5.3")
        self.assertEqual(result["sources"][0]["file"], "สินค้า - Black Shark Thailand (sellcenter).xlsx")

    def test_broad_spec_question_returns_source_details(self):
        result = self.engine.answer("สเปกมีอะไรบ้าง", product_name="Black Shark T11")
        self.assertEqual(result["status"], "answered")
        self.assertIn("Dynamic Driver: 13 มม.", result["answer"])
        self.assertEqual(result["sources"][0]["file"], "สินค้า - Black Shark Thailand (sellcenter).xlsx")


if __name__ == "__main__":
    unittest.main()
