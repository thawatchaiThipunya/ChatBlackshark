import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from backend.app import legacy_data


class Cursor(list):
    def sort(self, *_args):
        return self

    def skip(self, count):
        return Cursor(self[count:])

    def limit(self, count):
        return Cursor(self[:count])

    def max_time_ms(self, _count):
        return self


class Products:
    def __init__(self, rows):
        self.rows = rows
        self.query = None

    def find(self, query, _projection):
        self.query = query
        return Cursor(row for row in self.rows if row["item_id"] in query["item_id"]["$in"]
                      and query["shopname"] == row["shopname"])


class CatalogueProducts(Products):
    def find(self, query, _projection):
        if "item_id" in query:
            return super().find(query, _projection)
        return Cursor(row for row in self.rows if query["shopname"] == row["shopname"])


class Conversations:
    def __init__(self, rows):
        self.rows = rows
        self.query = None

    def find(self, query, _projection):
        self.query = query
        return Cursor(self.rows)


class LegacyDataScopeTests(unittest.TestCase):
    def test_item_card_requires_exact_shop_and_checks_every_active_unit(self):
        products = Products([
            {"item_id": 12345678, "item_name": "Watch A", "shopname": "Shop A", "item_status": "NORMAL",
             "model": [
                 {"model_id": 1, "model_name": "ดำ", "model_status": "MODEL_NORMAL",
                  "stock_info_v2": {"summary_info": {"total_available_stock": 0}}},
                 {"model_id": 2, "model_name": "แดง", "model_status": "MODEL_NORMAL",
                  "stock_info_v2": {"summary_info": {"total_available_stock": 3}}},
             ]},
            {"item_id": 12345678, "item_name": "Watch B", "shopname": "Shop B", "item_status": "NORMAL",
             "model": [{"model_id": 3, "model_name": "ดำ", "model_status": "MODEL_NORMAL",
                        "stock_info_v2": {"summary_info": {"total_available_stock": 5}}}]},
        ])
        with patch.object(legacy_data, "_products_collection", return_value=products):
            found = legacy_data._products_by_ids(["12345678"], "Shop A")
        self.assertEqual(list(found), ["12345678"])
        self.assertEqual(found["12345678"]["name"], "Watch A")
        self.assertEqual(found["12345678"]["availability"], "available")
        self.assertEqual([model["available"] for model in found["12345678"]["models"]], [0, 3])

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
             patch.object(legacy_data, "_products_by_ids", return_value={}):
            history = legacy_data.history_for_conversation("c", "111", "shopee", "Shop A", max_pairs=5)
        self.assertEqual(len(history), 10)
        self.assertEqual(history[0]["content"], "question 2")
        self.assertEqual(history[-2]["vision_descriptions"], ["เห็นนาฬิกาสีแดง"])
        self.assertEqual(history[-1]["content"], "answer 6")

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

    def test_catalog_stock_filters_combined_listing_by_model_unit(self):
        def unit(model_id, name, stock):
            return {"model_id": model_id, "model_name": name, "model_status": "MODEL_NORMAL",
                    "stock_info_v2": {"summary_info": {"total_available_stock": stock}}}
        products = CatalogueProducts([
            {"item_id": 12345678, "item_name": "Black Shark GS3 / GS3 Ultra Smartwatch", "shopname": "Shop A",
             "category_id": 100270, "item_status": "NORMAL", "model": [unit(1, "GS3 Black", 0), unit(2, "GS3 Ultra Silver", 4)]},
            {"item_id": 12345679, "item_name": "Black Shark GS3 Watch Strap", "shopname": "Shop A",
             "category_id": 100288, "item_status": "NORMAL", "model": [unit(3, "Black", 7)]},
            {"item_id": 12345680, "item_name": "Black Shark GS3 Smartwatch", "shopname": "Shop A",
             "category_id": 100270, "item_status": "NORMAL", "model": [unit(4, "Green", 2)]},
        ])
        catalog = [{"name": "Black Shark GS3"}, {"name": "Black Shark GS3 Ultra"}]
        with patch.object(legacy_data, "_products_collection", return_value=products):
            gs3 = legacy_data.products_for_catalog_names(["Black Shark GS3"], catalog, "Shop A")
            ultra = legacy_data.products_for_catalog_names(["Black Shark GS3 Ultra"], catalog, "Shop A")
        self.assertEqual([row["item_id"] for row in gs3], ["12345678", "12345680"])
        self.assertEqual([model["model_id"] for model in gs3[0]["models"]], ["1"])
        self.assertEqual(gs3[0]["availability"], "unavailable")
        self.assertEqual([model["model_id"] for model in ultra[0]["models"]], ["2"])

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
