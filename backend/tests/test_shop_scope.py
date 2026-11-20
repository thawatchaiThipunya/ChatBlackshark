import unittest

from backend.app.store import SupabaseStore


class ShopScopeTests(unittest.TestCase):
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
        store._request = lambda path, payload=None: responses[path]
        store.knowledge = lambda: {
            "products": [
                {"name": "BS Watch", "description": "watch facts", "source": {"file": "blackshark.xlsx"}},
                {"name": "Other Phone", "description": "phone facts", "source": {"file": "other.xlsx"}},
                {"name": "Unattributed", "description": "unknown facts", "source": {}},
            ],
            "faqs": [
                {"model_name": "BS Watch", "question": "Q", "answer": "A", "source": {"file": "blackshark.xlsx"}},
                {"model_name": "Other Phone", "question": "Q2", "answer": "A2", "source": {"file": "other.xlsx"}},
            ],
        }

        scope, knowledge = store.knowledge_for_shop("SHOPEE", "111")

        self.assertEqual(scope["shop_name"], "Black Shark Thailand")
        self.assertEqual([row["name"] for row in knowledge["products"]], ["BS Watch"])
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
