import unittest
import os
from unittest.mock import patch

from backend.app import shopee_products


class ShopeeProductLookupTests(unittest.TestCase):
    def test_item_lookup_requires_unique_shop_name_and_filters_mongo_query(self):
        document = {"item_id": "123456789", "item_name": "Black Shark GS3", "shopname": "BlackShark",
                    "description": "รายละเอียด", "item_status": "NORMAL", "model": [], "tier_variation": []}

        class Collection:
            def __init__(self):
                self.query = None

            def find_one(self, query, projection):
                self.query = query
                return document

        collection = Collection()

        class Database:
            def __getitem__(self, name):
                return collection

        class Client:
            def __getitem__(self, name):
                return Database()

        with patch.dict(os.environ, {"MONGO_URI": "mongodb://test"}), \
             patch.object(shopee_products, "_client", Client()), \
             patch("backend.app.legacy_data.resolve_legacy_shop_name", return_value="BlackShark") as alias_check:
            result = shopee_products.lookup_shopee_item(
                "123456789", platform="shopee", shop_id="1191420560", shop_name="Black Shark Thailand")

        alias_check.assert_called_once_with("shopee", "1191420560")
        self.assertEqual(collection.query["shopname"], "BlackShark")
        self.assertNotIn("brand", collection.query)
        self.assertEqual(result["name"], "Black Shark GS3")

    def test_item_lookup_rejects_shop_name_that_cannot_be_mapped_uniquely(self):
        with patch.dict(os.environ, {"MONGO_URI": "mongodb://test"}), \
             patch.object(shopee_products, "_client", None), \
             patch("backend.app.legacy_data.resolve_legacy_shop_name", return_value=None) as alias_check:
            result = shopee_products.lookup_shopee_item(
                "123456789", platform="shopee", shop_id="1191420560", shop_name="Black Shark Thailand")

        self.assertIsNone(result)
        alias_check.assert_called_once_with("shopee", "1191420560")


if __name__ == "__main__":
    unittest.main()
