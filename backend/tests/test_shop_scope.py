import unittest

from backend.app.store import SupabaseStore


class ShopScopeTests(unittest.TestCase):
    def test_shop_pagination_continues_when_server_page_cap_is_smaller(self):
        store = SupabaseStore(url="https://example.supabase.co", publishable_key="test")
        store.resolve_shop_scope = lambda platform, shop_id: {
            "platform": platform, "shop_id": shop_id, "shop_name": "Shop", "files": ["shop.xlsx"]}
        def request(path, payload=None):
            if path.startswith("bot_faqs?"):
                return []
            offset = int(path.split("offset=")[1].split("&")[0])
            return [{"name": f"item {i}", "source": {"file": "shop.xlsx"}}
                    for i in range(offset, min(offset + 500, 750))]
        store._request = request

        _, knowledge = store.knowledge_for_shop("shopee", "123")

        self.assertEqual(len(knowledge["products"]), 750)

    def test_shop_knowledge_queries_its_files_before_pagination(self):
        store = SupabaseStore(url="https://example.supabase.co", publishable_key="test")
        store.resolve_shop_scope = lambda platform, shop_id: {
            "platform": platform, "shop_id": shop_id, "shop_name": "CUKTECH Thailand",
            "files": ["cuktech.xlsx"],
        }
        requested = []

        def request(path, payload=None):
            requested.append(path)
            if path.startswith("bot_products?"):
                self.assertIn("item_ids", path)
                if "cuktech.xlsx" not in path:
                    return [{"name": "Other shop", "source": {"file": "other.xlsx"}}] * 1000
                offset = int(path.split("offset=")[1].split("&")[0]) if "offset=" in path else 0
                return [{"name": f"CTC {i}", "item_ids": [str(i)], "source": {"file": "cuktech.xlsx"}}
                        for i in range(offset, min(offset + 1000, 1001))]
            if path.startswith("bot_faqs?"):
                return []
            raise AssertionError(path)

        store._request = request
        _, knowledge = store.knowledge_for_shop("shopee", "987863120")
        self.assertEqual(len(knowledge["products"]), 1001)
        self.assertEqual(knowledge["products"][0]["item_ids"], ["0"])
        self.assertTrue(all(row["source"]["file"] == "cuktech.xlsx" for row in knowledge["products"]))
        self.assertTrue(all("cuktech.xlsx" in path for path in requested))

    def test_knowledge_is_filtered_by_platform_shop_and_attached_source_file(self):
        store = SupabaseStore(url="https://example.supabase.co", publishable_key="test")
        responses = {
            "platforms?select=p_id,code,name&limit=1000": [
                {"p_id": 1, "code": "shopee", "name": "Shopee"},
                {"p_id": 2, "code": "lazada", "name": "Lazada"},
            ],
            "shopnames?select=s_id,p_id,shop_id,shop_name,status&limit=1000": [
                {"s_id": 10, "p_id": 1, "shop_id": "111", "shop_name": "Black Shark Thailand", "status": "NORMAL"},
                {"s_id": 20, "p_id": 2, "shop_id": "222", "shop_name": "Other Store", "status": "ACTIVE"},
            ],
            "shop_files?select=s_id,shop_id,file_name&limit=2000": [
                {"s_id": 10, "shop_id": "111", "file_name": "blackshark.xlsx"},
                {"s_id": 20, "shop_id": "222", "file_name": "other.xlsx"},
            ],
        }
        fixture = {
            "products": [
                {"name": "BS Watch", "description": "watch facts", "source": {"file": "blackshark.xlsx", "sheet": "Products", "cell": "B3"}},
                {"name": "BS Watch", "description": "stale facts", "source": {"file": "blackshark.xlsx", "sheet": "Products", "cell": "B3"}},
                {"name": "BS Watch", "description": "another listing", "source": {"file": "blackshark.xlsx", "sheet": "Products", "cell": "B4"}},
                {"name": "BS Watch", "description": "same name, other shop", "source": {"file": "other.xlsx"}},
                {"name": "Unattributed", "description": "unknown facts", "source": {}},
            ],
            "faqs": [
                {"model_name": "BS Watch", "question": "Q", "answer": "A", "source": {"file": "blackshark.xlsx"}},
                {"model_name": "BS Watch", "question": "Q2", "answer": "other shop FAQ", "source": {"file": "other.xlsx"}},
            ],
        }
        def request(path, payload=None):
            if path.startswith("bot_products?"):
                offset = int(path.split("offset=")[1].split("&")[0])
                return fixture["products"][offset:offset + 1000]
            if path.startswith("bot_faqs?"):
                offset = int(path.split("offset=")[1].split("&")[0])
                return fixture["faqs"][offset:offset + 1000]
            return responses[path]
        store._request = request

        scope, knowledge = store.knowledge_for_shop("SHOPEE", "111")

        self.assertEqual(scope["shop_name"], "Black Shark Thailand")
        self.assertEqual([row["name"] for row in knowledge["products"]], ["BS Watch", "BS Watch"])
        self.assertEqual(knowledge["products"][0]["description"], "watch facts")
        self.assertEqual(knowledge["products"][1]["description"], "another listing")
        self.assertEqual([row["model_name"] for row in knowledge["faqs"]], ["BS Watch"])
        self.assertIsNone(store.resolve_shop_scope("shopee", "222"))
        self.assertIsNone(store.resolve_shop_scope("lazada", "111"))

    def test_shop_without_attached_files_is_not_a_supported_kb_scope(self):
        store = SupabaseStore(url="https://example.supabase.co", publishable_key="test")
        responses = {
            "platforms?select=p_id,code,name&limit=1000": [{"p_id": 1, "code": "shopee", "name": "Shopee"}],
            "shopnames?select=s_id,p_id,shop_id,shop_name,status&limit=1000": [
                {"s_id": 10, "p_id": 1, "shop_id": "111", "shop_name": "No KB Store", "status": "NORMAL"},
            ],
            "shop_files?select=s_id,shop_id,file_name&limit=2000": [],
        }
        store._request = lambda path, payload=None: responses[path]

        self.assertEqual(store.shop_scopes(), [])
        self.assertEqual(store.knowledge_for_shop("shopee", "111"), (None, None))

    def test_same_source_filename_cannot_authorize_two_shops(self):
        store = SupabaseStore(url="https://example.supabase.co", publishable_key="test")
        responses = {
            "platforms?select=p_id,code,name&limit=1000": [{"p_id": 1, "code": "shopee", "name": "Shopee"}],
            "shopnames?select=s_id,p_id,shop_id,shop_name,status&limit=1000": [
                {"s_id": 10, "p_id": 1, "shop_id": "111", "shop_name": "Store A"},
                {"s_id": 20, "p_id": 1, "shop_id": "222", "shop_name": "Store B"},
            ],
            "shop_files?select=s_id,shop_id,file_name&limit=2000": [
                {"s_id": 10, "shop_id": "111", "file_name": "products.xlsx"},
                {"s_id": 20, "shop_id": "222", "file_name": "products.xlsx"},
            ],
        }
        store._request = lambda path, payload=None: responses[path]
        self.assertEqual(store.shop_scopes(), [])


if __name__ == "__main__":
    unittest.main()
