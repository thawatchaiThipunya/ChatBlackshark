import io
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

from backend.app.ai_usage import call_gemini, reset_usage_attempt, set_usage_attempt, set_usage_reference, reset_usage_reference
from backend.app.openrouter import complete


class FakeResponse(io.BytesIO):
    status = 200

    def __init__(self, payload):
        super().__init__(json.dumps(payload).encode())


class AiUsageReportingTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "AI_USAGE_HUB_URL": "https://digital.in.th/",
            "AI_USAGE_HUB_TOKEN": "svc_test_token",
            "VERCEL_ENV": "production",
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.posts = []
        self.provider_requests = []

    def _inline_reporter(self, provider_payload):
        def urlopen(request, timeout):
            if request.full_url == "https://openrouter.ai/api/v1/chat/completions":
                self.provider_requests.append(request)
                return FakeResponse(provider_payload)
            if request.full_url == "https://digital.in.th/internal/ai-usage/logs":
                self.posts.append((request, json.loads(request.data)))
                return FakeResponse({"success": True})
            self.fail(f"unexpected URL: {request.full_url}")

        def submit_inline(executor, function, *args, **kwargs):
            function(*args, **kwargs)

        return patch("urllib.request.urlopen", side_effect=urlopen), patch(
            "concurrent.futures.ThreadPoolExecutor.submit", autospec=True, side_effect=submit_inline
        )

    def test_openrouter_success_requests_and_reports_provider_usage(self):
        payload = {
            "id": "gen-123",
            "model": "vendor/model-v2",
            "choices": [{"message": {"content": "ตอบแล้ว"}}],
            "usage": {"prompt_tokens": 19, "completion_tokens": 4, "cost": 0.00042},
        }
        urlopen, submit = self._inline_reporter(payload)
        with urlopen, submit:
            answer = complete("provider-secret", "vendor/model", "คำถามลูกค้า")

        self.assertEqual(answer, "ตอบแล้ว")
        request_payload = json.loads(self.provider_requests[0].data)
        self.assertEqual(request_payload.get("usage"), {"include": True})
        self.assertEqual(len(self.posts), 1)
        event = self.posts[0][1]
        self.assertEqual(event["provider"], "openrouter")
        self.assertEqual(event["model"], "vendor/model-v2")
        self.assertEqual(event["request_id"], "gen-123")
        self.assertEqual(event["prompt_tokens"], 19)
        self.assertEqual(event["completion_tokens"], 4)
        self.assertEqual(event["cost_usd"], 0.00042)
        self.assertEqual(event["status"], "success")
        self.assertNotIn("คำถามลูกค้า", self.posts[0][0].data.decode())
        self.assertNotIn("provider-secret", self.posts[0][0].data.decode())
        self.assertEqual(self.posts[0][0].get_header("X-service-token"), "svc_test_token")

    def test_openrouter_provider_failure_is_reported_and_rethrown(self):
        def urlopen(request, timeout):
            if request.full_url == "https://openrouter.ai/api/v1/chat/completions":
                raise HTTPError(request.full_url, 429, "rate limited", {}, io.BytesIO(b"private body"))
            self.posts.append((request, json.loads(request.data)))
            return FakeResponse({"success": True})

        def submit_inline(executor, function, *args, **kwargs):
            function(*args, **kwargs)

        with patch("urllib.request.urlopen", side_effect=urlopen), patch(
            "concurrent.futures.ThreadPoolExecutor.submit", autospec=True, side_effect=submit_inline
        ):
            with self.assertRaises(HTTPError) as raised:
                complete("provider-secret", "vendor/model", "private prompt")
        raised.exception.close()

        self.assertEqual(len(self.posts), 1)
        event = self.posts[0][1]
        self.assertEqual(event["status"], "error")
        self.assertEqual(event["http_status"], 429)
        self.assertNotIn("private prompt", self.posts[0][0].data.decode())
        self.assertNotIn("provider-secret", self.posts[0][0].data.decode())

    def test_usage_hub_timeout_does_not_change_openrouter_answer(self):
        provider_payload = {
            "id": "gen-timeout",
            "model": "vendor/model",
            "choices": [{"message": {"content": "ยังตอบได้"}}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 2, "cost": 0.00001},
        }
        calls = []

        def urlopen(request, timeout):
            calls.append(request.full_url)
            if request.full_url == "https://openrouter.ai/api/v1/chat/completions":
                return FakeResponse(provider_payload)
            raise TimeoutError("usage hub timeout")

        def submit_inline(executor, function, *args, **kwargs):
            function(*args, **kwargs)

        with patch("urllib.request.urlopen", side_effect=urlopen), patch(
            "concurrent.futures.ThreadPoolExecutor.submit", autospec=True, side_effect=submit_inline
        ):
            answer = complete("provider-secret", "vendor/model", "คำถาม")

        self.assertEqual(answer, "ยังตอบได้")
        self.assertEqual(calls, [
            "https://openrouter.ai/api/v1/chat/completions",
            "https://digital.in.th/internal/ai-usage/logs",
        ])

    def test_missing_hub_env_skips_report_and_keeps_openrouter_call(self):
        provider_payload = {
            "id": "gen-no-hub",
            "model": "vendor/model",
            "choices": [{"message": {"content": "ตอบได้"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.00001},
        }
        calls = []

        def urlopen(request, timeout):
            calls.append(request.full_url)
            return FakeResponse(provider_payload)

        with patch.dict(os.environ, {"AI_USAGE_HUB_URL": "", "AI_USAGE_HUB_TOKEN": ""}), patch(
            "urllib.request.urlopen", side_effect=urlopen
        ):
            answer = complete("provider-secret", "vendor/model", "คำถาม")

        self.assertEqual(answer, "ตอบได้")
        self.assertEqual(calls, ["https://openrouter.ai/api/v1/chat/completions"])

    def test_gemini_success_reports_token_usage_without_guessing_cost(self):
        response = SimpleNamespace(
            text="ตอบแล้ว",
            response_id="gemini-response-1",
            usage_metadata=SimpleNamespace(
                prompt_token_count=21,
                candidates_token_count=6,
                total_token_count=27,
                thoughts_token_count=None,
                cached_content_token_count=None,
            ),
        )
        client = SimpleNamespace(models=SimpleNamespace(generate_content=MagicMock(return_value=response)))
        reference = set_usage_reference("request-123")
        attempt = set_usage_attempt(2)
        urlopen, submit = self._inline_reporter({})
        try:
            with urlopen, submit:
                result = call_gemini(
                    client,
                    model="gemini-3.6-flash",
                    contents="private prompt",
                    config={},
                    source="chat.answer",
                    units={"media_processed": 1},
                )
        finally:
            reset_usage_attempt(attempt)
            reset_usage_reference(reference)

        self.assertIs(result, response)
        event = self.posts[0][1]
        self.assertEqual(event["provider"], "gemini")
        self.assertEqual(event["request_id"], "gemini-response-1")
        self.assertEqual(event["reference"], "chat_request:request-123")
        self.assertEqual(event["attempt"], 2)
        self.assertEqual((event["prompt_tokens"], event["completion_tokens"]), (21, 6))
        self.assertNotIn("cost_usd", event)
        self.assertNotIn("private prompt", self.posts[0][0].data.decode())

    def test_gemini_provider_error_is_reported_and_rethrown(self):
        client = SimpleNamespace(models=SimpleNamespace(
            generate_content=MagicMock(side_effect=TimeoutError("private provider error"))))
        urlopen, submit = self._inline_reporter({})
        with urlopen, submit:
            with self.assertRaises(TimeoutError):
                call_gemini(client, model="gemini-test", contents="private prompt", config={}, source="chat.intent")

        event = self.posts[0][1]
        self.assertEqual((event["status"], event["error_message"]), ("timeout", "TimeoutError"))
        self.assertNotIn("private prompt", self.posts[0][0].data.decode())


if __name__ == "__main__":
    unittest.main()
