import json
import unittest
from unittest.mock import patch

from backend.app.engine_v2 import GeminiAnswerer
from backend.app.vision import describe_openrouter_batch


class FakeStore:
    def __init__(self, provider="openrouter"):
        self.provider = provider
        self.key_calls = 0

    def runtime_config(self):
        return {
            "provider": self.provider, "openrouter_key": "or-secret",
            "openrouter_intent_model": "vendor/intent",
            "openrouter_answer_model": "vendor/answer",
            "openrouter_vision_model": "vendor/vision",
            "gemini_intent_model": "gemini-intent",
            "gemini_answer_model": "gemini-answer",
        }

    def next_key(self):
        self.key_calls += 1
        return "gemini-secret", 1, 1


class RoutingTests(unittest.TestCase):
    @patch("backend.app.engine_v2.openrouter_complete", return_value='{"intent":"smalltalk","needs":[],"product_names":[]}')
    def test_openrouter_intent_uses_own_model_and_one_key(self, complete):
        store = FakeStore()
        result, _ = GeminiAnswerer(store).extract_intent("สวัสดี", [], [])
        self.assertEqual(result["intent"], "smalltalk")
        self.assertEqual(complete.call_args.args[:2], ("or-secret", "vendor/intent"))
        self.assertEqual(store.key_calls, 0)

    @patch("backend.app.engine_v2.openrouter_complete", return_value="สวัสดีครับ")
    def test_openrouter_answer_uses_answer_model(self, complete):
        answer, _ = GeminiAnswerer(FakeStore()).answer("สวัสดี", "คำทักทาย", intent="smalltalk")
        self.assertEqual(answer, "สวัสดีครับ")
        self.assertEqual(complete.call_args.args[1], "vendor/answer")

    def test_gemini_uses_configured_model(self):
        store = FakeStore("gemini")
        with patch.object(GeminiAnswerer, "_generate", return_value="{}") as generate:
            GeminiAnswerer(store)._call("prompt", "intent")
        self.assertEqual(generate.call_args.args, ("gemini-secret", "prompt", "gemini-intent"))

    @patch("backend.app.vision.openrouter_complete")
    def test_five_images_use_one_openrouter_call_and_preserve_order(self, complete):
        complete.return_value = json.dumps({"descriptions": [f"ภาพ {i}" for i in range(5)]})
        results = describe_openrouter_batch([(bytes([i]), "image/png") for i in range(5)], "ช่วยดู", "or-secret", "vendor/vision")
        self.assertEqual([result.description for result in results], [f"ภาพ {i}" for i in range(5)])
        self.assertEqual(complete.call_count, 1)
        self.assertEqual(complete.call_args.args[1], "vendor/vision")
        self.assertEqual(len(complete.call_args.args[2]), 6)


if __name__ == "__main__":
    unittest.main()
