import unittest
from unittest.mock import patch

from backend.app import shopee_products


class ShopeeProductLookupTests(unittest.TestCase):
    def test_item_lookup_reuses_scoped_identity_resolver_without_loading_product_facts(self):
        identity = {"item_id": "123456789", "name": "Black Shark GS3"}
        with patch("backend.app.legacy_data.resolve_legacy_shop_name", return_value="BlackShark") as alias_check, \
             patch("backend.app.legacy_data.product_identities_by_ids", return_value={"123456789": identity}) as by_id:
            result = shopee_products.lookup_shopee_item(
                "123456789", platform="shopee", shop_id="1191420560", shop_name="Black Shark Thailand")

        alias_check.assert_called_once_with("shopee", "1191420560")
        by_id.assert_called_once_with(["123456789"], "BlackShark")
        self.assertEqual(result, identity)

    def test_item_lookup_rejects_shop_name_that_cannot_be_mapped_uniquely(self):
        with patch("backend.app.legacy_data.resolve_legacy_shop_name", return_value=None) as alias_check, \
             patch("backend.app.legacy_data.product_identities_by_ids") as by_id:
            result = shopee_products.lookup_shopee_item(
                "123456789", platform="shopee", shop_id="1191420560", shop_name="Black Shark Thailand")

        self.assertIsNone(result)
        alias_check.assert_called_once_with("shopee", "1191420560")
        by_id.assert_not_called()


if __name__ == "__main__":
    unittest.main()
