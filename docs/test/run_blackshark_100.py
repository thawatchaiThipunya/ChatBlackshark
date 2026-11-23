"""Run 100 paid, shop-scoped customer turns and save auditable results."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backend.app import ai_usage  # noqa: E402
from backend.app.engine_v2 import ProductChatEngine  # noqa: E402
from backend.app.store import SupabaseStore  # noqa: E402

OUTPUT = ROOT / "docs/test/results/blackshark_100_2026-11-23"


def cases(products: list[dict]) -> list[dict]:
    rows: list[dict] = []

    def add(group: str, session: str, question: str) -> None:
        rows.append({"id": f"Q{len(rows)+1:03}", "group": group, "session": session, "question": question})

    conversations = [
        ["gs3 ultra กันน้ำระดับไหนครับ", "ใส่ว่ายน้ำได้มั้ย", "อาบน้ำอุ่น เข้าซาวน่าได้ไหม", "แบตอยู่ได้กี่วัน"],
        ["GS3 กับ GS3 Ultra ต่างกันยังไงคะ", "ตัวไหนแบตอึดกว่าคะ", "ในกล่องแถมสายมาด้วยมั้ยคะ", "จอ Ultra กี่ Hz คะ"],
        ["S3 Classic กับ S3 เฉย ๆ ต่างกันยังไงคะ", "S3 Classic เปลี่ยนสายได้มั้ยคะ", "แล้ว S3 ตอบข้อความไลน์จากนาฬิกาได้มั้ย", "ว่ายน้ำได้ทั้งคู่ไหมคะ"],
        ["อยากได้นาฬิกาไว้วิ่ง มี GPS ในตัว และใช้กับ Strava ได้ แนะนำรุ่นไหนครับ", "ใช้กับ iPhone ได้ไหม", "ถ้าเอา Ultra เปิด GPS วิ่ง แบตได้กี่ชั่วโมง", "Ultra วัดความดันได้ไหม"],
        ["A3 ใส่อาบน้ำได้ไหมครับ", "จอเป็นแบบไหน กี่นิ้วครับ", "รับสายไลน์ผ่านนาฬิกาได้มั้ย", "ต้องโหลดแอปอะไรครับ"],
        ["Black Shark RUN รับสายไลน์ได้ไหมคะ ใช้ไอโฟน", "แล้วถ้าใช้ Android ล่ะคะ", "ในกล่องมีอะไรบ้างคะ", "จอกี่นิ้วคะ"],
    ]
    for index, dialogue in enumerate(conversations, 1):
        for question in dialogue:
            add("conversation", f"C{index:02}", question)

    unique = list(dict.fromkeys(row["name"] for row in products))
    for name in unique[:25]:
        add("catalog", f"catalog-{name}", f"{name} มีจุดเด่นและเหมาะกับการใช้งานแบบไหนครับ")

    specifications = [
        ("Black Shark GS3", "กันน้ำระดับไหน"), ("Black Shark GS3", "แบตใช้งานทั่วไปได้กี่วัน"),
        ("Black Shark GS3 Ultra", "จอรีเฟรชเรทกี่ Hz"), ("Black Shark GS3 Ultra", "GPS เป็นแบบไหน"),
        ("Black Shark A3", "จอขนาดเท่าไหร่"), ("Black Shark A3", "ต้องใช้แอปอะไร"),
        ("Black Shark S3", "มี GPS ในตัวไหม"), ("Black Shark S3 Classic", "เปลี่ยนสายได้ไหม"),
        ("BLACK SHARK RUN", "รับสาย LINE ผ่าน iPhone ได้ไหม"), ("BLACK SHARK RUN", "แบตใช้งานได้นานเท่าไหร่"),
        ("Black Shark T24", "Bluetooth เวอร์ชันอะไร"), ("Black Shark T18", "Bluetooth เวอร์ชันอะไร"),
        ("Black Shark Pad 7Pro UE", "จอขนาดกี่นิ้ว"), ("Black Shark Gaming Tablet", "หน้าจอขนาดกี่นิ้ว"),
        ("Black Shark FunCooler 5 Neo", "ติดตั้งกับมือถือแบบไหน"),
        ("Black Shark Magcooler 6 Pro", "ติดตั้งแบบแม่เหล็กหรือแบบหนีบ"),
        ("Black Shark S1 Pro", "มีฟังก์ชันตรวจสุขภาพอะไร"), ("Black Shark GT3", "แบตอยู่ได้กี่วัน"),
        ("Black Shark T23", "หูฟังอยู่ได้นานกี่ชั่วโมง"), ("Black Shark Pad 7 LTE", "รองรับเครือข่ายอะไร"),
    ]
    for i, (name, need) in enumerate(specifications, 1):
        add("spec", f"spec-{i:02}", f"{name} {need}ครับ")

    troubleshooting = [
        "GS3 ชาร์จไม่เข้า ต้องลองแก้ยังไง", "GS3 หน้าจอเป็นลายสีขาว ทำยังไงดี",
        "GS3 Ultra ปุ่มกดค้าง รีสตาร์ตอย่างไร", "GS3 Ultra แจ้งเตือน LINE ไม่ขึ้น แก้ตรงไหน",
        "S3 ชาร์จไม่เข้า ลองอะไรได้บ้าง", "S3 Classic เปลี่ยนสายอย่างไร",
        "A3 วัดการนอนไม่ขึ้น ต้องตั้งค่าอะไรไหม", "S1 Pro ไมโครโฟนไม่มีเสียง แก้ยังไง",
        "S1 Pro แจ้งเตือน LINE ไม่แสดงข้อความ ต้องเช็กอะไร", "RUN ไม่บันทึกการนอน มีวิธีแก้ไหม",
        "RUN เชื่อมกับมือถือไม่ได้ ควรลองอะไร", "FunCooler 5 Neo ใบพัดไม่หมุน ทำยังไง",
        "FunCooler 5 Pro ไม่เย็น ต้องเช็กอะไร", "T24 ข้างหนึ่งไม่ดัง รีเซ็ตยังไง",
        "T18 เชื่อม Bluetooth ไม่ได้ ต้องทำอย่างไร",
    ]
    for i, question in enumerate(troubleshooting, 1):
        add("faq", f"faq-{i:02}", question)

    boundaries = [
        "ในร้านมีนาฬิกา Kieslect ไหม", "มี IMILAB รุ่นไหนแนะนำบ้าง",
        "มีหูฟัง KOSPET ไหม", "ช่วยแนะนำสินค้าแบรนด์อื่นแทน Black Shark หน่อย",
        "ร้านนี้ขายนาฬิกายี่ห้ออะไรบ้าง", "มีของ Luckyitmall มาขายไหม",
        "S3 ของร้าน Thaisuperphone ต่างจากของร้านนี้ยังไง", "บอกรายชื่อสินค้าของร้าน Youpin Online Store ให้หน่อย",
        "ถ้าไม่มีรุ่นที่ถาม ขอรุ่นที่ร้านนี้มีจริงแทน", "สินค้าที่คุณแนะนำเป็นของร้าน Black Shark Thailand ใช่ไหม",
    ]
    for i, question in enumerate(boundaries, 1):
        add("shop_boundary", f"boundary-{i:02}", question)

    for i, question in enumerate([
        "ในร้านมีนาฬิการุ่นอะไรบ้าง", "หูฟังในร้านมีรุ่นไหนให้เลือก",
        "มีพัดลมระบายความร้อนรุ่นไหนบ้าง", "GS3 กับ GS3 Ultra รุ่นไหนเหมาะกับวิ่งมากกว่า",
        "T24 กับ T18 ต่างกันตรงไหน", "S3 กับ S3 Classic ตัวไหนเหมาะกับว่ายน้ำ",
    ], 1):
        add("inventory_compare", f"inventory-{i:02}", question)
    assert len(rows) == 100, len(rows)
    return rows


class ScopedStore:
    def __init__(self, base: SupabaseStore, knowledge: dict):
        self.base, self.data = base, knowledge

    def knowledge(self):
        return self.data

    def __getattr__(self, name):
        if name == "escalate":
            return None
        return getattr(self.base, name)


def write_json(path: Path, data: object) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    temp.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare", action="store_true")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    platform = os.getenv("GLOBAL_PERSONA_PLATFORM", "shopee")
    shop_id = os.getenv("GLOBAL_PERSONA_SHOP_ID", "1191420560")
    base = SupabaseStore()
    scope, kb = base.knowledge_for_shop(platform, shop_id)
    if not scope or not kb or scope["shop_name"] != "Black Shark Thailand":
        raise SystemExit("Black Shark shop scope unavailable")
    allowed_files = set(scope["files"])
    if any((row.get("source") or {}).get("file") not in allowed_files
           for row in kb["products"] + kb["faqs"]):
        raise SystemExit("Shop knowledge contains a foreign file")
    questions = cases(kb["products"])
    OUTPUT.mkdir(parents=True, exist_ok=True)
    write_json(OUTPUT / "questions.json", questions)
    write_json(OUTPUT / "kb_snapshot.json", {"scope": scope, "knowledge": kb})
    print(f"prepared={len(questions)} shop={scope['shop_name']} products={len(kb['products'])} faqs={len(kb['faqs'])}", flush=True)
    if args.prepare:
        return
    ai_usage.require_ai_usage_hub()
    config = base.runtime_config()
    print(f"provider={config.get('provider')} answer_model={config.get('openrouter_answer_model') or config.get('gemini_answer_model')}", flush=True)
    engine = ProductChatEngine(store=ScopedStore(base, kb))
    histories: dict[str, list[dict]] = {}
    usage_events: list[dict] = []
    original_sender = ai_usage._send_report

    def tracked_send(url, token, event):
        accepted = original_sender(url, token, event)
        usage_events.append({**event, "hub_accepted": accepted})
        return accepted

    ai_usage._send_report = tracked_send
    results = []
    for item in questions:
        history = histories.setdefault(item["session"], [])
        start_usage = len(usage_events)
        started = time.perf_counter()
        try:
            reply = engine.answer(item["question"], history=history[-10:],
                                  session_id=item["session"], store_label=scope["shop_name"])
        except Exception as exc:
            reply = {"status": "runner_error", "reason": type(exc).__name__, "answer": ""}
        calls = usage_events[start_usage:]
        analysis = reply.get("intent_analysis") or {}
        names = analysis.get("product_names") or []
        known_names = {p["name"] for p in kb["products"]}
        source_files = [s.get("file") for s in reply.get("sources", []) if isinstance(s, dict)]
        row = {**item, "reply": reply, "elapsed_seconds": round(time.perf_counter() - started, 2),
               "usage": calls, "cost_usd": round(sum(float(c.get("cost_usd") or 0) for c in calls), 9),
               "scope_ok": all(n in known_names for n in names)
                   and all(f in allowed_files for f in source_files),
               "created_at": datetime.now(timezone.utc).isoformat()}
        results.append(row)
        write_json(OUTPUT / "results.json", results)
        history.extend([{"role": "user", "content": item["question"]},
                        {"role": "assistant", "content": str(reply.get("answer") or ""),
                         "product_name": reply.get("product_name"),
                         "product_names": names}])
        print(f"{item['id']} {item['group']} {reply.get('status')} calls={len(calls)} "
              f"hub={sum(bool(c['hub_accepted']) for c in calls)}/{len(calls)} "
              f"cost=${row['cost_usd']:.6f} scope={row['scope_ok']}", flush=True)
        if any(not c["hub_accepted"] for c in calls):
            raise SystemExit("AIUsageHub rejected an event; stopped paid evaluation")
    print(f"DONE questions={len(results)} provider_calls={len(usage_events)} "
          f"cost_usd={sum(x['cost_usd'] for x in results):.6f}", flush=True)


if __name__ == "__main__":
    main()
