import unittest

from backend.app.engine_v2 import GeminiAnswerer


class PersonaStore:
    def __init__(self, persona):
        self.saved_persona = persona

    def persona(self):
        return self.saved_persona

    def runtime_config(self):
        return {"provider": "gemini", "gemini_answer_model": "gemini-test"}

    def next_key(self):
        return "test-key", 1, 1


class PersonaPromptTests(unittest.TestCase):
    def test_answer_uses_saved_persona_without_changing_facts(self):
        prompts = []
        answerer = GeminiAnswerer(PersonaStore({
            "length": "short", "tone": "friendly", "voice": "female",
        }), lambda key, prompt: prompts.append(prompt) or "GS3 ใช้ได้ 21 วันค่ะ")

        answer, reason = answerer.answer("แบตกี่วัน", "GS3 แบตสูงสุด 21 วัน", "GS3")

        self.assertEqual(reason, "llm_grounded")
        self.assertEqual(answer, "GS3 ใช้ได้ 21 วันค่ะ")
        self.assertIn("PERSONA:", prompts[0])
        self.assertIn("ตอบกระชับ", prompts[0])
        self.assertIn("เป็นกันเอง", prompts[0])
        self.assertIn("ค่ะ/คะ", prompts[0])
        self.assertIn("SOURCE:\nGS3 แบตสูงสุด 21 วัน", prompts[0])

    def test_intent_prompt_does_not_include_persona(self):
        prompts = []
        answerer = GeminiAnswerer(PersonaStore({
            "length": "long", "tone": "very_polite", "voice": "male",
        }), lambda key, prompt: prompts.append(prompt) or '{"intent":"smalltalk","needs":[],"product_names":[]}')

        answerer.extract_intent("สวัสดี", [], [])

        self.assertNotIn("PERSONA:", prompts[0])


if __name__ == "__main__":
    unittest.main()
