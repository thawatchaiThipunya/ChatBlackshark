import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from backend.app import legacy_data
from backend.app import main


class Cursor(list):
    def sort(self, *_args):
        return self

    def skip(self, count):
        return Cursor(self[count:])

    def limit(self, count):
        return Cursor(self[:count])

    def max_time_ms(self, _count):
        return self


class Conversations:
    def __init__(self, rows):
        self.rows = rows
        self.query = None

    def find(self, query, _projection):
        self.query = query
        return Cursor(self.rows)

    def distinct(self, field, query):
        values = []
        for row in self.rows:
            if row.get("platform") != query.get("platform"):
                continue
            if "shop_id" in query and row.get("shop_id") not in query["shop_id"].get("$in", []):
                continue
            name = query.get("shop_name")
            if name and not name.search(str(row.get("shop_name") or "")):
                continue
            value = row.get(field)
            if value not in values:
                values.append(value)
        return values


class LegacyDataScopeTests(unittest.TestCase):
    def setUp(self):
        legacy_data._legacy_shop_name_cache.clear()
        legacy_data._shop_alias_cache.clear()

    def test_supabase_display_name_resolves_unique_legacy_alias_by_shop_id(self):
        conversations = Conversations([
            {"platform": "shopee", "shop_id": "1191420560", "shop_name": "BlackShark"},
            {"platform": "shopee", "shop_id": "1191420560", "shop_name": "BlackShark"},
            {"platform": "shopee", "shop_id": "999", "shop_name": "Other Store"},
        ])
        with patch.object(legacy_data, "_conversation_collection", return_value=conversations):
            alias = legacy_data.resolve_legacy_shop_name("shopee", "1191420560")
            scoped = legacy_data.shop_alias_is_unique("shopee", "1191420560", "BlackShark")
        self.assertEqual(alias, "BlackShark")
        self.assertTrue(scoped)

    def test_legacy_alias_shared_by_another_shop_is_rejected(self):
        conversations = Conversations([
            {"platform": "shopee", "shop_id": "1191420560", "shop_name": "BlackShark"},
            {"platform": "shopee", "shop_id": "999", "shop_name": "BlackShark"},
        ])
        with patch.object(legacy_data, "_conversation_collection", return_value=conversations):
            self.assertIsNone(legacy_data.resolve_legacy_shop_name("shopee", "1191420560"))

    def test_card_identity_lookup_projects_only_item_id_and_listing_name(self):
        product = {"item_id": 12345678, "item_name": "Brand Model X", "shopname": "Shop A",
                   "description": "external description", "model": [{"model_name": "Red"}],
                   "tier_variation": [{"options": ["Red"]}]}

        class IdentityCollection:
            def __init__(self):
                self.projection = None

            def find(self, query, projection):
                self.projection = projection
                return Cursor([product] if query["shopname"] == "Shop A" else [])

        collection = IdentityCollection()
        with patch.object(legacy_data, "_products_collection", return_value=collection):
            found = legacy_data.product_identities_by_ids(["12345678"], "Shop A")

        self.assertEqual(found, {"12345678": {"item_id": "12345678", "name": "Brand Model X"}})
        self.assertEqual(collection.projection, {"_id": 0, "item_id": 1, "item_name": 1, "name": 1})

    def test_conversation_list_never_returns_unapproved_shop(self):
        collection = Conversations([
            {"conversation_id": "a", "platform": "shopee", "shop_id": "111", "shop_name": "Shop A"},
            {"conversation_id": "b", "platform": "shopee", "shop_id": "222", "shop_name": "Shop B"},
        ])
        with patch.object(legacy_data, "_conversation_collection", return_value=collection):
            result = legacy_data.list_conversations(allowed_shops=[
                {"platform": "shopee", "shop_id": "111", "shop_name": "Shop A"}],
                platform="shopee", shop_id="111")
        self.assertEqual([row["conversation_id"] for row in result["conversations"]], ["a"])
        self.assertEqual(collection.query["$or"][0]["shop_id"]["$in"], ["111", 111])

    def test_history_contains_at_most_five_pairs_and_prior_image_description(self):
        now = datetime.now(timezone.utc)
        rows = []
        for index in range(7):
            rows.append({"message_id": f"u{index}", "role": "user", "direction": "in",
                         "text": f"question {index}", "created_timestamp": now + timedelta(seconds=index * 2)})
            rows.append({"message_id": f"a{index}", "role": "admin", "direction": "out",
                         "text": f"answer {index}", "created_timestamp": now + timedelta(seconds=index * 2 + 1)})
        shadows = [{"inbound_message_id": "u6", "bot_reply_text": "answer 6",
                    "bot_image_desc": "เห็นนาฬิกาสีแดง", "created_at": now + timedelta(seconds=20)}]
        with patch.object(legacy_data, "_message_rows", return_value=rows), \
             patch.object(legacy_data, "_shadow_rows", return_value=shadows), \
             patch.object(legacy_data, "product_identities_by_ids", return_value={}):
            history = legacy_data.history_for_conversation("c", "111", "shopee", "Shop A", max_pairs=5)
        self.assertEqual(len(history), 10)
        self.assertEqual(history[0]["content"], "question 2")
        self.assertEqual(history[-2]["vision_descriptions"], ["เห็นนาฬิกาสีแดง"])
        self.assertEqual(history[-1]["content"], "answer 6")

    def test_history_preserves_card_item_id_for_kb_identity_resolution(self):
        row = {"message_id": "card-1", "role": "user", "direction": "in", "text": "จอกี่นิ้ว",
               "raw_payload": {"data": {"content": {"message_type": "item",
                   "content": {"item_id": "123456789"}}}}}
        listing = {"item_id": "123456789", "name": "(NewArrival) Official product listing"}
        with patch.object(legacy_data, "_message_rows", return_value=[row]), \
             patch.object(legacy_data, "_shadow_rows", return_value=[]), \
             patch.object(legacy_data, "shop_alias_is_unique", return_value=True), \
             patch.object(legacy_data, "product_identities_by_ids", return_value={"123456789": listing}):
            history = legacy_data.history_for_conversation("c", "111", "shopee", "Shop A")

        self.assertEqual(history[0]["card_item_ids"], ["123456789"])
        self.assertEqual(history[0]["card_context"], [listing])

    def test_variation_card_and_bundle_read_scoped_item_ids(self):
        variation = {"data": {"content": {"message_type": "variation_card",
            "content": {"product_id": 56067346193}}}}
        event, _ = legacy_data._message_event(variation, "[variation_card]")
        self.assertEqual(event.item_ids, ("56067346193",))
        self.assertEqual(event.text, "")
        bundle = {"data": {"content": {"message_type": "bundle_message",
            "content": {"messages": ["sub-1"]}}}}
        event, media = legacy_data._message_event(bundle, "[bundle_message]")

        class Submessages:
            def __init__(self):
                self.query = None

            def find(self, query, _projection):
                self.query = query
                return Cursor([{"message_id": "sub-1", "text": "", "raw_payload": variation}])

        collection = Submessages()
        with patch.object(legacy_data, "_message_collection", return_value=collection):
            event, _ = legacy_data._expand_bundle(event, media["media"], "conversation-1", "1191420560", "shopee")
        self.assertEqual(event.item_ids, ("56067346193",))
        self.assertEqual(collection.query["conversation_id"], "conversation-1")
        self.assertEqual(collection.query["shop_id"]["$in"], ["1191420560", 1191420560])

    def test_order_card_history_keeps_order_number_as_context(self):
        now = datetime.now(timezone.utc)
        rows = [{"message_id": "order-1", "role": "user", "direction": "in",
                 "raw_payload": {"message_type": "order", "content": {
                     "source_content": {"order_sn": "240101ABCD1234"}}},
                 "created_timestamp": now}]
        with patch.object(legacy_data, "_message_rows", return_value=rows), \
             patch.object(legacy_data, "_shadow_rows", return_value=[]), \
             patch.object(legacy_data, "product_identities_by_ids", return_value={}):
            history = legacy_data.history_for_conversation("c", "111", "shopee", "Shop A", max_pairs=5)

        self.assertIn("240101ABCD1234", history[0]["content"])

    def test_shopee_sticker_ids_resolve_nested_asset_url_and_trusted_download_host(self):
        payload = {"data": {"content": {"message_type": "sticker", "content": {
            "sticker": {"sticker_id": 7, "sticker_package_id": "shop_pack",
                        "region": "TH", "extension": "webp"},
        }}}}

        event, media = legacy_data._message_event(payload, "[sticker]")

        self.assertEqual(event.message_type, "sticker")
        self.assertEqual(event.text, "")
        self.assertEqual(media["media"], [{
            "url": "https://deo.shopeemobile.com/shopee/shopee-sticker-live-th/packs/shop_pack/7@1x.webp",
            "kind": "image",
        }])
        self.assertTrue(main._trusted_media_url(media["media"][0]["url"]))

    def test_sticker_without_extension_uses_extension_from_shopee_pack_metadata(self):
        payload = {"data": {"content": {"message_type": "sticker", "content": {
            "sticker": {"sticker_id": "0001", "sticker_package_id": "shop_pack", "region": "TH"},
        }}}}
        metadata = [
            {"packs": [{"pid": "shop_pack", "reg": ["TH"]}]},
            {"stickers": [{"sid": "0001", "ext": "webp"}]},
        ]

        with patch.object(legacy_data, "_fetch_shopee_sticker_json", side_effect=metadata, create=True) as fetch:
            _event, media = legacy_data._message_event(payload, "[sticker]")

        self.assertTrue(media["media"][0]["url"].endswith("/0001@1x.webp"))
        self.assertEqual(fetch.call_count, 2)

    def test_image_payload_uses_original_url_without_duplicate_thumbnail(self):
        payload = {"message_type": "image", "content": {
            "url": "https://cf.shopee.co.th/full.jpg",
            "thumb_url": "https://cf.shopee.co.th/thumb.jpg",
        }}

        _event, media = legacy_data._message_event(payload, "[image]")

        self.assertEqual(media["media"], [{"url": "https://cf.shopee.co.th/full.jpg", "kind": "image"}])

    def test_shopee_envelope_preserves_message_text_cards_video_and_notification_metadata(self):
        cases = [
            ({"data": {"content": {"message_type": "image_with_text",
              "content": {"text": "หน้าจอขึ้นแบบนี้ครับ", "image_url": "https://cf.shopee.co.th/a.jpg"}}}},
             "[image]", "image_with_text", "หน้าจอขึ้นแบบนี้ครับ", "https://cf.shopee.co.th/a.jpg", "image"),
            ({"data": {"content": {"message_type": "video",
              "content": {"video_url": "https://cf.shopee.co.th/a.mp4"}}}},
             "[video]", "video", "", "https://cf.shopee.co.th/a.mp4", "video"),
            ({"data": {"content": {"message_type": "item",
              "content": {"item_id": 46867428860}}}},
             "[item]", "item", "", "", ""),
            ({"data": {"content": {"message_type": "bundle_message",
              "source_content": {"item_id": "46867428860"},
              "content": {"messages": ["sub-1", "sub-2"]}}}},
             "[bundle_message]", "bundle_message", "", "", ""),
        ]
        for payload, fallback, expected_type, expected_text, expected_url, expected_kind in cases:
            with self.subTest(message_type=expected_type):
                event, media = legacy_data._message_event(payload, fallback)
                self.assertEqual(event.message_type, expected_type)
                self.assertEqual(event.text, expected_text)
                if expected_type == "item":
                    self.assertEqual(event.item_ids, ("46867428860",))
                if expected_type == "bundle_message":
                    self.assertEqual(event.item_ids, ("46867428860",))
                    self.assertEqual(event.metadata["bundle_message_ids"], ["sub-1", "sub-2"])
                if expected_url:
                    self.assertIn({"url": expected_url, "kind": expected_kind}, media["media"])

        notification, _ = legacy_data._message_event({"data": {"content": {
            "message_type": "notification",
            "content": {"notification_for_receiver": "ข้อความแจ้งเตือนจาก Shopee"},
        }}}, "[notification]")
        self.assertEqual(notification.message_type, "notification")
        self.assertEqual(notification.text, "")
        self.assertEqual(notification.metadata["notification_text"], "ข้อความแจ้งเตือนจาก Shopee")

    def test_notification_message_page_shows_platform_notification_text(self):
        row = {"message_id": "notification-1", "role": "user", "direction": "in",
               "text": "[notification]", "raw_payload": {"data": {"content": {
                   "message_type": "notification",
                   "content": {"notification_for_receiver": "พัสดุถูกจัดส่งแล้ว"},
               }}}}

        formatted = legacy_data._format_message(row, "Shop A", {}, {}, "conversation-1", "shop-1", "shopee")

        self.assertEqual(formatted["message_type"], "notification")
        self.assertEqual(formatted["content"], "พัสดุถูกจัดส่งแล้ว")

    def test_message_page_cursor_keeps_same_timestamp_rows(self):
        class Messages:
            query = None

            def find(self, query, _projection):
                self.query = query
                return Cursor([])

        collection = Messages()
        stamp = datetime(2026, 11, 20, tzinfo=timezone.utc)
        with patch.object(legacy_data, "_message_collection", return_value=collection):
            legacy_data._message_rows("conversation-1", "111", "shopee",
                                      before=stamp, before_id="message-7", limit=100)
        self.assertEqual(collection.query["$or"][1],
                         {"created_timestamp": stamp, "message_id": {"$lt": "message-7"}})


if __name__ == "__main__":
    unittest.main()
