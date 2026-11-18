import unittest

from backend.app.engine_v2 import GeminiAnswerer, ProductChatEngine


class FakeStore:
    def __init__(self, keys=None):
        self.keys = keys if keys is not None else ["bad", "good"]
        self.index = 0

    def knowledge(self):
        return {
            "products": [{"name": "Black Shark T11", "description": "Bluetooth : 5.3\nGame Mode : 45 ms", "source": {"file": "products.xlsx"}}],
            "faqs": [{"model_name": "Black Shark T11", "question": "ชาร์จไม่เข้า", "answer": "ลองเปลี่ยนสายชาร์จ", "source": {"file": "faq.xlsx"}}],
            "support_models": ["Black Shark T11"],
        }

    def list_keys(self):
        return [{"id": i} for i in range(len(self.keys))]

    def next_key(self):
        if not self.keys:
            return None, None, 0
        i = self.index % len(self.keys)
        self.index += 1
        return self.keys[i], i, len(self.keys)


class GroundedBotTests(unittest.TestCase):
    def make_engine(self, store=None, generate=None):
        store = store or FakeStore()
        answerer = GeminiAnswerer(store, generate or (lambda key, prompt: "Bluetooth 5.3"))
        return ProductChatEngine(store=store, answerer=answerer)

    def test_spec_uses_gemini_with_selected_source(self):
        prompts = []
        engine = self.make_engine(generate=lambda key, prompt: prompts.append(prompt) or "รองรับ Bluetooth 5.3")
        result = engine.answer("Bluetooth เวอร์ชันอะไร", model_name="Black Shark T11")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["sources"][0]["file"], "products.xlsx")
        self.assertIn("Bluetooth : 5.3", prompts[0])

    def test_troubleshooting_uses_same_bot(self):
        engine = self.make_engine(generate=lambda key, prompt: "ลองเปลี่ยนสายชาร์จ")
        result = engine.answer("ชาร์จไม่เข้า", model_name="Black Shark T11")
        self.assertEqual(result["intent"], "troubleshooting")
        self.assertEqual(result["sources"][0]["file"], "faq.xlsx")

    def test_sensitive_question_never_calls_llm(self):
        engine = self.make_engine(generate=lambda key, prompt: self.fail("LLM called"))
        for question in ("ขอคืนเงิน", "เคลมประกัน", "ใบกำกับภาษี"):
            self.assertEqual(engine.answer(question, model_name="Black Shark T11")["status"], "handoff_mock")

    def test_unknown_model_handoffs(self):
        result = self.make_engine().answer("สเปกอะไร", model_name="Unknown")
        self.assertEqual(result["reason"], "model_not_found")

    def test_missing_key_handoffs(self):
        store = FakeStore([])
        result = self.make_engine(store).answer("Bluetooth เวอร์ชันอะไร", model_name="Black Shark T11")
        self.assertEqual(result["reason"], "llm_key_missing")

    def test_failed_key_retries_next(self):
        def generate(key, prompt):
            if key == "bad":
                raise RuntimeError("quota")
            return "Bluetooth 5.3"
        result = self.make_engine(generate=generate).answer("Bluetooth เวอร์ชันอะไร", model_name="Black Shark T11")
        self.assertEqual(result["status"], "answered")

    def test_new_number_is_rejected(self):
        result = self.make_engine(generate=lambda key, prompt: "Bluetooth 9.9").answer("Bluetooth เวอร์ชันอะไร", model_name="Black Shark T11")
        self.assertEqual(result["reason"], "llm_unsupported_number")


if __name__ == "__main__":
    unittest.main()
