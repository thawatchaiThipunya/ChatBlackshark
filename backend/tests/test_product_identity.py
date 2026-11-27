import unittest

from backend.app.product_identity import match_catalog_product


class ProductIdentityTests(unittest.TestCase):
    def test_long_marketplace_title_matches_unique_kb_model_alias(self):
        products = [{
            "name": "Black Shark PureLoop",
            "description": "ชื่อรุ่นสินค้าย่อ: PureLoop\nติดตามสุขภาพ 24 ชั่วโมง",
        }]
        listing_title = (
            "(NewArrival) Black Shark PureLoop Smart Health Tracker "
            "สายรัดข้อมืออัจฉริยะ ติดตามสุขภาพ 24 ชม. ใช้งานได้ 14 วัน -1Y"
        )

        self.assertEqual(match_catalog_product(listing_title, products), "Black Shark PureLoop")

    def test_item_id_maps_to_exact_kb_product_when_title_is_unhelpful(self):
        products = [{"name": "Black Shark GS3 Smartwatch Charger", "item_ids": [46057699735]}]

        self.assertEqual(
            match_catalog_product("(NewArrival) Official store charger listing", products, item_id="46057699735"),
            "Black Shark GS3 Smartwatch Charger",
        )

    def test_conflicting_item_id_does_not_override_explicit_unknown_edition(self):
        products = [
            {"name": "Black Shark S3", "description": "ชื่อรุ่นสินค้าย่อ: S3", "item_ids": [55355455002]},
            {"name": "Black Shark S3 Classic", "description": "ชื่อรุ่นสินค้าย่อ: S3 Classic"},
        ]

        self.assertIsNone(match_catalog_product(
            "Black Shark S3 Special Edition", products, item_id="55355455002"
        ))

    def test_duplicate_item_id_can_be_disambiguated_by_full_kb_title(self):
        products = [
            {"name": "Black Shark GS3 Ultra", "description": "ชื่อรุ่นสินค้าย่อ: GS3 Ultra", "item_ids": [26893612033]},
            {"name": "Black Shark GS3 Ultra (REFINED EDITION)",
             "description": "ชื่อรุ่นสินค้าย่อ: GS3 Ultra Refined Edition", "item_ids": [26893612033]},
        ]

        self.assertEqual(
            match_catalog_product("Black Shark GS3 Ultra Refined Edition", products, item_id="26893612033"),
            "Black Shark GS3 Ultra (REFINED EDITION)",
        )

    def test_spec_ampersand_does_not_make_gs3_ultra_card_look_like_a_bundle(self):
        base_description = (
            "ชื่อรุ่นสินค้าย่อ: GS3 Ultra\n"
            "มาตรฐาน MIL-STD 810H; GPS Dual-Band; กันน้ำ 5ATM และ IP69K; "
            "หน้าจอ AMOLED 60Hz; Bluetooth Calling"
        )
        refined_description = base_description.replace(
            "ชื่อรุ่นสินค้าย่อ: GS3 Ultra",
            "ชื่อรุ่นสินค้าย่อ: GS3 Ultra REFINED EDITION",
        )
        products = [
            {"name": "Black Shark GS3 Ultra", "description": base_description,
             "item_ids": [26893612033]},
            {"name": "Black Shark GS3 Ultra (REFINED EDITION)",
             "description": refined_description, "item_ids": [26893612033]},
        ]
        listing_title = (
            "Black Shark GS3 Ultra Smartwatch สมาร์ทวอทช์ GPS โทรออกได้ "
            "กันน้ำ 5ATM & IP69K มาตรฐาน MIL-STD 810H -1Y"
        )

        self.assertEqual(
            match_catalog_product(listing_title, products, item_id=26893612033),
            "Black Shark GS3 Ultra",
        )

    def test_unknown_edition_is_not_mapped_to_base_model(self):
        products = [
            {"name": "Black Shark S3", "description": "ชื่อรุ่นสินค้าย่อ: S3"},
            {"name": "Black Shark S3 Classic", "description": "ชื่อรุ่นสินค้าย่อ: S3 Classic"},
        ]

        self.assertIsNone(match_catalog_product("Black Shark S3 Special Edition", products))

    def test_composite_listing_is_not_mapped_to_only_one_component(self):
        products = [
            {"name": "Black Shark GS3", "description": "ชื่อรุ่นสินค้าย่อ: GS3"},
            {"name": "Black Shark GS3 Smartwatch Charger", "description": "ชื่อรุ่นสินค้าย่อ: GS3 Charger"},
        ]

        self.assertIsNone(match_catalog_product("Black Shark GS3 + charger bundle", products))


if __name__ == "__main__":
    unittest.main()
