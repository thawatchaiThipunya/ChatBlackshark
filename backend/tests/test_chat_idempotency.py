import unittest
from unittest.mock import patch

from backend.app.store import SupabaseStore


class ChatMessageIdempotencyTests(unittest.TestCase):
    def test_save_message_keeps_existing_rpc_and_passes_turn_id_inside_payload(self):
        store = SupabaseStore(url="https://example.supabase.co", publishable_key="public", token="app-token")
        payload = {"role": "assistant", "content": "คำตอบ", "payload": {"request_id": "turn-1"}}
        with patch.object(store, "_request", return_value={"id": "message-1"}) as request:
            result = store.chat_action("g" * 40, "save_message", "session-1", payload)

        self.assertEqual(result, {"id": "message-1"})
        request.assert_called_once_with("rpc/bot_chat_action", {
            "p_app_token": "app-token", "p_guest_token": "g" * 40,
            "p_action": "save_message", "p_session_id": "session-1", "p_payload": payload,
        })

    def test_other_chat_actions_keep_using_existing_rpc(self):
        store = SupabaseStore(url="https://example.supabase.co", publishable_key="public", token="app-token")
        with patch.object(store, "_request", return_value=[]) as request:
            store.chat_action("g" * 40, "load_session", "session-1")

        self.assertEqual(request.call_args.args[0], "rpc/bot_chat_action")


if __name__ == "__main__":
    unittest.main()
