import unittest

from backend.app.product_categories import category_from_description
from backend.app.product_identity import match_catalog_product


class ProductCategoryTests(unittest.TestCase):
    def test_reads_explicit_category_from_product_type_lines(self):
        self.assertEqual(category_from_description("ประเภทสินค้า: smart watch\nชื่อรุ่นสินค้าย่อ: GS3"), "smart watch")
        self.assertEqual(category_from_description("ประเภท: หูฟังไร้สาย (TWS)\nBluetooth: 5.4"), "หูฟังไร้สาย (TWS)")

    def test_does_not_guess_category_when_source_has_no_type_line(self):
        self.assertIsNone(category_from_description("Black Shark Pad 7Pro UE\nหน้าจอ 11 นิ้ว"))

    def test_matches_the_most_specific_exact_catalog_model(self):
        products = [
            {"name": "Black Shark S3", "description": "ชื่อรุ่นสินค้าย่อ: S3"},
            {"name": "Black Shark S3 Classic", "description": "ชื่อรุ่นสินค้าย่อ: S3 Classic"},
        ]
        self.assertEqual(match_catalog_product("Black Shark S3 Classic Smart Watch", products), "Black Shark S3 Classic")

    def test_matches_listing_titles_with_newarrival_and_capacity_suffixes(self):
        products = [
            {"name": "BLACK SHARK RUN", "description": "ชื่อรุ่นสินค้าย่อ: Run"},
            {"name": "Black Shark Gaming Tablet", "description": "ชื่อรุ่นสินค้าย่อ: Gaming"},
        ]
        self.assertEqual(match_catalog_product("(NewArrival) BLACK SHARK RUN Smart Watch", products), "BLACK SHARK RUN")
        self.assertEqual(match_catalog_product("Black Shark Gaming Tablet (12/256GB)", products), "Black Shark Gaming Tablet")

    def test_does_not_silently_map_unlisted_edition_to_base_model(self):
        products = [{"name": "Black Shark S3", "description": "ชื่อรุ่นสินค้าย่อ: S3"}]
        self.assertIsNone(match_catalog_product("Black Shark S3 Special Edition", products))


if __name__ == "__main__":
    unittest.main()
