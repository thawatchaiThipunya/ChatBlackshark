import base64
import hashlib
import hmac
import json
import os
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app import main


ISSUER_SECRET = "issuer-secret-for-tests-that-is-at-least-32-chars"
SIGNING_SECRET = "signing-secret-for-tests-that-is-at-least-32-chars"
LEGACY_TOKEN = "legacy-token-for-tests-that-is-at-least-32-chars"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _signed_token(*, issued_at: int, expires_at: int, secret: str = SIGNING_SECRET) -> str:
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = _b64url(json.dumps({
        "iss": "chatblackshark-api",
        "aud": "chatblackshark-engine",
        "sub": "chatbotproductms",
        "scope": "engine:answer",
        "iat": issued_at,
        "exp": expires_at,
    }, separators=(",", ":")).encode())
    signing_input = f"{header}.{claims}".encode("ascii")
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{header}.{claims}.{_b64url(signature)}"


class EngineTokenAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def test_issuer_returns_a_signed_ten_minute_access_token(self):
        with patch.dict(os.environ, {
            "CHATBLACKSHARK_TOKEN_ISSUER_SECRET": ISSUER_SECRET,
            "CHATBLACKSHARK_TOKEN_SIGNING_SECRET": SIGNING_SECRET,
        }):
            response = self.client.post("/api/engine/token",
                headers={"Authorization": f"Bearer {ISSUER_SECRET}"})

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["token_type"], "Bearer")
        self.assertEqual(body["expires_in"], 600)
        token = body["access_token"]
        now = int(time.time())
        header, claims, signature = token.split(".")
        signing_input = f"{header}.{claims}".encode("ascii")
        expected = hmac.new(SIGNING_SECRET.encode(), signing_input, hashlib.sha256).digest()
        self.assertTrue(hmac.compare_digest(base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)), expected))
        payload = json.loads(base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4)))
        self.assertEqual(payload["iss"], "chatblackshark-api")
        self.assertEqual(payload["aud"], "chatblackshark-engine")
        self.assertEqual(payload["sub"], "chatbotproductms")
        self.assertEqual(payload["scope"], "engine:answer")
        self.assertLessEqual(payload["iat"], now)
        self.assertEqual(payload["exp"] - payload["iat"], 600)

    def test_wrong_issuer_secret_is_rejected(self):
        with patch.dict(os.environ, {
            "CHATBLACKSHARK_TOKEN_ISSUER_SECRET": ISSUER_SECRET,
            "CHATBLACKSHARK_TOKEN_SIGNING_SECRET": SIGNING_SECRET,
        }):
            response = self.client.post("/api/engine/token",
                headers={"Authorization": "Bearer wrong-issuer-secret"})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "engine_token_issuer_unauthorized")

    def test_signing_config_is_required_to_issue_access_token(self):
        with patch.dict(os.environ, {"CHATBLACKSHARK_TOKEN_ISSUER_SECRET": ISSUER_SECRET}, clear=False):
            os.environ.pop("CHATBLACKSHARK_TOKEN_SIGNING_SECRET", None)
            response = self.client.post("/api/engine/token",
                headers={"Authorization": f"Bearer {ISSUER_SECRET}"})

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "engine_token_signing_not_configured")

    def test_answer_accepts_a_valid_signed_access_token_and_persona(self):
        now = int(time.time())
        token = _signed_token(issued_at=now, expires_at=now + 600)
        payload = {
            "conversation_id": "conversation-1",
            "message_id": "message-1",
            "shop_id": "shop-1",
            "platform": "shopee",
            "persona": {
                "bot_name": "น้องร้านเรา", "persona_text": "เป็นกันเอง ช่วยแนะนำอย่างสุภาพ",
                "friendliness": 80, "length": "medium", "detail_level": 55, "voice": "female",
            },
        }
        expected = {"status": "answered", "answer": "สวัสดีค่ะ"}
        with patch.dict(os.environ, {"CHATBLACKSHARK_TOKEN_SIGNING_SECRET": SIGNING_SECRET}, clear=False), \
             patch.object(main, "_answer_legacy_message", return_value=expected) as answer:
            response = self.client.post("/api/engine/answer", json=payload,
                headers={"Authorization": f"Bearer {token}"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["answer"], "สวัสดีค่ะ")
        self.assertEqual(answer.call_args.args[0].persona.bot_name, "น้องร้านเรา")

    def test_expired_signed_access_token_is_rejected(self):
        now = int(time.time())
        token = _signed_token(issued_at=now - 700, expires_at=now - 100)
        payload = {"conversation_id": "c", "message_id": "m", "shop_id": "s", "platform": "shopee"}
        with patch.dict(os.environ, {"CHATBLACKSHARK_TOKEN_SIGNING_SECRET": SIGNING_SECRET}, clear=False):
            response = self.client.post("/api/engine/answer", json=payload,
                headers={"Authorization": f"Bearer {token}"})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "legacy_engine_unauthorized")

    def test_test_answer_accepts_a_valid_signed_access_token(self):
        now = int(time.time())
        token = _signed_token(issued_at=now, expires_at=now + 600)
        scope = {"platform": "shopee", "shop_id": "shop-1", "shop_name": "ร้านทดสอบ"}
        knowledge = {"products": [{"name": "สินค้า A", "description": "ข้อมูลทดสอบ"}], "faqs": []}

        class FakeEngine:
            def __init__(self, store):
                self.store = store

            def answer(self, question, **kwargs):
                return {"status": "answered", "intent": "product_info", "answer": question,
                        "products": [], "sources": []}

        with patch.dict(os.environ, {"CHATBLACKSHARK_TOKEN_SIGNING_SECRET": SIGNING_SECRET}, clear=False), \
             patch.object(main, "_shop_context", return_value=(scope, knowledge)), \
             patch.object(main, "ProductChatEngine", FakeEngine):
            response = self.client.post("/api/engine/test-answer",
                json={"question": "ทดสอบ", "platform": "shopee", "shop_id": "shop-1"},
                headers={"Authorization": f"Bearer {token}"})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["answer"], "ทดสอบ")

    def test_legacy_engine_token_remains_accepted_during_migration(self):
        payload = {"conversation_id": "c", "message_id": "m", "shop_id": "s", "platform": "shopee"}
        with patch.dict(os.environ, {"LEGACY_ENGINE_TOKEN": LEGACY_TOKEN}, clear=False), \
             patch.object(main, "_answer_legacy_message", return_value={"status": "answered", "answer": "ok"}):
            response = self.client.post("/api/engine/answer", json=payload,
                headers={"Authorization": f"Bearer {LEGACY_TOKEN}"})

        self.assertEqual(response.status_code, 200, response.text)


if __name__ == "__main__":
    unittest.main()
