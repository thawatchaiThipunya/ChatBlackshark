import json
import unittest

from backend.app.engine_v2 import GeminiAnswerer, ProductChatEngine, supported_wording


class FakeStore:
    def __init__(self, products, faqs=(), keys=("test-key",)):
        self.data = {"products": products, "faqs": list(faqs), "support_models": []}
        self.keys = keys

    def knowledge(self):
        return self.data

    def next_key(self):
        return (self.keys[0], 0, len(self.keys)) if self.keys else (None, None, 0)


class IntentFlowTests(unittest.TestCase):
    def engine(self, products, generate, faqs=()):
        store = FakeStore(products, faqs)
        return ProductChatEngine(store=store, answerer=GeminiAnswerer(store, generate))

    def test_intent_extracts_needs_and_product_from_this_chat(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            return json.dumps({"intent": "compatibility", "topic": "swimming", "needs": ["can swim"],
                               "product_names": ["Black Shark GS3 Ultra"], "unresolved": False})
        store = FakeStore([{"name": "Black Shark GS3 Ultra", "description": "5ATM", "source": {}}])
        plan, reason = GeminiAnswerer(store, generate).extract_intent(
            "ใส่ลงสระได้ปะ", [{"role": "assistant", "content": "รุ่นนี้ครับ", "product_name": "Black Shark GS3 Ultra"}],
            ["Black Shark GS3 Ultra"])
        self.assertEqual(reason, "intent_resolved")
        self.assertEqual(plan["needs"], ["can swim"])
        self.assertEqual(plan["product_names"], ["Black Shark GS3 Ultra"])
        self.assertIn("Black Shark GS3 Ultra", prompts[0])

    def test_swimming_followup_uses_full_product_evidence_and_direct_answer(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "compatibility", "topic": "swimming", "needs": ["swimming suitability"],
                                   "product_names": ["Black Shark GS3 Ultra"], "unresolved": False})
            return "ใส่ว่ายน้ำได้ครับ GS3 Ultra มีโหมดว่ายน้ำและกันน้ำ 5ATM ตามข้อมูลร้าน"
        engine = self.engine([{"name": "Black Shark GS3 Ultra", "description": "กันน้ำ 5ATM\nโหมดกีฬา: ว่ายน้ำ",
                               "source": {"file": "watch.xlsx"}}], generate)
        result = engine.answer("ใส่ลงสระได้ปะ", history=[{"role": "assistant", "content": "GS3 Ultra ครับ",
                                                  "product_name": "Black Shark GS3 Ultra"}])
        self.assertEqual(result["intent"], "compatibility")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["intent_analysis"]["needs"], ["swimming suitability"])
        self.assertIn("โหมดกีฬา: ว่ายน้ำ", prompts[-1])
        self.assertIn("ได้ครับ", result["answer"])

    def test_comparison_followup_uses_two_models_and_answered_criterion(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "comparison", "topic": "battery life", "needs": ["which lasts longer"],
                                   "product_names": ["Black Shark GS3", "Black Shark GS3 Ultra"], "unresolved": False})
            return "ถ้าเน้นแบต GS3 อึดกว่าครับ อยู่ได้สูงสุด 21 วัน ส่วน GS3 Ultra สูงสุด 18 วัน"
        engine = self.engine([
            {"name": "Black Shark GS3", "description": "แบตเตอรี่สูงสุด 21 วัน", "source": {"file": "watch.xlsx"}},
            {"name": "Black Shark GS3 Ultra", "description": "แบตเตอรี่สูงสุด 18 วัน", "source": {"file": "watch.xlsx"}},
        ], generate)
        result = engine.answer("ตัวไหนอึดกว่ากัน", history=[{"role": "user", "content": "GS3 เทียบ GS3 Ultra"}])
        self.assertEqual(result["intent"], "comparison")
        self.assertEqual(result["status"], "answered")
        self.assertIn("แบตเตอรี่สูงสุด 21 วัน", prompts[-1])
        self.assertIn("แบตเตอรี่สูงสุด 18 วัน", prompts[-1])

    def test_search_checks_all_needs_against_catalog_before_answering(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "product_search", "topic": "exercise watch",
                                   "needs": ["GPS", "Strava", "blood pressure"], "product_names": [], "unresolved": False})
            if "Find products in this one store" in prompt:
                return json.dumps({"product_names": ["Black Shark GS3"]})
            return "แนะนำ GS3 ครับ มี GPS และต่อ Strava ได้ แต่ไม่รองรับการวัดความดัน"
        engine = self.engine([
            {"name": "Black Shark GS3", "description": "GPS: มี\nStrava: รองรับ\nวัดความดัน: ไม่รองรับ", "source": {}},
            {"name": "Black Shark T11", "description": "หูฟังไร้สาย Bluetooth 5.3", "source": {}},
        ], generate)
        result = engine.answer("อยากได้ไว้ซ้อมวิ่ง เห็นเส้นทาง ต่อแอปวิ่ง แล้ววัดความดันด้วย")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["product_name"], "Black Shark GS3")
        self.assertEqual(result["intent_analysis"]["needs"], ["GPS", "Strava", "blood pressure"])
        self.assertIn("ไม่รองรับ", result["answer"])
        self.assertEqual(len(prompts), 3)

    def test_troubleshooting_chooses_faq_from_product_context(self):
        def generate(key, prompt):
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "troubleshooting", "topic": "charging issue", "needs": ["fix charging"],
                                   "product_names": ["Black Shark GS3"], "unresolved": False})
            if "Select the single FAQ" in prompt:
                return '{"index":0}'
            return "ลองเช็ดขั้วชาร์จให้แห้งก่อนครับ แล้วทดสอบชาร์จอีกครั้ง"
        engine = self.engine([{"name": "Black Shark GS3", "description": "GPS: มี", "source": {}}], generate,
                             [{"model_name": "Black Shark GS3", "question": "ชาร์จไม่เข้า",
                               "answer": "เช็ดขั้วชาร์จให้แห้งแล้วทดสอบชาร์จ", "source": {"file": "faq.xlsx"}}])
        result = engine.answer("เสียบสายแล้วมันไม่ขึ้นอะไรเลย", history=[{"role": "assistant", "content": "GS3 ครับ",
                                                                     "product_name": "Black Shark GS3"}])
        self.assertEqual(result["intent"], "troubleshooting")
        self.assertEqual(result["matched_question"], "ชาร์จไม่เข้า")
        self.assertEqual(result["sources"][0]["file"], "faq.xlsx")

    def test_long_faq_model_name_stays_with_the_most_specific_product(self):
        selected_options = []
        def generate(key, prompt):
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "troubleshooting", "topic": "charging", "needs": ["fix charging"],
                                   "product_names": ["Black Shark S3 Classic"], "unresolved": False})
            if "Select the single FAQ" in prompt:
                selected_options.append(prompt)
                return '{"index":0}'
            return "ลองทำความสะอาดขั้วชาร์จของ S3 Classic ครับ"
        engine = self.engine([
            {"name": "Black Shark S3", "description": "GPS", "source": {}},
            {"name": "Black Shark S3 Classic", "description": "3ATM", "source": {}},
        ], generate, [
            {"model_name": "Black Shark S3 Smart Watch", "question": "ชาร์จไม่เข้า S3", "answer": "รีสตาร์ท S3", "source": {}},
            {"model_name": "Black Shark S3 Classic Smart Watch", "question": "ชาร์จไม่เข้า Classic",
             "answer": "ทำความสะอาดขั้วชาร์จของ S3 Classic", "source": {}},
        ])
        result = engine.answer("รุ่นคลาสสิกชาร์จไม่ขึ้น")
        self.assertEqual(result["matched_question"], "ชาร์จไม่เข้า Classic")
        self.assertNotIn("ชาร์จไม่เข้า S3\"", selected_options[0])

    def test_image_question_uses_vision_description_as_source(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "image_understanding", "topic": "screen damage",
                                   "needs": ["describe screen"], "product_names": [], "unresolved": False})
            return "จากภาพ หน้าจอมีเส้นสีขาวพาดอยู่ครับ"
        engine = self.engine([], generate)
        result = engine.answer("จอเป็นอะไรครับ", vision_evidence=["หน้าจอนาฬิกามีเส้นสีขาวพาด"])
        self.assertEqual(result["intent"], "image_understanding")
        self.assertEqual(result["status"], "answered")
        self.assertIn("หน้าจอนาฬิกามีเส้นสีขาวพาด", prompts[-1])

    def test_conflicting_source_values_are_available_to_answer_stage(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "product_info", "topic": "refresh rate", "needs": ["display refresh rate"],
                                   "product_names": ["Black Shark GS3 Ultra"], "unresolved": False})
            return "ข้อมูล GS3 Ultra ขัดกันครับ จุดหนึ่งบอก 60Hz แต่อีกจุดบอก 30Hz จึงยังยืนยันค่าไม่ได้"
        engine = self.engine([{"name": "Black Shark GS3 Ultra",
                               "description": "จอ Refresh Rate 60Hz\nHz ของจอ: 30Hz", "source": {}}], generate)
        result = engine.answer("จอมันกี่เฮิรตซ์นะ")
        self.assertEqual(result["status"], "answered")
        self.assertIn("60Hz", prompts[-1])
        self.assertIn("30Hz", prompts[-1])
        self.assertIn("ขัดกัน", result["answer"])

    def test_natural_thai_wording_is_not_rejected_for_missing_exact_words(self):
        self.assertTrue(supported_wording("ใส่ลงสระได้ครับ มีโหมดว่ายน้ำ", "โหมดกีฬา: ว่ายน้ำ"))
        self.assertFalse(supported_wording("Gemini API key: AIza123456789012345678901234567890", "สินค้า"))


if __name__ == "__main__":
    unittest.main()
