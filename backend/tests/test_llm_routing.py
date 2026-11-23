import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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
    def test_gemini_attempts_each_distinct_key_until_last_succeeds(self):
        store = FakeStore("gemini")
        store.key_cycle = lambda: [(f"key-{i}", i) for i in range(10)]
        seen = []
        def generate(key, prompt):
            seen.append(key)
            if key != "key-9":
                raise RuntimeError("provider failed")
            return "ตอบได้"

        result, reason = GeminiAnswerer(store, generate)._call("prompt", "answer")

        self.assertEqual((result, reason), ("ตอบได้", "llm_grounded"))
        self.assertEqual(seen, [f"key-{i}" for i in range(10)])

    @patch("backend.app.engine_v2.openrouter_complete")
    def test_openrouter_error_stops_after_one_call(self, complete):
        complete.side_effect = RuntimeError("provider failed")

        result, reason = GeminiAnswerer(FakeStore())._call("prompt", "answer")

        self.assertEqual((result, reason), (None, "llm_unavailable"))
        self.assertEqual(complete.call_count, 1)

    @patch("backend.app.engine_v2.openrouter_complete", return_value='{"intent":"smalltalk","needs":[],"product_names":[]}')
    def test_openrouter_intent_uses_own_model_and_one_key(self, complete):
        store = FakeStore()
        result, _ = GeminiAnswerer(store).extract_intent("สวัสดี", [], [])
        self.assertEqual(result["intent"], "smalltalk")
        self.assertEqual(complete.call_args.args[:2], ("or-secret", "vendor/intent"))
        self.assertEqual(store.key_calls, 0)

    @patch("backend.app.engine_v2.openrouter_complete", side_effect=["สวัสดีครับ", '{"answer":"สวัสดีครับ"}'])
    def test_openrouter_answer_uses_answer_model(self, complete):
        answer, _ = GeminiAnswerer(FakeStore()).answer("สวัสดี", "คำทักทาย", intent="smalltalk")
        self.assertEqual(answer, "สวัสดีครับ")
        self.assertEqual([call.args[1] for call in complete.call_args_list], ["vendor/answer", "vendor/answer"])

    def test_gemini_uses_configured_model(self):
        store = FakeStore("gemini")
        with patch.object(GeminiAnswerer, "_generate", return_value="{}") as generate:
            GeminiAnswerer(store)._call("prompt", "intent")
        self.assertEqual(generate.call_args.args, ("gemini-secret", "prompt", "gemini-intent"))

    def test_gemini_key_rotation_reports_each_real_generation_attempt(self):
        store = FakeStore("gemini")
        store.key_cycle = lambda: [("first", 1), ("second", 2)]
        client = MagicMock()
        client.__enter__.return_value = client
        responses = iter([RuntimeError("provider failed"), SimpleNamespace(text="ตอบได้")])

        def generate(*args, **kwargs):
            response = next(responses)
            if isinstance(response, BaseException):
                raise response
            return response

        with patch("google.genai.Client", return_value=client), patch(
            "backend.app.engine_v2.call_gemini", side_effect=generate
        ) as call_gemini:
            result, reason = GeminiAnswerer(store)._call("prompt", "answer")

        self.assertEqual((result, reason), ("ตอบได้", "llm_grounded"))
        self.assertEqual(call_gemini.call_count, 2)
        self.assertEqual([call.kwargs["attempt"] for call in call_gemini.call_args_list], [1, 2])
        self.assertEqual([call.kwargs["source"] for call in call_gemini.call_args_list],
                         ["chat.answer", "chat.answer"])

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
