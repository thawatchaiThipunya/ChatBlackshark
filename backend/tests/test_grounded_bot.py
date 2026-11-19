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

    def test_card_listing_context_reaches_answer_while_kb_remains_available(self):
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
        self.assertIn("รองรับโหมดว่ายน้ำ", prompts[-1])

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
        self.assertEqual(result["status"], "answered")
        self.assertIsNone(result.get("product_name"))
        self.assertNotIn("หน้าจอ AMOLED 1.43 นิ้ว", prompts[-1])
        self.assertIn("edition ในรายการขาย", prompts[-1])

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

    def test_compatibility_answer_receives_all_relevant_conflicting_faqs(self):
        prompts = []
        def generate(key, prompt):
            prompts.append(prompt)
            if "Return ONLY JSON with keys: intent" in prompt:
                return json.dumps({"intent": "compatibility", "topic": "LINE calling", "needs": ["LINE calls on iOS"],
                                   "product_names": ["Black Shark RUN"], "unresolved": False})
            if "Select every FAQ" in prompt:
                return '{"indices":[0,1]}'
            return "เอกสารมีข้อมูลไม่ตรงกัน จึงยังยืนยันการโทร LINE บน iOS ไม่ได้ครับ"
        faqs = [
            {"model_name": "Black Shark RUN Smart Watch Global Version Black", "question": "โทร LINE ไม่ได้",
             "answer": "ไม่รองรับการโทรผ่านแอปโซเชียลมีเดีย", "source": {"cell": "C62"}},
            {"model_name": "Black Shark RUN Smart Watch Global Version Yellow", "question": "แจ้งเตือน LINE บน iPhone",
             "answer": "กรุณาเปิดใช้ฟังก์ชันโทรร่วมกับ iPhone", "source": {"cell": "C93"}},
        ]
        engine = self.engine([{"name": "Black Shark RUN", "description": "ใช้แอป Shark Wearable", "source": {}}],
                             generate, faqs)
        result = engine.answer("รับสาย LINE บน iPhone ได้ไหม")
        self.assertEqual(result["status"], "answered")
        self.assertIn("ไม่รองรับการโทรผ่านแอปโซเชียลมีเดีย", prompts[-1])
        self.assertIn("เปิดใช้ฟังก์ชันโทรร่วมกับ iPhone", prompts[-1])
        self.assertEqual(len(result["sources"]), 3)

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
