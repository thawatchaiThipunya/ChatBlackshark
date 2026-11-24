import json
import unittest

from backend.app.engine_v2 import GeminiAnswerer, ProductChatEngine, supported_wording


class FakeStore:
    def __init__(self, products, faqs=(), keys=("test-key",)):
        self.data = {"products": products, "faqs": list(faqs), "support_models": []}
        self.keys = keys

    def knowledge(self):
        return self.data

    def next_key(self):
        return (self.keys[0], 0, len(self.keys)) if self.keys else (None, None, 0)


class IntentFlowTests(unittest.TestCase):
    def test_all_ten_rate_limited_keys_stop_with_temporary_error(self):
        class RateLimited(Exception):
            code = 429
        used = []
        def generate(key, prompt):
            used.append(key)
            raise RateLimited()
        products = [{"name": "Black Shark GS3", "description": "สาย 22 มม.", "source": {}}]
        store = FakeStore(products)
        store.key_cycle = lambda: [(f"key-{index}", index) for index in range(10)]
        engine = ProductChatEngine(store=store, answerer=GeminiAnswerer(store, generate))

        result = engine.answer("อยากทราบวิธีเปลี่ยนสาย gs3")

        self.assertEqual(result["reason"], "llm_rate_limited")
        self.assertEqual(result["status"], "temporarily_unavailable")
        self.assertFalse(result["handoff_sent"])
        self.assertNotIn("เจ้าหน้าที่", result["answer"])
        self.assertEqual(used, [f"key-{index}" for index in range(10)])

    def test_rate_limited_faq_selection_stops_before_answer_call(self):
        prompts = []
        class RateLimited(Exception):
            code = 429
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "troubleshooting", "topic": "strap", "needs": ["change strap"],
                                   "product_names": ["Black Shark GS3"], "unresolved": False})
            raise RateLimited()
        engine = self.engine([{"name": "Black Shark GS3", "description": "วิธีใส่สาย youtu.be/Oen126kBRzI", "source": {}}],
                             generate, [{"model_name": "Black Shark GS3 Smart Watch", "question": "เปลี่ยนสายได้ไหม",
                                         "answer": "เปลี่ยนสายได้", "source": {}}])

        result = engine.answer("เปลี่ยนสาย GS3 อย่างไร")

        self.assertEqual(result["status"], "temporarily_unavailable")
        self.assertEqual(result["reason"], "llm_rate_limited")
        self.assertEqual(len(prompts), 2)
        self.assertFalse(result["handoff_sent"])

    def engine(self, products, generate, faqs=()):
        store = FakeStore(products, faqs)
        return ProductChatEngine(store=store, answerer=GeminiAnswerer(store, generate))

    def test_comparison_followup_uses_previous_product_scope_when_intent_omits_names(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "comparison", "topic": "battery", "needs": ["battery life"],
                                   "product_names": [], "unresolved": False})
            if "COMPARISON TABLE REFORMAT" in prompt:
                return json.dumps({"answer": "| คุณสมบัติ | Black Shark GS3 | Black Shark GS3 Ultra |\n"
                                           "|---|---|---|\n| แบตเตอรี่ | 21 วัน | 18 วัน |"}, ensure_ascii=False)
            return "GS3 อยู่ได้ 21 วัน ส่วน GS3 Ultra อยู่ได้ 18 วันครับ"
        products = [
            {"name": "Black Shark GS3", "description": "แบตเตอรี่ 21 วัน", "source": {}},
            {"name": "Black Shark GS3 Ultra", "description": "แบตเตอรี่ 18 วัน", "source": {}},
        ]
        history = [{"role": "user", "content": "เทียบ GS3 กับ GS3 Ultra", "product_scope": {
            "question": "เทียบ GS3 กับ GS3 Ultra", "product_names": ["Black Shark GS3", "Black Shark GS3 Ultra"]}},
            {"role": "assistant", "content": "เทียบให้แล้วครับ"}]

        result = self.engine(products, generate).answer("ตัวไหนแบตอึดกว่า", history=history)

        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["intent_analysis"]["product_names"],
                         ["Black Shark GS3", "Black Shark GS3 Ultra"])
        self.assertIn("แบตเตอรี่ 21 วัน", prompts[-1])
        self.assertIn("แบตเตอรี่ 18 วัน", prompts[-1])

    def test_new_explicit_edition_does_not_reuse_previous_model_as_fact_source(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "product_info", "topic": "GPS battery", "needs": ["GPS battery"],
                                   "product_names": [], "product_mentions": ["Ultra"], "unresolved": False})
            return "รุ่นนี้ยังไม่มีข้อมูล GPS ครับ"
        products = [
            {"name": "Black Shark RUN", "description": "GPS 10 ชั่วโมง", "source": {}},
            {"name": "Black Shark GS3 Ultra", "description": "GPS 25 ชั่วโมง", "source": {}},
        ]

        result = self.engine(products, generate).answer(
            "ถ้าเอา Ultra เปิด GPS แบตได้กี่ชม", product_name="Black Shark RUN")

        self.assertNotIn("Black Shark RUN", result["intent_analysis"]["product_names"])
        self.assertFalse(any("GPS 10 ชั่วโมง" in prompt for prompt in prompts[1:]))

    def test_explicit_same_model_keeps_current_kb_context_if_intent_omits_name(self):
        def generate(key, prompt):
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "product_info", "topic": "battery", "needs": ["battery life"],
                                   "product_names": [], "product_mentions": ["GS3"], "unresolved": False})
            return "GS3 ใช้ได้ 21 วันครับ"
        engine = self.engine([{"name": "Black Shark GS3", "description": "แบตเตอรี่ 21 วัน", "source": {}}], generate)

        result = engine.answer("GS3 แบตได้กี่วัน", product_name="Black Shark GS3")

        self.assertEqual(result["intent_analysis"]["product_names"], ["Black Shark GS3"])
        self.assertEqual(result["status"], "answered")

    def test_singular_followup_does_not_assume_one_of_two_prior_models(self):
        def generate(key, prompt):
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "compatibility", "topic": "swimming", "needs": ["swimming"],
                                   "product_names": [], "unresolved": False})
            return "ยังระบุรุ่นไม่ได้ครับ"
        products = [{"name": "Black Shark GS3", "description": "5ATM", "source": {}},
                    {"name": "Black Shark GS3 Ultra", "description": "5ATM", "source": {}}]
        history = [{"role": "user", "content": "เทียบสองรุ่น", "product_scope": {
            "product_names": ["Black Shark GS3", "Black Shark GS3 Ultra"]}},
            {"role": "assistant", "content": "เทียบแล้วครับ"}]

        result = self.engine(products, generate).answer("ใส่ว่ายน้ำได้ไหม", history=history)

        self.assertEqual(result["intent_analysis"]["product_names"], [])

    def test_usage_answer_can_use_product_guide_alongside_faq(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "troubleshooting", "topic": "change watch strap",
                    "needs": ["how to change strap"], "product_names": ["Black Shark GS3"], "unresolved": False})
            if "Select every FAQ" in prompt:
                return '{"index":0}'
            return "GS3 เปลี่ยนสายได้ครับ ดูวิธีใส่สายที่ https://youtu.be/Oen126kBRzI"
        engine = self.engine([{"name": "Black Shark GS3",
            "description": "วิธีการใส่สายนาฬิกา: https://youtu.be/Oen126kBRzI",
            "source": {"file": "product.xlsx"}}], generate,
            [{"model_name": "Black Shark GS3 Smart Watch", "question": "เปลี่ยนสายได้ไหม",
              "answer": "สามารถเปลี่ยนสายได้", "source": {"file": "faq.xlsx"}}])

        result = engine.answer("อยากทราบวิธีเปลี่ยนสาย gs3")

        self.assertEqual(result["status"], "answered")
        self.assertIn("youtu.be/Oen126kBRzI", result["answer"])
        self.assertIn("วิธีการใส่สายนาฬิกา", prompts[-1])
        self.assertIn("https://youtu.be/Oen126kBRzI", prompts[-1])
        self.assertIn("สามารถเปลี่ยนสายได้", prompts[-1])
        self.assertEqual(len(result["sources"]), 2)

    def test_usage_answer_uses_product_guide_when_faq_has_no_match(self):
        def generate(key, prompt):
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "troubleshooting", "topic": "change watch strap",
                    "needs": ["how to change strap"], "product_names": ["Black Shark GS3"], "unresolved": False})
            if "Select every FAQ" in prompt:
                return '{"index":null}'
            return "ดูวิธีใส่สาย GS3 ได้ที่ https://youtu.be/Oen126kBRzI"
        engine = self.engine([{"name": "Black Shark GS3",
            "description": "วิธีการใส่สายนาฬิกา: https://youtu.be/Oen126kBRzI",
            "source": {"file": "product.xlsx"}}], generate,
            [{"model_name": "Black Shark GS3 Smart Watch", "question": "เปลี่ยนสายได้ไหม",
              "answer": "สามารถเปลี่ยนสายได้", "source": {"file": "faq.xlsx"}}])

        result = engine.answer("อยากทราบวิธีเปลี่ยนสาย gs3")

        self.assertEqual(result["status"], "answered")
        self.assertIn("youtu.be/Oen126kBRzI", result["answer"])

    def test_mongo_color_evidence_is_not_available_to_product_answer(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "product_info", "topic": "available colors",
                                   "needs": ["available colors"],
                                   "product_names": ["Black Shark GS3 Ultra"], "unresolved": False})
            return "เอกสารของร้านยังไม่ได้ระบุสีของ Black Shark GS3 Ultra ครับ"
        product = {"name": "Black Shark GS3 Ultra", "description": "นาฬิกา GPS", "source": {"file": "kb.xlsx"}}
        faq = {"model_name": "Black Shark GS3 Ultra Smart Watch", "question": "มี GPS ไหม",
               "answer": "มี GPS", "source": {"file": "faq.xlsx", "cell": "C3"}}
        listing = {"item_id": "123", "name": "Black Shark GS3 Ultra", "description": "",
                   "availability": "available", "tier_variation": [],
                   "models": [{"model_name": "Black", "available": 3},
                              {"model_name": "Glacier Silver", "available": 4}]}
        result = self.engine([product], generate, [faq]).answer(
            "Black Shark GS3 Ultra มีสีอะไรบ้าง", commerce_lookup=lambda *_: {"products": [listing]})
        self.assertNotIn("Glacier Silver", prompts[-1])
        self.assertNotIn("Glacier Silver", result["answer"])
        self.assertEqual(result["sources"], [product["source"]])

    def test_faq_selection_excludes_other_catalog_models_with_shared_name_prefix(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "troubleshooting", "topic": "charging",
                                   "needs": ["charging steps"],
                                   "product_names": ["Black Shark GS3"], "unresolved": False})
            if "Select every FAQ" in prompt:
                return '{"indices":[0]}'
            return "ลองตรวจขั้วชาร์จของ GS3 ครับ"
        products = [
            {"name": "Black Shark GS3", "description": "GS3 facts", "source": {}},
            {"name": "Black Shark GS3 Sport", "description": "Sport facts", "source": {}},
            {"name": "Black Shark GS3 Ultra", "description": "Ultra facts", "source": {}},
        ]
        faqs = [
            {"model_name": "Black Shark GS3 Sport Smart Watch", "question": "ชาร์จไม่เข้า",
             "answer": "Sport-only steps", "source": {"cell": "C14"}},
            {"model_name": "Black Shark GS3 Smart Watch", "question": "ชาร์จไม่เข้า",
             "answer": "GS3-only steps", "source": {"cell": "C22"}},
            {"model_name": "Black Shark GS3 Ultra Smart Watch", "question": "ชาร์จไม่เข้า",
             "answer": "Ultra-only steps", "source": {"cell": "C38"}},
        ]
        result = self.engine(products, generate, faqs).answer("GS3 ชาร์จไม่เข้า")
        selection_prompt = next(prompt for prompt in prompts if "Select every FAQ" in prompt)
        self.assertIn("GS3-only steps", selection_prompt)
        self.assertNotIn("Sport-only steps", selection_prompt)
        self.assertNotIn("Ultra-only steps", selection_prompt)
        self.assertEqual(result["matched_question"], "ชาร์จไม่เข้า")

    def test_intent_extracts_needs_and_product_from_this_chat(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            return json.dumps({"intent": "compatibility", "topic": "swimming", "needs": ["can swim"],
                               "product_names": ["Black Shark GS3 Ultra"], "unresolved": False})
        store = FakeStore([{"name": "Black Shark GS3 Ultra", "description": "5ATM", "source": {}}])
        plan, reason = GeminiAnswerer(store, generate).extract_intent(
            "ใส่ลงสระได้ปะ", [{"role": "assistant", "content": "รุ่นนี้ครับ", "product_name": "Black Shark GS3 Ultra"}],
            ["Black Shark GS3 Ultra"])
        self.assertEqual(reason, "intent_resolved")
        self.assertEqual(plan["needs"], ["can swim"])
        self.assertEqual(plan["product_names"], ["Black Shark GS3 Ultra"])
        self.assertIn("Black Shark GS3 Ultra", prompts[0])

    def test_intent_keeps_only_product_mentions_present_in_customer_text(self):
        def generate(key, prompt):
            return json.dumps({"intent": "stock_availability", "topic": "S3 Special Edition stock",
                "needs": ["silver stock"], "product_names": [],
                "product_mentions": ["S3 Special Edition", "Black Shark GS3 Ultra"],
                "unresolved": False})
        store = FakeStore([])
        plan, _ = GeminiAnswerer(store, generate).extract_intent(
            "Black Shark S3 Special Edition สีเงินมีของไหม", [], [])

        self.assertEqual(plan["product_mentions"], ["S3 Special Edition"])

    def test_explicit_other_store_request_is_answered_without_handoff_or_product_search(self):
        prompts = []

        def generate(key, prompt):
            prompts.append(prompt)
            return json.dumps({
                "intent": "product_search", "topic": "Youpin inventory", "needs": ["list products"],
                "product_names": [], "product_mentions": [], "shop_scope": "other_store",
                "requested_store_name": "Youpin Online Store", "unresolved": False,
            }, ensure_ascii=False)

        engine = self.engine([{"name": "Black Shark GS3", "description": "นาฬิกา", "source": {}}], generate)
        engine.store.scope = {"platform": "shopee", "shop_id": "current-shop-id",
                             "shop_name": "Black Shark Thailand"}

        result = engine.answer("ขอดูสินค้าของร้าน Youpin Online Store หน่อยครับ",
                               store_label="Black Shark Thailand")

        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["reason"], "other_store_requested")
        self.assertFalse(result["handoff_sent"])
        self.assertFalse(result["handoff_mock"])
        self.assertEqual(result["sources"], [])
        self.assertIsNone(result.get("product_name"))
        self.assertIn("Black Shark Thailand", result["answer"])
        self.assertIn("Youpin Online Store", result["answer"])
        self.assertNotIn("Black Shark GS3", result["answer"])
        self.assertEqual(len(prompts), 1, "other-store intent should stop before product search or answer generation")
        self.assertIn('"shop_id": "current-shop-id"', prompts[0])
        self.assertIn('"shop_name": "Black Shark Thailand"', prompts[0])

    def test_intent_distinguishes_another_brand_from_another_store(self):
        prompts = []

        def generate(key, prompt):
            prompts.append(prompt)
            return json.dumps({
                "intent": "product_search", "topic": "watch brand availability", "needs": ["Kieslect watch"],
                "product_names": [], "product_mentions": [], "shop_scope": "current_store",
                "requested_store_name": "", "category": "smart watch", "unresolved": False,
            }, ensure_ascii=False)

        store = FakeStore([])
        plan, reason = GeminiAnswerer(store, generate).extract_intent(
            "ร้านนี้มีนาฬิกา Kieslect ขายไหม", [], [], product_categories=["smart watch"],
            active_shop={"platform": "shopee", "shop_id": "active-shop-id", "shop_name": "Current Shop"})

        self.assertEqual(reason, "intent_resolved")
        self.assertEqual(plan["shop_scope"], "current_store")
        self.assertIsNone(plan["requested_store_name"])
        self.assertIn("brand or product name alone does not mean another store", prompts[0].casefold())
        self.assertIn('"shop_id": "active-shop-id"', prompts[0])
        self.assertIn("defines which shop the customer is currently speaking to", prompts[0].casefold())

    def test_requested_store_name_must_be_literal_customer_text(self):
        def generate(key, prompt):
            return json.dumps({
                "intent": "product_search", "topic": "store inventory", "needs": ["list products"],
                "product_names": [], "product_mentions": [], "shop_scope": "other_store",
                "requested_store_name": "Invented Merchant", "unresolved": False,
            }, ensure_ascii=False)

        plan, _ = GeminiAnswerer(FakeStore([]), generate).extract_intent(
            "ขอดูสินค้าของร้านอื่นหน่อย", [], [])

        self.assertEqual(plan["shop_scope"], "other_store")
        self.assertIsNone(plan["requested_store_name"])

    def test_compatibility_answer_prompt_requires_direct_verdict_before_specs(self):
        prompts = []

        def generate(key, prompt):
            prompts.append(prompt)
            if prompt.startswith("ANSWER REVIEW"):
                return json.dumps({"answer": "ได้ครับ รุ่นนี้มีโหมดว่ายน้ำและมาตรฐานกันน้ำ 5ATM ตามข้อมูลร้าน"},
                                  ensure_ascii=False)
            return "ได้ครับ รุ่นนี้มีโหมดว่ายน้ำและมาตรฐานกันน้ำ 5ATM ตามข้อมูลร้าน"

        store = FakeStore([])
        store.runtime_config = lambda: {}
        answer, reason = GeminiAnswerer(store, generate).answer(
            "ใส่ว่ายน้ำได้ไหม", "โหมดกีฬา: ว่ายน้ำ\nกันน้ำ 5ATM", "Black Shark GS3 Ultra",
            intent="compatibility", needs=["swimming suitability"])

        self.assertEqual(reason, "llm_grounded")
        self.assertTrue(answer.startswith("ได้ครับ"))
        self.assertIn("must begin with a clear verdict", prompts[0].casefold())
        self.assertIn("answer the customer's yes/no question before listing specifications", prompts[0].casefold())
        self.assertIn("must begin with a clear, source-supported verdict", prompts[1].casefold())

    def test_text_question_for_product_missing_from_kb_does_not_use_scoped_mongo_evidence(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "stock_availability", "topic": "S3 Special Edition silver stock",
                    "needs": ["silver variant availability"], "product_names": [],
                    "product_mentions": ["S3 Special Edition"], "unresolved": False})
            return ""
        product = {"item_id": "55355455002", "name": "Black Shark S3 Special Edition",
            "description": "Listing facts", "item_status": "NORMAL", "availability": "available",
            "tier_variation": [], "models": [{"model_name": "Silver+สแตนเลสเงิน", "available": 71,
                "stock_known": True, "model_status": "MODEL_NORMAL"}]}
        calls = []
        def commerce_lookup(intent, names, cards, mentions):
            calls.append((intent, names, cards, mentions))
            return {"products": [product]}
        engine = self.engine([], generate)

        result = engine.answer("Black Shark S3 Special Edition สีเงินมีของไหมครับ",
                               commerce_lookup=commerce_lookup)

        self.assertEqual(result["status"], "handoff_mock")
        self.assertNotIn("สีเงินมีสินค้าพร้อมส่ง", result["answer"])
        self.assertEqual(calls[0][3], ["S3 Special Edition"])
        self.assertEqual(result["sources"], [])

    def test_intent_extracts_category_and_complete_inventory_request(self):
        def generate(key, prompt):
            return json.dumps({"intent": "product_search", "topic": "watch inventory", "needs": ["all watch models"],
                               "product_names": [], "category": "smart watch", "list_all": True,
                               "unresolved": False})
        store = FakeStore([])
        plan, reason = GeminiAnswerer(store, generate).extract_intent(
            "ในร้านมีนาฬิกาอะไรบ้างครับ", [], [], product_categories=["smart watch", "Pad"])
        self.assertEqual(reason, "intent_resolved")
        self.assertEqual(plan["category"], "smart watch")
        self.assertTrue(plan["list_all"])

    def test_category_inventory_contains_every_category_match(self):
        prompts = []
        names = ["Black Shark GS3", "Black Shark A3", "Black Shark S3"]
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "product_search", "topic": "watch inventory", "needs": ["all watches"],
                                   "product_names": [], "category": "smart watch", "list_all": True,
                                   "unresolved": False})
            return "มีนาฬิกาตามข้อมูลร้านดังนี้ครับ " + " ".join(names)
        products = [{"name": name, "description": "ประเภทสินค้า: smart watch", "category": "smart watch", "source": {}}
                    for name in names]
        products.append({"name": "Black Shark Pad 7Pro LTE", "description": "ประเภทสินค้า: Pad", "category": "Pad", "source": {}})
        result = self.engine(products, generate).answer("มีนาฬิกาอะไรบ้าง")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["intent_analysis"]["product_names"], names)
        self.assertTrue(all(name in result["answer"] for name in names))
        self.assertNotIn("Black Shark Pad 7Pro LTE", result["answer"])
        self.assertEqual(len(prompts), 2)

    def test_complete_category_list_keeps_all_names_with_compact_product_facts(self):
        prompts = []
        products = [{"name": f"Watch model {index}", "description": "ประเภทสินค้า: smart watch\n" + ("รายละเอียด " * 1500),
                     "category": "smart watch", "source": {}} for index in range(14)]
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "product_search", "topic": "watch inventory", "needs": ["list watches"],
                                   "product_names": [], "category": "smart watch", "list_all": True,
                                   "unresolved": False})
            return "มีนาฬิกา 14 รุ่นครับ " + ", ".join(item["name"] for item in products)
        result = self.engine(products, generate).answer("นาฬิกามีรุ่นอะไรบ้าง")
        self.assertEqual(len(result["intent_analysis"]["product_names"]), 14)
        self.assertIn("count=14", prompts[-1])
        self.assertLess(len(prompts[-1]), 30000)
        self.assertTrue(all(item["name"] in prompts[-1] for item in products))

    def test_recommendation_selection_sends_only_the_intent_selected_category(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "product_search", "topic": "running watch", "needs": ["GPS"],
                    "product_names": [], "category": "smart watch", "list_all": False, "unresolved": False})
            if "Find products in this one store" in prompt:
                return json.dumps({"product_names": ["Black Shark GS3"]})
            return "GS3 มีข้อมูล GPS ตามคู่มือครับ"
        products = [
            {"name": "Black Shark GS3", "description": "มี GPS", "category": "smart watch", "source": {}},
            {"name": "Black Shark Pad 7", "description": "ไม่มี GPS", "category": "Pad", "source": {}},
        ]
        result = self.engine(products, generate).answer("มีนาฬิกา GPS แนะนำไหม")
        self.assertEqual(result["status"], "answered")
        self.assertIn('"name": "Black Shark GS3"', prompts[1])
        self.assertNotIn("Black Shark Pad 7", prompts[1])

    def test_unscoped_product_selection_keeps_prompt_size_bounded(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            return '{"product_names": ["Black Shark 0"]}'
        products = [{"name": f"Black Shark {index}", "description": "ข้อมูลสินค้า " + ("x" * 5000)}
                    for index in range(44)]
        GeminiAnswerer(FakeStore(products), generate).select_products("หารุ่นที่เหมาะ", ["เงื่อนไข"], products)
        self.assertLess(len(prompts[0]), 70000)

    def test_card_model_is_added_to_a_one_model_comparison(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "comparison", "topic": "battery", "needs": ["battery life"],
                                   "product_names": ["Black Shark GS3 Ultra"], "unresolved": False})
            return "| รายการ | Black Shark GS3 | Black Shark GS3 Ultra |\n|---|---|---|\n| แบต | 21 วัน | 18 วัน |"
        products = [
            {"name": "Black Shark GS3", "description": "แบต 21 วัน", "category": "smart watch", "source": {}},
            {"name": "Black Shark GS3 Ultra", "description": "แบต 18 วัน", "category": "smart watch", "source": {}},
        ]
        result = self.engine(products, generate).answer(
            "แล้วต่างจาก Ultra ยังไง", product_name="Black Shark GS3")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["intent_analysis"]["product_names"], ["Black Shark GS3", "Black Shark GS3 Ultra"])
        self.assertIn("แบต 21 วัน", prompts[-1])
        self.assertIn("แบต 18 วัน", prompts[-1])

    def test_comparison_prompt_keeps_each_product_fact_under_its_model(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            return "ตามข้อมูลที่แยกตามรุ่นครับ"
        products = [
            {"name": "Black Shark GS3", "description": "อุปกรณ์ภายในกล่อง: สายผ้า", "source": {}},
            {"name": "Black Shark GS3 Ultra", "description": "อุปกรณ์ภายในกล่อง: ตัวเรือน สายชาร์จ คู่มือ", "source": {}},
        ]
        def intent(key, prompt):
            prompts.append(prompt)
            return json.dumps({"intent": "comparison", "topic": "box contents", "needs": ["included straps"],
                               "product_names": ["Black Shark GS3", "Black Shark GS3 Ultra"], "unresolved": False})
        engine = self.engine(products, intent)
        engine.answer("ตัวไหนแถมสาย", product_name="Black Shark GS3")
        self.assertIn("สินค้า: Black Shark GS3\nอุปกรณ์ภายในกล่อง: สายผ้า", prompts[-1])
        self.assertIn("สินค้า: Black Shark GS3 Ultra\nอุปกรณ์ภายในกล่อง: ตัวเรือน สายชาร์จ คู่มือ", prompts[-1])

    def test_package_evidence_is_repeated_at_prompt_end_and_conflicts_are_explicit(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "product_info", "topic": "box contents", "needs": ["package contents"],
                    "product_names": ["Black Shark GS3"], "unresolved": False})
            return "ตรวจสอบรายการในกล่องก่อนสั่งซื้อครับ"
        description = (
            "จุดเด่นสินค้า:\n"
            "1. มาพร้อมสายนาฬิกาถึง 2 เส้น เลือกใช้ได้อย่างอิสระ\n"
            "   สายซิลิโคน เหมาะสำหรับออกกำลังกาย\n"
            "   สายผ้า Woven Strap ระบายอากาศดี\n\n"
            "อุปกรณ์ที่ได้รับในแพ็กเกจ:\n"
            "1. Black Shark GS3\n2. สายผ้า (Woven Strap)\n3. สายชาร์จ\n4. คู่มือการใช้งาน\n\n"
            "หมายเหตุสำคัญ: ข้อมูลอื่น"
        )
        engine = self.engine([{"name": "Black Shark GS3", "description": description, "source": {}}], generate)

        result = engine.answer("GS3 ในกล่องมีสายอะไรบ้าง")

        self.assertEqual(result["status"], "answered")
        final_prompt = prompts[-1]
        package_check = final_prompt.split("PACKAGE EVIDENCE CHECK (per exact product)", 1)[1]
        self.assertIn("สายผ้า (Woven Strap)", package_check)
        self.assertIn("มาพร้อมสายนาฬิกาถึง 2 เส้น", package_check)
        self.assertIn("source mentions accessory bundle outside the package list", package_check)

    def test_unselected_faqs_are_not_shown_as_answer_sources(self):
        def generate(key, prompt):
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "product_info", "topic": "box contents", "needs": ["package contents"],
                    "product_names": ["Black Shark GS3"], "unresolved": False})
            if "Select every FAQ" in prompt:
                return '{"indices": []}'
            return "ในกล่องมีตัวเรือน สายผ้า สายชาร์จ และคู่มือครับ"
        product_source = {"file": "product.xlsx", "cell": "B3"}
        faqs = [
            {"model_name": "Black Shark GS3", "question": "ปุ่มกดไม่ได้", "answer": "ลองทำความสะอาดปุ่ม", "source": {"file": "faq.xlsx", "cell": "C9"}},
            {"model_name": "Black Shark GS3", "question": "ชาร์จไม่เข้า", "answer": "ตรวจสอบขั้วชาร์จ", "source": {"file": "faq.xlsx", "cell": "C17"}},
        ]
        engine = self.engine([{"name": "Black Shark GS3", "description": "อุปกรณ์ที่ได้รับในแพ็กเกจ:\nสายผ้า\nสายชาร์จ\nคู่มือ", "source": product_source}], generate, faqs)

        result = engine.answer("GS3 ในกล่องมีอะไรบ้าง")

        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["sources"], [product_source])

    def test_card_listing_identity_anchors_answer_without_using_listing_facts(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "compatibility", "topic": "swimming", "needs": ["can swim"],
                    "product_names": [], "unresolved": False})
            return "รุ่นนี้มีข้อมูลการกันน้ำจากรายการสินค้าและข้อมูลรุ่นครับ"
        products = [{"name": "Black Shark GS3 Ultra", "description": "กันน้ำ 5ATM", "source": {"file": "kb.xlsx"}}]
        result = self.engine(products, generate).answer(
            "ใส่ว่ายน้ำได้ไหม", product_name="Black Shark GS3 Ultra",
            card_context=[{"item_id": "123456789", "name": "Black Shark GS3 Ultra", "description": "รองรับโหมดว่ายน้ำ"}])
        self.assertEqual(result["status"], "answered")
        self.assertIn("กันน้ำ 5ATM", prompts[-1])
        self.assertNotIn("รองรับโหมดว่ายน้ำ", prompts[-1])
        self.assertIn("CARD_PRODUCT_IDENTITY", prompts[-1])

    def test_unmapped_card_does_not_get_answered_as_a_similarly_named_kb_product(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "product_info", "topic": "display", "needs": ["display size"],
                    "product_names": ["Black Shark S3"], "unresolved": False})
            return "การ์ดนี้ระบุชื่อ Black Shark S3 Special Edition แต่ไม่มีขนาดจอในข้อมูลรายการครับ"
        products = [{"name": "Black Shark S3", "description": "หน้าจอ AMOLED 1.43 นิ้ว", "source": {}}]
        result = self.engine(products, generate).answer(
            "จอกี่นิ้ว", card_context=[{"item_id": "123456789", "name": "Black Shark S3 Special Edition",
                                        "description": "edition ในรายการขาย"}])
        self.assertEqual(result["status"], "need_model")
        self.assertIsNone(result.get("product_name"))
        self.assertNotIn("หน้าจอ AMOLED 1.43 นิ้ว", prompts[-1])
        self.assertNotIn("edition ในรายการขาย", "\n".join(prompts))

    def test_unmapped_listing_cannot_supply_product_facts_or_a_product_card(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "product_info", "topic": "product card", "needs": ["what is this"],
                                   "product_names": [], "unresolved": False})
            return "การ์ดนี้เป็นรายการแท็บเล็ต Pad 7 ครับ"

        listing = {"item_id": "43524489240", "name": "Black Shark Pad 7 / Pad 7 Pro",
                   "description": "รายการขายแท็บเล็ต", "availability": "available",
                   "image_url": "https://example.com/pad7.jpg", "models": []}
        engine = self.engine([{"name": "Black Shark GS3", "description": "นาฬิกา", "source": {}}], generate)

        result = engine.answer("ช่วยดูการ์ดนี้", card_context=[listing],
                               commerce_lookup=lambda intent, names, cards, mentions: {"products": [listing]})

        self.assertEqual(result["status"], "need_model")
        self.assertEqual(result.get("product_cards", []), [])
        self.assertNotIn("รายการขายแท็บเล็ต", "\n".join(prompts))

    def test_card_troubleshooting_uses_the_matching_kb_faq(self):
        prompts = []
        faq = {"model_name": "Black Shark GS3 Ultra Smart Watch Global Version",
               "question": "ปุ่มกดค้างทำอย่างไร", "answer": "กดปุ่ม HOME ค้าง 10 วินาทีเพื่อรีสตาร์ต",
               "source": {"file": "faq.xlsx", "cell": "C36"}}
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "troubleshooting", "topic": "stuck button", "needs": ["reset steps"],
                    "product_names": [], "unresolved": False})
            if "Select every FAQ" in prompt:
                return '{"index": 0}'
            return "ลองกดปุ่ม HOME ค้างไว้ 10 วินาทีเพื่อรีสตาร์ตครับ"
        products = [{"name": "Black Shark GS3 Ultra", "description": "หน้าจอ 60Hz", "source": {"file": "product.xlsx"}}]
        result = self.engine(products, generate, [faq]).answer(
            "ปุ่มค้างทำยังไง", product_name="Black Shark GS3 Ultra",
            card_context=[{"item_id": "111111111", "name": "Black Shark GS3 Ultra Smart Watch",
                           "product_name": "Black Shark GS3 Ultra", "description": "รายการการ์ดสินค้า"}])
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["matched_question"], faq["question"])
        self.assertIn(faq["answer"], prompts[-1])

    def test_answers_general_multi_model_question_from_models_mentioned_in_history(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "product_info", "topic": "global version", "needs": ["version"],
                    "product_names": ["Black Shark T23", "Black Shark GS3 Ultra"], "unresolved": False})
            return "ข้อมูลเอกสารระบุชื่อรุ่นแยกตามรายการครับ"
        products = [
            {"name": "Black Shark T23", "description": "T23 product facts", "source": {}},
            {"name": "Black Shark GS3 Ultra", "description": "GS3 Ultra product facts", "source": {}},
        ]
        faqs = [
            {"model_name": "Black Shark T23 In Ear Global Version", "question": "Bluetooth", "answer": "reset", "source": {}},
            {"model_name": "Black Shark GS3 Ultra Smart Watch Global Version", "question": "GPS", "answer": "reset", "source": {}},
        ]
        result = self.engine(products, generate, faqs).answer(
            "ที่คุยมาก่อนเป็น Global Version ทั้งคู่ไหม", history=[
                {"role": "assistant", "content": "Black Shark T23", "product_name": "Black Shark T23"},
                {"role": "assistant", "content": "Black Shark GS3 Ultra", "product_name": "Black Shark GS3 Ultra"},
            ])
        self.assertEqual(result["intent_analysis"]["product_names"], ["Black Shark T23", "Black Shark GS3 Ultra"])
        self.assertIn("FAQ_MODEL_LABELS", prompts[-1])
        self.assertIn("Global Version", prompts[-1])
        self.assertIn("do not infer stock/version", prompts[-1])

    def test_compatibility_answer_ignores_symptom_faq_that_conflicts_with_product_spec(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "compatibility", "topic": "LINE calling", "needs": ["LINE calls on iOS"],
                                   "product_names": ["Black Shark RUN"], "unresolved": False})
            if "Select every FAQ" in prompt:
                return '{"indices":[0]}'
            if "ANSWER REVIEW" in prompt:
                return '{"answer":"Black Shark RUN รับสาย LINE บน iOS ได้ครับ ส่วน Android ยังอยู่ระหว่างการพัฒนา"}'
            return "Black Shark RUN รับสาย LINE ผ่านนาฬิกาบน iOS ได้ครับ ส่วน Android ยังอยู่ระหว่างการพัฒนา"
        product_facts = (
            "ฟีเจอร์ ENC Bluetooth Calling รองรับการรับสาย LINE ผ่านนาฬิกาได้โดยตรงสำหรับ iOS "
            "ส่วน Android ยังอยู่ระหว่างการพัฒนา"
        )
        faqs = [
            {"model_name": "Black Shark RUN Smart Watch Global Version Black", "question": "ปัญหาโทร LINE ไม่ได้",
             "answer": "รับสายไลน์ไม่ได้ มีแค่แจ้งเตือน; ไม่รองรับการโทร-รับสายผ่านแอปโซเชียลมีเดีย",
             "source": {"cell": "C17"}},
        ]
        engine = self.engine([{"name": "Black Shark RUN", "description": product_facts, "source": {}}],
                             generate, faqs)
        engine.store.runtime_config = lambda: {}

        result = engine.answer("รับสาย LINE บน iPhone ได้ไหม")

        self.assertEqual(result["status"], "answered")
        self.assertIn("รับสาย LINE บน iOS ได้", result["answer"])
        self.assertIn(product_facts, prompts[-1])
        self.assertFalse(any("FAQ_EVIDENCE" in prompt for prompt in prompts))
        self.assertFalse(any("Select every FAQ" in prompt for prompt in prompts))
        self.assertEqual(result["sources"], [{}])

    def test_store_support_answers_from_relevant_product_faq_before_escalating(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "store_support", "topic": "warranty", "needs": ["warranty period"],
                                   "product_names": ["Black Shark GS3"], "unresolved": False})
            if "Select every FAQ" in prompt:
                return '{"indices":[0]}'
            if "ANSWER REVIEW" in prompt:
                return '{"answer":"Black Shark GS3 รับประกัน 1 ปีครับ"}'
            return "Black Shark GS3 รับประกัน 1 ปีครับ"
        faq = {"model_name": "Black Shark GS3 Smart Watch", "question": "รับประกันกี่ปี",
               "answer": "รับประกัน 1 ปี", "source": {"cell": "F4"}}
        engine = self.engine([{"name": "Black Shark GS3", "description": "สมาร์ทวอทช์", "source": {"cell": "B3"}}],
                             generate, [faq])
        engine.store.runtime_config = lambda: {}

        result = engine.answer("GS3 รับประกันกี่ปี")

        self.assertEqual(result["status"], "answered")
        self.assertIn("รับประกัน 1 ปี", result["answer"])
        self.assertEqual(result["sources"], [{"cell": "B3"}, {"cell": "F4"}])
        self.assertTrue(any("FAQ_EVIDENCE" in prompt for prompt in prompts))

    def test_store_support_handoff_keeps_canonical_intent_and_records_case(self):
        def generate(key, prompt):
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "store_support", "topic": "refund", "needs": ["refund request"],
                                   "product_names": ["Black Shark GS3"], "unresolved": False})
            if "Select every FAQ" in prompt:
                return '{"indices":[]}'
            if "ANSWER REVIEW" in prompt:
                return '{"answer":"ข้อมูลการคืนเงินส่วนนี้ต้องให้เจ้าหน้าที่ตรวจสอบครับ"}'
            return "ข้อมูลการคืนเงินส่วนนี้ต้องให้เจ้าหน้าที่ตรวจสอบครับ"
        store = FakeStore([{"name": "Black Shark GS3", "description": "สมาร์ทวอทช์", "source": {}}], [])
        store.escalate = lambda **kwargs: {"id": 23}
        engine = ProductChatEngine(store=store, answerer=GeminiAnswerer(store, generate))

        result = engine.answer("GS3 ขอคืนเงินได้ไหม", session_id="session-1")

        self.assertEqual(result["status"], "escalated")
        self.assertEqual(result["intent"], "escalation")
        self.assertEqual(result["case_id"], 23)
        self.assertTrue(result["handoff_sent"])

    def test_answer_review_corrects_thai_and_removes_unsupported_claim(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            if "ANSWER REVIEW" in prompt:
                return '{"answer":"Black Shark GS3 มีโหมดว่ายน้ำครับ แต่เอกสารไม่ได้ระบุว่าเหมาะกับการดำน้ำลึก"}'
            return "Black Shark GS3 มีโหมดว่ายน้ำและเหมาะกับการดำน้ำลึกครับ"
        store = FakeStore([])
        store.runtime_config = lambda: {}
        answerer = GeminiAnswerer(store, generate)

        answer, reason = answerer.answer("GS3 ดำน้ำได้ไหม", "Black Shark GS3\nโหมดกีฬา: ว่ายน้ำ", product_name="Black Shark GS3")

        self.assertEqual(reason, "llm_grounded")
        self.assertIn("ไม่ได้ระบุ", answer)
        self.assertIn("unsupported", prompts[-1])

    def test_comparison_answer_is_reformatted_as_a_real_markdown_table(self):
        prompts = []
        bullet_answer = (
            "GS3 ใช้งานทั่วไปได้สูงสุด 21 วัน ส่วน GS3 Ultra ได้ 18 วันครับ"
        )
        table_answer = (
            "| คุณสมบัติ | Black Shark GS3 | Black Shark GS3 Ultra |\n"
            "|---|---|---|\n"
            "| แบตเตอรี่ใช้งานทั่วไป | สูงสุด 21 วัน | สูงสุด 18 วัน |"
        )

        def generate(key, prompt):
            prompts.append(prompt)
            if "ANSWER REVIEW" in prompt:
                return json.dumps({"answer": bullet_answer}, ensure_ascii=False)
            if "COMPARISON TABLE REFORMAT" in prompt:
                return json.dumps({"answer": table_answer}, ensure_ascii=False)
            return bullet_answer

        store = FakeStore([])
        store.runtime_config = lambda: {}
        answerer = GeminiAnswerer(store, generate)

        answer, reason = answerer.answer(
            "GS3 กับ GS3 Ultra แบตต่างกันยังไง",
            "สินค้า: Black Shark GS3\nแบตเตอรี่ใช้งานทั่วไปสูงสุด 21 วัน\n\n"
            "สินค้า: Black Shark GS3 Ultra\nแบตเตอรี่ใช้งานทั่วไปสูงสุด 18 วัน",
            product_name="Black Shark GS3 / Black Shark GS3 Ultra",
            intent="comparison",
            needs=["battery life"],
        )

        self.assertEqual(reason, "llm_grounded")
        self.assertIn("| คุณสมบัติ | Black Shark GS3 | Black Shark GS3 Ultra |", answer)
        self.assertTrue(any("COMPARISON TABLE REFORMAT" in prompt for prompt in prompts))

    def test_order_status_does_not_claim_all_orders_when_selection_is_ambiguous(self):
        def generate(key, prompt):
            return json.dumps({"intent": "order_status", "topic": "delivery status", "needs": ["status"],
                               "product_names": [], "unresolved": False})

        engine = self.engine([], generate)
        result = engine.answer("ของที่สั่งส่งถึงไหนแล้วครับ", commerce_lookup=lambda *args: {
            "orders": [
                {"order_sn": "ORDER-ONE", "items": [{"name": "Black Shark GS3"}]},
                {"order_sn": "ORDER-TWO", "items": [{"name": "Black Shark S3"}]},
            ],
            "needs_clarification": True,
        })

        self.assertEqual(result["status"], "need_model")
        self.assertEqual(result["reason"], "order_context_ambiguous")
        self.assertIn("เลขคำสั่งซื้อ", result["answer"])

    def test_troubleshooting_can_resolve_model_from_one_customer_order_then_use_kb_faq(self):
        prompts = []

        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "troubleshooting", "topic": "screen problem",
                                   "needs": ["screen troubleshooting"], "product_names": [],
                                   "product_mentions": [], "unresolved": True})
            if "Select every FAQ" in prompt:
                return '{"indices":[0]}'
            if "ANSWER REVIEW" in prompt:
                return json.dumps({"answer": "ลองรีสตาร์ตนาฬิกาตามขั้นตอนในคู่มือก่อนนะครับ"}, ensure_ascii=False)
            return "ลองรีสตาร์ตนาฬิกาตามขั้นตอนในคู่มือก่อนนะครับ"

        product = {"name": "Black Shark GS3", "description": "สมาร์ทวอทช์", "source": {"cell": "B3"}}
        faq = {"model_name": "Black Shark GS3", "question": "หน้าจอเป็นลายแก้อย่างไร",
               "answer": "กดปุ่ม HOME ค้าง 10–25 วินาทีเพื่อรีสตาร์ต", "source": {"cell": "C18"}}
        engine = self.engine([product], generate, [faq])
        engine.store.runtime_config = lambda: {}

        result = engine.answer("จอนาฬิกาที่ซื้อไปเป็นลาย ทำยังไงดีครับ", commerce_lookup=lambda *args: {
            "orders": [{"order_sn": "ORDER-ONE", "items": [
                {"name": "Black Shark GS3 Smart Watch", "model_name": "Black Shark GS3"}]}]})

        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["product_name"], "Black Shark GS3")
        answer_prompt = next(prompt for prompt in prompts if "TOPIC NEEDS" in prompt)
        self.assertIn("FAQ_EVIDENCE", answer_prompt)

    def test_troubleshooting_asks_which_order_when_purchased_models_are_ambiguous(self):
        def generate(key, prompt):
            return json.dumps({"intent": "troubleshooting", "topic": "device issue", "needs": ["fix"],
                               "product_names": [], "product_mentions": [], "unresolved": False})

        products = [
            {"name": "Black Shark GS3", "description": "สมาร์ทวอทช์", "source": {}},
            {"name": "Black Shark S3", "description": "สมาร์ทวอทช์", "source": {}},
        ]
        engine = self.engine(products, generate)
        result = engine.answer("นาฬิกาที่ซื้อไปจอมีปัญหาครับ", commerce_lookup=lambda *args: {
            "orders": [
                {"order_sn": "ORDER-ONE", "items": [{"name": "Black Shark GS3"}]},
                {"order_sn": "ORDER-TWO", "items": [{"name": "Black Shark S3"}]},
            ]})

        self.assertEqual(result["status"], "need_model")
        self.assertEqual(result["reason"], "order_product_ambiguous")
        self.assertIn("รุ่น", result["answer"])

    def test_troubleshooting_answer_still_uses_relevant_faq(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "troubleshooting", "topic": "LINE call not showing", "needs": ["check call settings"],
                                   "product_names": ["Black Shark RUN"], "unresolved": False})
            if "Select every FAQ" in prompt:
                return '{"indices":[0]}'
            return "ลองตรวจสิทธิ์การแจ้งเตือนและเปิดใช้ฟังก์ชันโทรร่วมกับ iPhone ครับ"
        faq = {"model_name": "Black Shark RUN Smart Watch Global Version Black",
               "question": "รับสายไลน์ไม่ได้ มีแค่แจ้งเตือน",
               "answer": "เปิดสิทธิ์ LINE และเปิดใช้ฟังก์ชันโทรร่วมกับ iPhone",
               "source": {"cell": "C17"}}
        engine = self.engine([{"name": "Black Shark RUN", "description": "รองรับ LINE บน iOS", "source": {}}],
                             generate, [faq])

        result = engine.answer("ใช้ RUN กับ iPhone แล้วสาย LINE ไม่ขึ้นที่นาฬิกา")

        self.assertEqual(result["status"], "answered")
        self.assertIn("Select every FAQ", prompts[1])
        self.assertIn("เปิดใช้ฟังก์ชันโทรร่วมกับ iPhone", prompts[-1])

    def test_image_answer_prompt_can_play_with_context_without_forcing_a_product(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "image_understanding", "topic": "guitar sheet music", "needs": ["react to image"],
                                   "product_names": [], "unresolved": False})
            return "ดูเหมือนกำลังซ้อมกีตาร์อยู่เลยครับ"
        result = self.engine([], generate).answer("ช่วยดูภาพหน่อย", vision_evidence=["ภาพเป็นแท็บกีตาร์"])
        self.assertEqual(result["status"], "answered")
        self.assertIn("react naturally to its subject", prompts[-1])
        self.assertIn("do not force a sales pitch", prompts[-1])

    def test_swimming_followup_uses_full_product_evidence_and_direct_answer(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "compatibility", "topic": "swimming", "needs": ["swimming suitability"],
                                   "product_names": ["Black Shark GS3 Ultra"], "unresolved": False})
            return "ใส่ว่ายน้ำได้ครับ GS3 Ultra มีโหมดว่ายน้ำและกันน้ำ 5ATM ตามข้อมูลร้าน"
        engine = self.engine([{"name": "Black Shark GS3 Ultra", "description": "กันน้ำ 5ATM\nโหมดกีฬา: ว่ายน้ำ",
                               "source": {"file": "watch.xlsx"}}], generate)
        result = engine.answer("ใส่ลงสระได้ปะ", history=[{"role": "assistant", "content": "GS3 Ultra ครับ",
                                                  "product_name": "Black Shark GS3 Ultra"}])
        self.assertEqual(result["intent"], "compatibility")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["intent_analysis"]["needs"], ["swimming suitability"])
        self.assertIn("โหมดกีฬา: ว่ายน้ำ", prompts[-1])
        self.assertIn("ได้ครับ", result["answer"])

    def test_comparison_followup_uses_two_models_and_answered_criterion(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "comparison", "topic": "battery life", "needs": ["which lasts longer"],
                                   "product_names": ["Black Shark GS3", "Black Shark GS3 Ultra"], "unresolved": False})
            if "COMPARISON TABLE REFORMAT" in prompt:
                return json.dumps({"answer": "| คุณสมบัติ | Black Shark GS3 | Black Shark GS3 Ultra |\n"
                                           "|---|---|---|\n| แบตเตอรี่ | 21 วัน | 18 วัน |"}, ensure_ascii=False)
            return "ถ้าเน้นแบต GS3 อึดกว่าครับ อยู่ได้สูงสุด 21 วัน ส่วน GS3 Ultra สูงสุด 18 วัน"
        engine = self.engine([
            {"name": "Black Shark GS3", "description": "แบตเตอรี่สูงสุด 21 วัน", "source": {"file": "watch.xlsx"}},
            {"name": "Black Shark GS3 Ultra", "description": "แบตเตอรี่สูงสุด 18 วัน", "source": {"file": "watch.xlsx"}},
        ], generate)
        result = engine.answer("ตัวไหนอึดกว่ากัน", history=[{"role": "user", "content": "GS3 เทียบ GS3 Ultra"}])
        self.assertEqual(result["intent"], "comparison")
        self.assertEqual(result["status"], "answered")
        self.assertIn("แบตเตอรี่สูงสุด 21 วัน", prompts[-1])
        self.assertIn("แบตเตอรี่สูงสุด 18 วัน", prompts[-1])

    def test_search_checks_all_needs_against_catalog_before_answering(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "product_search", "topic": "exercise watch",
                                   "needs": ["GPS", "Strava", "blood pressure"], "product_names": [], "unresolved": False})
            if "Find products in this one store" in prompt:
                return json.dumps({"product_names": ["Black Shark GS3"]})
            return "แนะนำ GS3 ครับ มี GPS และต่อ Strava ได้ แต่ไม่รองรับการวัดความดัน"
        engine = self.engine([
            {"name": "Black Shark GS3", "description": "GPS: มี\nStrava: รองรับ\nวัดความดัน: ไม่รองรับ", "source": {}},
            {"name": "Black Shark T11", "description": "หูฟังไร้สาย Bluetooth 5.3", "source": {}},
        ], generate)
        result = engine.answer("อยากได้ไว้ซ้อมวิ่ง เห็นเส้นทาง ต่อแอปวิ่ง แล้ววัดความดันด้วย")
        self.assertEqual(result["status"], "answered")
        self.assertEqual(result["product_name"], "Black Shark GS3")
        self.assertEqual(result["intent_analysis"]["needs"], ["GPS", "Strava", "blood pressure"])
        self.assertIn("ไม่รองรับ", result["answer"])
        self.assertEqual(len(prompts), 3)

    def test_troubleshooting_chooses_faq_from_product_context(self):
        def generate(key, prompt):
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "troubleshooting", "topic": "charging issue", "needs": ["fix charging"],
                                   "product_names": ["Black Shark GS3"], "unresolved": False})
            if "Select every FAQ" in prompt:
                return '{"index":0}'
            return "ลองเช็ดขั้วชาร์จให้แห้งก่อนครับ แล้วทดสอบชาร์จอีกครั้ง"
        engine = self.engine([{"name": "Black Shark GS3", "description": "GPS: มี", "source": {}}], generate,
                             [{"model_name": "Black Shark GS3", "question": "ชาร์จไม่เข้า",
                               "answer": "เช็ดขั้วชาร์จให้แห้งแล้วทดสอบชาร์จ", "source": {"file": "faq.xlsx"}}])
        result = engine.answer("เสียบสายแล้วมันไม่ขึ้นอะไรเลย", history=[{"role": "assistant", "content": "GS3 ครับ",
                                                                     "product_name": "Black Shark GS3"}])
        self.assertEqual(result["intent"], "troubleshooting")
        self.assertEqual(result["matched_question"], "ชาร์จไม่เข้า")
        self.assertEqual(result["sources"][1]["file"], "faq.xlsx")

    def test_long_faq_model_name_stays_with_the_most_specific_product(self):
        selected_options = []
        def generate(key, prompt):
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "troubleshooting", "topic": "charging", "needs": ["fix charging"],
                                   "product_names": ["Black Shark S3 Classic"], "unresolved": False})
            if "Select every FAQ" in prompt:
                selected_options.append(prompt)
                return '{"index":0}'
            return "ลองทำความสะอาดขั้วชาร์จของ S3 Classic ครับ"
        engine = self.engine([
            {"name": "Black Shark S3", "description": "GPS", "source": {}},
            {"name": "Black Shark S3 Classic", "description": "3ATM", "source": {}},
        ], generate, [
            {"model_name": "Black Shark S3 Smart Watch", "question": "ชาร์จไม่เข้า S3", "answer": "รีสตาร์ท S3", "source": {}},
            {"model_name": "Black Shark S3 Classic Smart Watch", "question": "ชาร์จไม่เข้า Classic",
             "answer": "ทำความสะอาดขั้วชาร์จของ S3 Classic", "source": {}},
        ])
        result = engine.answer("รุ่นคลาสสิกชาร์จไม่ขึ้น")
        self.assertEqual(result["matched_question"], "ชาร์จไม่เข้า Classic")
        self.assertNotIn("ชาร์จไม่เข้า S3\"", selected_options[0])

    def test_image_question_uses_vision_description_as_source(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "image_understanding", "topic": "screen damage",
                                   "needs": ["describe screen"], "product_names": [], "unresolved": False})
            return "จากภาพ หน้าจอมีเส้นสีขาวพาดอยู่ครับ"
        engine = self.engine([], generate)
        result = engine.answer("จอเป็นอะไรครับ", vision_evidence=["หน้าจอนาฬิกามีเส้นสีขาวพาด"])
        self.assertEqual(result["intent"], "image_understanding")
        self.assertEqual(result["status"], "answered")
        self.assertIn("หน้าจอนาฬิกามีเส้นสีขาวพาด", prompts[-1])

    def test_conflicting_source_values_are_available_to_answer_stage(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "product_info", "topic": "refresh rate", "needs": ["display refresh rate"],
                                   "product_names": ["Black Shark GS3 Ultra"], "unresolved": False})
            return "ข้อมูล GS3 Ultra ขัดกันครับ จุดหนึ่งบอก 60Hz แต่อีกจุดบอก 30Hz จึงยังยืนยันค่าไม่ได้"
        engine = self.engine([{"name": "Black Shark GS3 Ultra",
                               "description": "จอ Refresh Rate 60Hz\nHz ของจอ: 30Hz", "source": {}}], generate)
        result = engine.answer("จอมันกี่เฮิรตซ์นะ")
        self.assertEqual(result["status"], "answered")
        self.assertIn("60Hz", prompts[-1])
        self.assertIn("30Hz", prompts[-1])
        self.assertIn("ขัดกัน", result["answer"])

    def test_natural_thai_wording_is_not_rejected_for_missing_exact_words(self):
        self.assertTrue(supported_wording("ใส่ลงสระได้ครับ มีโหมดว่ายน้ำ", "โหมดกีฬา: ว่ายน้ำ"))
        self.assertFalse(supported_wording("Gemini API key: AIza123456789012345678901234567890", "สินค้า"))


if __name__ == "__main__":
    unittest.main()
