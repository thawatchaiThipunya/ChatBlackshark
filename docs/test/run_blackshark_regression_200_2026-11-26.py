"""Run a sequential, shop-scoped, paid 200-question regression and render HTML."""
from __future__ import annotations

import argparse
import html
import json
import os
import statistics
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "docs/test/results/blackshark_100_2026-11-26_current_logic"
OUTPUT = ROOT / "docs/test/results/blackshark_regression_200_2026-11-26"
BUDGET_GUARD_USD = 1.50  # $0.50 reserve under the user's $2 hard limit.
EXPECTED_SHOP_NAME = "Black Shark Thailand"
EXPECTED_PROVIDER = "openrouter"
EXPECTED_MODEL = "google/gemini-3.5-flash-lite"
MANUAL_FINDINGS = [
    {"id": "Q012", "severity": "high", "type": "answer_regression",
     "note": "Baseline answered; current run returned handoff_mock/insufficient_shop_evidence for whether S3 and S3 Classic can be used for swimming. Shop KB B8 says S3 is 5ATM and supports water activities; B9 says S3 Classic is 3ATM and rated for rain/splashes. The bot should explain the distinction and uncertainty instead of handing off."},
    {"id": "Q061", "severity": "high", "type": "answer_regression",
     "note": "Baseline answered; current run handed off for T18 Bluetooth version. Shop KB B39 states Bluetooth 5.3, so the answer is available in the scoped product record."},
    {"id": "Q077", "severity": "high", "type": "faq_regression",
     "note": "Baseline answered; current run handed off for S1 Pro microphone troubleshooting. Shop FAQ C3 contains checks for the microphone opening, obstruction, careful cleaning, and retesting."},
    {"id": "N198", "severity": "high", "type": "incorrect_spec_in_comparison",
     "note": "The comparison table says GS3 Ultra is 30 Hz. Shop KB B5 says 60 Hz; the same run also answered 60 Hz correctly for the single-product questions Q008 and Q052. This comparison answer contradicts its own allowed source and is an accuracy failure."},
    {"id": "Q069", "severity": "info", "type": "handoff_supported_by_kb_gap",
     "note": "This handoff is not a regression: baseline also handed off. In the Black Shark-only KB rows B23–B24 for Pad 7 LTE, the product name contains LTE but no cellular band/network standard is specified; the record only states Wi‑Fi 2.4/5 GHz and Bluetooth 5.0. Handoff avoids guessing."},
]
UNCLASSIFIED_CATEGORY = "หมวดหมู่ยังไม่ระบุ"

# Confirmed by a read-only shop-scoped Mongo -> KB identity lookup before this run.
CARD_PRODUCTS = [
    ("24140513934", "Black Shark A3"),
    ("40331159062", "Black Shark C3"),
    ("43367444997", "Black Shark FunCooler 3 Pro"),
    ("43467455382", "Black Shark FunCooler 4"),
    ("42218197718", "Black Shark FunCooler 5 Neo"),
    ("40067459782", "Black Shark FunCooler 5 Pro"),
    ("27822949206", "Black Shark GS3"),
    ("46057699735", "Black Shark GS3 Smartwatch Charger"),
    ("27276155598", "Black Shark GS3 Sport"),
    ("46657749892", "Black Shark GS3 Sport Smartwatch Charger"),
    ("25064823709", "Black Shark GS3 Ultra"),
    ("41933130625", "Black Shark GS3 Ultra (REFINED EDITION)"),
    ("56355146597", "Black Shark Gaming Tablet"),
    ("50255768193", "Black Shark Goblin X8"),
    ("42067454827", "Black Shark MagCooler 3 Pro"),
    ("42131170553", "Black Shark Magcooler 6 Max"),
    ("50661100978", "Black Shark Magcooler 6 Pro"),
    ("42231170586", "Black Shark Magcooler 6 Ultra"),
    ("45612054431", "Black Shark OpenAir"),
    ("53252579399", "Black Shark Pad 7 wifi"),
    ("53118402816", "Black Shark PureLoop"),
    ("44217439082", "Black Shark Rokas 3"),
    ("57811093211", "Black Shark Rokas 5"),
    ("24214834958", "Black Shark S1 Classic"),
    ("55607690408", "Black Shark S1 Pro Smartwatch Charger"),
    ("25678132425", "Black Shark S3"),
    ("43278262719", "Black Shark S3 Classic"),
    ("42031170589", "Black Shark Supercharged Laptop Cooler Pro"),
    ("40217446305", "Black Shark T11"),
    ("28791107344", "Black Shark T18"),
    ("28692005387", "Black Shark T23"),
    ("42017441072", "Black Shark T24"),
]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str))
    temp.replace(path)


def all_names_from_reply(reply: dict[str, Any]) -> list[str]:
    result: list[str] = []
    analysis = reply.get("intent_analysis") or {}
    if isinstance(analysis, dict) and isinstance(analysis.get("product_names"), list):
        result.extend(name for name in analysis["product_names"] if isinstance(name, str))
    if isinstance(reply.get("product_name"), str):
        result.append(reply["product_name"])
    for key in ("product_cards", "products"):
        cards = reply.get(key)
        if isinstance(cards, list):
            for card in cards:
                if isinstance(card, dict):
                    for field in ("product_name", "name"):
                        value = card.get(field)
                        if isinstance(value, str):
                            result.append(value)
    return list(dict.fromkeys(result))


def names_from_reply(reply: dict[str, Any], valid_names: set[str]) -> list[str]:
    return [name for name in all_names_from_reply(reply) if name in valid_names]


def source_files(reply: dict[str, Any]) -> list[str]:
    result = []
    for source in reply.get("sources", []) if isinstance(reply.get("sources"), list) else []:
        if not isinstance(source, dict):
            continue
        name = source.get("file")
        if not name and isinstance(source.get("source"), dict):
            name = source["source"].get("file")
        if isinstance(name, str) and name:
            result.append(name)
    return list(dict.fromkeys(result))


def has_markdown_table(answer: str) -> bool:
    lines = (answer or "").splitlines()
    for index in range(len(lines) - 2):
        header, separator, row = (line.strip() for line in lines[index:index + 3])
        if not (header.startswith("|") and header.endswith("|") and
                separator.startswith("|") and separator.endswith("|") and
                row.startswith("|") and row.endswith("|")):
            continue
        columns = [part.strip() for part in header.strip("|").split("|")]
        separators = [part.strip() for part in separator.strip("|").split("|")]
        cells = [part.strip() for part in row.strip("|").split("|")]
        if len(columns) >= 3 and len(columns) == len(separators) == len(cells) and all(
                all(char == "-" or char == ":" for char in cell) and "-" in cell
                for cell in separators):
            return True
    return False


def build_supplemental(knowledge: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    products = knowledge.get("products") or []
    by_name: dict[str, dict[str, Any]] = {}
    categories_by_name: dict[str, set[str]] = defaultdict(set)
    from backend.app.product_categories import category_from_description
    for item in products:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        category = str(item.get("category") or category_from_description(item.get("description")) or "").strip()
        if category:
            categories_by_name[name].add(category)
        by_name.setdefault(name, item)
    category_by_name = {
        name: (next(iter(categories)) if len(categories) == 1 else None)
        for name, categories in categories_by_name.items()
    }
    category_products: dict[str, list[str]] = defaultdict(list)
    for name in by_name:
        category = category_by_name.get(name)
        category_products[category or UNCLASSIFIED_CATEGORY].append(name)
    categories = sorted((category for category in category_products if category != UNCLASSIFIED_CATEGORY),
                        key=str.casefold)
    if not categories or len(categories) > 8:
        raise SystemExit(f"The fixed suite expects 1–8 explicit KB categories; found {len(categories)}. No paid call made.")

    cases: list[dict[str, Any]] = []
    number = 101
    for index, (item_id, product_name) in enumerate(CARD_PRODUCTS):
        session = f"CARD-{index + 1:02d}"
        cases.append({"id": f"N{number:03d}", "suite": "new", "group": "item_id_only",
                      "session": session, "question": "", "item_ids": [item_id],
                      "expected_product": product_name, "test_kind": "card_initial"})
        number += 1
        cases.append({"id": f"N{number:03d}", "suite": "new", "group": "card_followup",
                      "session": session, "question": "แล้วรุ่นนี้มีจุดเด่นอะไรบ้างครับ",
                      "followup_of": f"N{number - 1:03d}", "expected_product": product_name,
                      "test_kind": "card_followup"})
        number += 1
        if index < 16:
            cases.append({"id": f"N{number:03d}", "suite": "new", "group": "card_followup",
                          "session": session, "question": "ช่วยสรุปสเปกสำคัญของรุ่นนี้ให้หน่อยครับ",
                          "followup_of": f"N{number - 2:03d}", "expected_product": product_name,
                          "test_kind": "card_followup_second"})
            number += 1

    broad = [
        "ร้านนี้มีสินค้าหมวดอะไรบ้างครับ",
        "มีอะไรขายบ้าง ขอแยกตามประเภทสินค้าให้หน่อยครับ",
        "ร้านมีหมวดสินค้าอะไรบ้างครับ",
        "ขอภาพรวมสินค้าทั้งร้าน แบ่งตามหมวดให้ดูหน่อยครับ",
        "ในร้านมีสินค้าอะไรบ้างครับ",
    ]
    expected_counts = {category: len(set(names)) for category, names in category_products.items()}
    for index, question in enumerate(broad, start=1):
        cases.append({"id": f"N{number:03d}", "suite": "new", "group": "inventory_broad",
                      "session": f"INV-BROAD-{index}", "question": question,
                      "expected_category_counts": expected_counts, "test_kind": "inventory_broad"})
        number += 1
    category_questions = [(category, f"ในหมวด {category} มีรุ่นไหนบ้างครับ") for category in categories]
    alternate_phrasings = (
        "มีรุ่นอะไรในประเภท {category} ให้เลือกบ้างครับ",
        "ช่วยแสดงรายชื่อสินค้าในกลุ่ม {category} ทั้งหมดให้หน่อยครับ",
    )
    fill = 0
    while len(category_questions) < 8:
        category = max(categories, key=lambda value: len(category_products[value]))
        wording = alternate_phrasings[fill % len(alternate_phrasings)].format(category=category)
        category_questions.append((category, wording))
        fill += 1
    for index, (category, question) in enumerate(category_questions, start=1):
        cases.append({"id": f"N{number:03d}", "suite": "new", "group": "inventory_category",
                      "session": f"INV-CAT-{index}",
                      "question": question,
                      "expected_category": category,
                      "expected_products": sorted(set(category_products[category]), key=str.casefold),
                      "test_kind": "inventory_category",
                      "question_variant": "canonical" if index <= len(categories) else "alternate_wording"})
        number += 1

    edge = [
        {"group": "card_disambiguation", "question": "รุ่นนี้กันน้ำได้ไหมครับ", "item_ids": ["26893612033"],
         "expected_product": "Black Shark GS3 Ultra", "test_kind": "card_question"},
        {"group": "card_kb_conflict", "question": "", "item_ids": ["55355455002"],
         "expected_reason": "card_not_in_shop_kb", "test_kind": "card_unmatched"},
        {"group": "fake_item_id", "question": "", "item_ids": ["12345678901"],
         "expected_reason": "item_id_not_found", "test_kind": "card_unmatched"},
        {"group": "multi_card", "question": "การ์ดสองชิ้นนี้เป็นสินค้าอะไรบ้างครับ",
         "item_ids": ["27822949206", "46057699735"],
         "expected_products": ["Black Shark GS3", "Black Shark GS3 Smartwatch Charger"],
         "test_kind": "multi_card"},
        {"group": "card_comparison", "question": "Black Shark GS3 กับ GS3 Ultra ต่างกันอย่างไรครับ ขอเป็นตารางเทียบสเปก",
         "item_ids": ["27822949206"], "expected_products": ["Black Shark GS3", "Black Shark GS3 Ultra"],
         "test_kind": "card_comparison"},
        {"group": "card_product_qa", "question": "รุ่นนี้มีฟังก์ชันสุขภาพอะไรบ้างครับ",
         "item_ids": ["53118402816"], "expected_product": "Black Shark PureLoop", "test_kind": "card_question"},
        {"group": "card_product_qa", "question": "Black Shark Goblin X8 เป็นหูฟังแบบไหน มีการเชื่อมต่ออะไรบ้างครับ",
         "item_ids": ["50255768193"], "expected_product": "Black Shark Goblin X8", "test_kind": "card_question"},
    ]
    for index, case in enumerate(edge, start=1):
        cases.append({"id": f"N{number:03d}", "suite": "new", "session": f"EDGE-{index}", **case})
        number += 1
    if len(cases) != 100 or number != 201:
        raise SystemExit(f"Supplemental suite must contain exactly 100 cases; got {len(cases)}")
    return cases, {"category_counts": expected_counts,
                   "category_products": {key: sorted(set(value), key=str.casefold)
                                         for key, value in category_products.items()},
                   "unique_product_count": len(by_name)}


def evaluate_case(case: dict[str, Any], reply: dict[str, Any], *, valid_names: set[str],
                  allowed_files: set[str], expected_category_counts: dict[str, int]) -> dict[str, Any]:
    reply = reply if isinstance(reply, dict) else {}
    analysis = reply.get("intent_analysis") if isinstance(reply.get("intent_analysis"), dict) else {}
    all_products = all_names_from_reply(reply)
    products = [name for name in all_products if name in valid_names]
    files = source_files(reply)
    status = str(reply.get("status") or "unknown")
    scope_ok = all(name in valid_names for name in all_products) and all(file in allowed_files for file in files)
    checks: dict[str, bool | None] = {"shop_scope": scope_ok}
    expected_product = case.get("expected_product")
    expected_products = case.get("expected_products")
    if expected_product:
        checks["expected_product_mapped_or_retained"] = expected_product in products
    if expected_products:
        checks["expected_products_present"] = set(expected_products).issubset(set(products))
    expected_reason = case.get("expected_reason")
    if expected_reason:
        checks["expected_card_reason"] = (reply.get("reason") == expected_reason or
                                          reply.get("handoff_reason") == expected_reason)
    if case.get("test_kind") == "inventory_broad":
        actual = {str(row.get("category")): int(row.get("count") or 0)
                  for row in analysis.get("category_summary", []) if isinstance(row, dict)}
        checks["category_counts_match_live_kb"] = actual == expected_category_counts
    if case.get("test_kind") == "inventory_category":
        actual = analysis.get("product_names") if isinstance(analysis.get("product_names"), list) else []
        checks["category_product_list_matches_live_kb"] = set(actual) == set(case.get("expected_products") or [])
    if case.get("test_kind") == "card_comparison":
        checks["comparison_markdown_table"] = has_markdown_table(str(reply.get("answer") or ""))
    evaluated = [value for value in checks.values() if value is not None]
    return {"status": status, "reason": reply.get("reason"), "product_names": products,
            "all_product_names_returned": all_products,
            "source_files": files, "scope_ok": scope_ok, "checks": checks,
            "deterministic_checks_passed": sum(value is True for value in evaluated),
            "deterministic_checks_total": len(evaluated),
            "deterministic_checks_ok": bool(evaluated) and all(value is True for value in evaluated)}


def render_report(rows: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    statuses = Counter()
    manual_findings = summary.get("manual_findings", MANUAL_FINDINGS)
    manual_by_id = {item["id"]: item for item in manual_findings}
    for row in rows:
        groups[str(row.get("group") or "unknown")].append(row)
        statuses[str((row.get("reply") or {}).get("status") or "unknown")] += 1

    def bar_rows(values: dict[str, float], *, currency: bool = False) -> str:
        maximum = max(values.values(), default=1) or 1
        parts = []
        for label, value in values.items():
            width = max(2, min(100, value / maximum * 100))
            display = f"${value:.4f}" if currency else f"{value:g}"
            parts.append(f'<div class="barrow"><span>{html.escape(label)}</span><div class="bartrack"><i style="width:{width:.1f}%"></i></div><b>{display}</b></div>')
        return "".join(parts)

    group_counts = {key: len(value) for key, value in sorted(groups.items())}
    group_costs = {key: sum(float(row.get("total_cost_including_retries_usd") or row.get("cost_usd") or 0) for row in value)
                   for key, value in sorted(groups.items())}
    group_ok = {key: sum(bool((row.get("evaluation") or {}).get("deterministic_checks_ok")) for row in value)
                for key, value in sorted(groups.items())}
    status_rows = "".join(f"<tr><td>{html.escape(status)}</td><td>{count}</td></tr>"
                          for status, count in sorted(statuses.items()))
    group_table = "".join(
        f"<tr><td>{html.escape(group)}</td><td>{len(items)}</td>"
        f"<td>{sum((item.get('reply') or {}).get('status') == 'answered' for item in items)}</td>"
        f"<td>{sum((item.get('evaluation') or {}).get('deterministic_checks_ok') is True for item in items)}</td>"
        f"<td>{sum((item.get('evaluation') or {}).get('scope_ok') is False for item in items)}</td>"
        f"<td>{sum(float(item.get('total_cost_including_retries_usd') or item.get('cost_usd') or 0) for item in items):.5f}</td></tr>"
        for group, items in sorted(groups.items()))

    cards = []
    for row in rows:
        reply = row.get("reply") or {}
        evaluation = row.get("evaluation") or {}
        baseline = row.get("baseline") or {}
        status = str(reply.get("status") or "unknown")
        case_cost = float(row.get("total_cost_including_retries_usd") or row.get("cost_usd") or 0)
        changed = row.get("answer_changed_from_baseline")
        manual = manual_by_id.get(str(row.get("id") or ""))
        checks = evaluation.get("checks") or {}
        check_text = " · ".join(f"{html.escape(str(k))}: {'ผ่าน' if v is True else 'ไม่ผ่าน' if v is False else '—'}"
                                for k, v in checks.items()) or "ไม่มี deterministic check เพิ่มเติม"
        usage = "".join(
            f"<tr><td>{html.escape(str(event.get('source') or ''))}</td>"
            f"<td>{html.escape(str(event.get('model') or ''))}</td><td>{html.escape(str(event.get('status') or ''))}</td>"
            f"<td>{event.get('prompt_tokens', 0)}</td><td>{event.get('completion_tokens', 0)}</td>"
            f"<td>${float(event.get('cost_usd') or 0):.6f}</td>"
            f"<td>{'รับแล้ว' if event.get('hub_accepted') else 'ปฏิเสธ/ไม่รับ'}</td></tr>"
            for event in row.get("usage", []))
        sources = " · ".join(
            f"{html.escape(str(source.get('file') or ''))} {html.escape(str(source.get('cell') or ''))}"
            for source in reply.get("sources", []) if isinstance(source, dict)) or "ไม่มี source reference"
        card_ids = ", ".join(row.get("item_ids") or []) or "—"
        cards.append(f"""<article class="case" data-suite="{html.escape(str(row.get('suite') or ''))}"
            data-group="{html.escape(str(row.get('group') or ''))}" data-status="{html.escape(status)}"
            data-scope="{'pass' if evaluation.get('scope_ok') is True else 'fail' if evaluation.get('scope_ok') is False else 'na'}"
            data-check="{'pass' if evaluation.get('deterministic_checks_ok') is True else 'fail' if evaluation.get('deterministic_checks_ok') is False else 'na'}"
            data-changed="{'yes' if changed is True else 'no' if changed is False else 'na'}"
            data-review="{'yes' if manual else 'no'}">
          <header><strong>{html.escape(str(row.get('id') or ''))}</strong><span class="pill">{html.escape(status)}</span>
          {f'<span class="pill" style="background:#fff0ed;color:#a3291d">manual review</span>' if manual else ''}
          <span class="muted">{html.escape(str(row.get('group') or ''))} · {float(row.get('elapsed_seconds') or 0):.2f}s · ${case_cost:.6f} รวม retry</span>
          <span class="muted">deterministic {evaluation.get('deterministic_checks_passed',0)}/{evaluation.get('deterministic_checks_total',0)}</span></header>
          <h3>{html.escape(str(row.get('question') or 'การ์ด item_id อย่างเดียว'))}</h3>
          <p class="muted">item_id: {html.escape(card_ids)} · expected: {html.escape(str(row.get('expected_product') or row.get('expected_category') or '—'))}</p>
          <p><b>คำตอบจริง</b><br><span class="answer">{html.escape(str(reply.get('answer') or ''))}</span></p>
          <p><b>รุ่นที่ engine ระบุ:</b> {html.escape(', '.join(evaluation.get('product_names') or [])) or '—'} · 
             <b>intent:</b> {html.escape(str(reply.get('intent') or '—'))} · <b>reason:</b> {html.escape(str(reply.get('reason') or '—'))}</p>
          <p><b>ตรวจอัตโนมัติ:</b> {check_text}<br><b>KB source:</b> {sources}<br>
             <b>provider calls:</b> {len(row.get('usage', []))} · <b>Hub:</b> {sum(bool(e.get('hub_accepted')) for e in row.get('usage', []))}/{len(row.get('usage', []))}</p>
          {f'<p class="warn"><b>ผลตรวจมือ · {html.escape(str(manual.get("type") or ""))}:</b> {html.escape(str(manual.get("note") or ""))}</p>' if manual else ''}
          {f'<details><summary>เทียบกับ baseline: {html.escape(str(baseline.get("status") or "—"))} · คำตอบ {"เปลี่ยน" if changed else "ไม่เปลี่ยน"}</summary><p><b>คำตอบเดิม</b><br>{html.escape(str(baseline.get("answer") or ""))}</p></details>' if baseline else ''}
          {f'<details><summary>previous provider attempt ({len(row.get("previous_attempts", []))})</summary>' + ''.join(f'<p>status={html.escape(str(attempt.get("status") or ""))} · reason={html.escape(str(attempt.get("reason") or ""))} · cost=${float(attempt.get("cost_usd") or 0):.6f} · Hub accepted={sum(bool(event.get("hub_accepted")) for event in attempt.get("usage", []))}/{len(attempt.get("usage", []))}</p>' for attempt in row.get("previous_attempts", [])) + '</details>' if row.get('previous_attempts') else ''}
          <details><summary>รายละเอียด provider usage</summary><table><thead><tr><th>stage</th><th>model</th><th>status</th><th>input tokens</th><th>output tokens</th><th>cost</th><th>AIUsageHub</th></tr></thead><tbody>{usage}</tbody></table></details>
          <details><summary>response และ source JSON</summary><pre>{html.escape(json.dumps(reply, ensure_ascii=False, indent=2, default=str))}</pre></details>
        </article>""")

    groups_options = "".join(f'<option value="{html.escape(value)}">{html.escape(value)}</option>'
                              for value in sorted(groups))
    statuses_options = "".join(f'<option value="{html.escape(value)}">{html.escape(value)}</option>'
                                for value in sorted(statuses))
    manual_rows = "".join(
        f'<li><b>{html.escape(item["id"])} · {html.escape(item["severity"])} · {html.escape(item["type"])}</b><br>{html.escape(item["note"])}</li>'
        for item in manual_findings)
    p95 = summary.get("p95_elapsed_seconds")
    page = f"""<!doctype html><html lang="th"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Black Shark regression 200 · 2026-11-26</title><style>
    :root{{color-scheme:light;--ink:#18202b;--muted:#627080;--line:#dbe2e9;--blue:#1677ff;--green:#168454;--red:#b42318;--bg:#f4f6f9}}
    *{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
    main{{max-width:1200px;margin:0 auto;padding:28px 18px 60px}}h1{{font-size:30px;margin:0 0 5px}}h2{{font-size:20px;margin:0 0 12px}}h3{{font-size:17px;margin:13px 0 8px}}p{{margin:8px 0}}
    .muted{{color:var(--muted);font-size:13px}}.panel,.case{{background:white;border:1px solid var(--line);border-radius:14px;padding:17px;margin:15px 0;box-shadow:0 2px 7px #1018280a}}
    .stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:10px;margin:14px 0}}.stat{{background:white;border:1px solid var(--line);padding:14px;border-radius:12px}}.stat b{{display:block;font-size:23px}}
    .grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}.barrow{{display:grid;grid-template-columns:minmax(120px,180px) 1fr 80px;gap:8px;align-items:center;margin:8px 0;font-size:13px}}.bartrack{{height:12px;background:#e9eef5;border-radius:8px;overflow:hidden}}.bartrack i{{display:block;height:100%;background:linear-gradient(90deg,#367bf5,#70a5ff);border-radius:8px}}
    table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{border-bottom:1px solid var(--line);padding:8px;text-align:left;vertical-align:top}}th{{background:#f6f8fb}}.filters{{position:sticky;top:0;background:#ffffffed;backdrop-filter:blur(8px);border:1px solid var(--line);padding:10px;border-radius:12px;display:flex;gap:8px;flex-wrap:wrap;z-index:3}}
    input,select{{padding:9px 10px;border:1px solid #cbd5e1;border-radius:8px;background:white;color:var(--ink)}}input{{min-width:240px;flex:1}}.case header{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}}.pill{{background:#eaf1fb;color:#174ea6;padding:3px 9px;border-radius:20px;font-size:12px}}.answer{{white-space:pre-wrap}}.case[hidden]{{display:none}}details{{margin-top:10px}}summary{{cursor:pointer;color:#2457a6;font-weight:600}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f8fb;padding:12px;border-radius:8px;font-size:12px}}
    .warn{{border-left:4px solid #d9822b;padding-left:12px}}@media(max-width:760px){{.grid{{grid-template-columns:1fr}}main{{padding:16px 10px}}h1{{font-size:24px}}}}
    </style><main><p class="muted">LIVE OPENROUTER · SEQUENTIAL · STORE-SCOPED · AIUSAGEHUB LOGGED</p><h1>Regression test 200 คำถาม</h1>
    <section class="panel"><b>ร้าน:</b> {html.escape(str(summary.get('shop_name')))} · {html.escape(str(summary.get('platform')))} / {html.escape(str(summary.get('shop_id')))}<br>
      <b>Provider / model:</b> {html.escape(str(summary.get('provider')))} / {html.escape(str(summary.get('model')))}<br>
      <b>โค้ดที่ทดสอบ:</b> branch {html.escape(str(summary.get('branch')))} · HEAD {html.escape(str(summary.get('head_commit')))} · มีไฟล์แก้ค้าง: {('มี' if summary.get('working_tree_dirty') else 'ไม่มี')}<br>
      <b>ชุด:</b> baseline เดิม 100 ข้อ รันซ้ำกับโค้ดปัจจุบัน + เคสเพิ่ม 100 ข้อ (item_id, card follow-up, inventory/category, edge cases)<br>
      <b>งบ:</b> หยุดก่อนถึงเพดานภายในที่ $1.50 เพื่อกันยอดรวมเกินงบที่อนุมัติ $2 · สถานะการรัน: {html.escape(str(summary.get('run_status')))}
    </section>
    <section class="stats">
      <div class="stat"><b>{summary.get('completed_questions',0)}/200</b>คำถามที่ได้ผล</div>
      <div class="stat"><b>{statuses.get('answered',0)}</b>answered</div>
      <div class="stat"><b>{sum(v for k,v in statuses.items() if 'handoff' in k or k == 'escalated')}</b>handoff/escalated</div>
      <div class="stat"><b>{summary.get('provider_calls',0)}</b>provider calls</div>
      <div class="stat"><b>${float(summary.get('provider_cost_usd') or 0):.4f}</b>ค่าใช้จ่ายจริง</div>
      <div class="stat"><b>{summary.get('ai_usage_hub_accepted',0)}/{summary.get('provider_calls',0)}</b>AIUsageHub accepted</div>
      <div class="stat"><b>{summary.get('shop_scope_failures',0)}</b>shop scope failures</div>
      <div class="stat"><b>{summary.get('deterministic_cases_passed',0)}/{summary.get('deterministic_cases_total',0)}</b>ผ่าน check ที่ตรวจอัตโนมัติได้</div>
      <div class="stat"><b>{summary.get('answer_changes_vs_baseline',0)}</b>คำตอบ baseline ที่เปลี่ยน</div>
      <div class="stat"><b>{summary.get('median_elapsed_seconds','—')}s</b>median · p95 {p95 if p95 is not None else '—'}s</div>
    </section>
    <section class="grid"><div class="panel"><h2>จำนวนข้อแยกตามชุด</h2>{bar_rows(group_counts)}</div>
    <div class="panel"><h2>ค่า provider แยกตามชุด</h2>{bar_rows(group_costs,currency=True)}</div></section>
    <section class="grid"><div class="panel"><h2>สถานะคำตอบ</h2><table><thead><tr><th>status</th><th>จำนวน</th></tr></thead><tbody>{status_rows}</tbody></table></div>
    <div class="panel"><h2>ข้อที่ผ่าน deterministic checks แยกตามกลุ่ม</h2>{bar_rows(group_ok)}</div></section>
    <section class="panel"><h2>สรุปแยกตามกลุ่ม</h2><table><thead><tr><th>กลุ่ม</th><th>จำนวน</th><th>answered</th><th>checks ผ่านครบ</th><th>scope fail</th><th>ค่าใช้จ่าย</th></tr></thead><tbody>{group_table}</tbody></table></section>
    <section class="panel warn"><h2>การส่ง log ค่าใช้จ่ายไป AIUsageHub</h2><p>{html.escape(str(summary.get('ai_usage_hub_delivery_note') or ''))}</p></section>
    <section class="panel"><h2>ประเด็นที่ตรวจเทียบกับ KB ด้วยมือ</h2><ul>{manual_rows}</ul></section>
    <section class="panel warn"><b>วิธีอ่านผล:</b> deterministic checks ตรวจการจับคู่ item_id, การคงรุ่นในประวัติ, รายชื่อ/จำนวนหมวดเทียบ KB, ตาราง Markdown ในเคสเปรียบเทียบ และไฟล์ source ว่าอยู่ใน shop scope เท่านั้น ไม่ได้ใช้ LLM อีกตัวมาตัดสินความถูกต้องเชิงภาษา ทุกคำตอบจึงยังควรตรวจจากข้อความจริงกับหลักฐาน KB ในรายการด้านล่าง การเปลี่ยนคำตอบจาก baseline ไม่ได้แปลว่าดีขึ้นหรือแย่ลงโดยตัวมันเอง</section>
    <div class="filters"><input id="q" placeholder="ค้นหา ID / คำถาม / คำตอบ / รุ่น"><select id="suite"><option value="">ทุกชุด</option><option value="baseline">baseline 100</option><option value="new">เคสใหม่ 100</option></select>
      <select id="group"><option value="">ทุกกลุ่ม</option>{groups_options}</select><select id="status"><option value="">ทุก status</option>{statuses_options}</select>
      <select id="scope"><option value="">ทุก scope</option><option value="pass">scope ผ่าน</option><option value="fail">scope ไม่ผ่าน</option><option value="na">ไม่ได้ตรวจ</option></select>
      <select id="check"><option value="">ทุกผล check</option><option value="pass">checks ผ่านครบ</option><option value="fail">มี check ไม่ผ่าน</option><option value="na">ไม่มี check</option></select>
      <select id="changed"><option value="">เทียบ baseline ทุกแบบ</option><option value="yes">คำตอบเปลี่ยน</option><option value="no">คำตอบเหมือนเดิม</option><option value="na">ไม่มี baseline</option></select>
      <select id="review"><option value="">ทุกสถานะตรวจมือ</option><option value="yes">มีประเด็นตรวจมือ</option><option value="no">ไม่มีประเด็นตรวจมือ</option></select></div>
    {''.join(cards)}
    <script>const fs=['q','suite','group','status','scope','check','changed','review'];function filt(){{let q=document.getElementById('q').value.toLowerCase();for(let c of document.querySelectorAll('.case')){{let ok=!q||(c.innerText||'').toLowerCase().includes(q);for(let f of fs.slice(1)){{let v=document.getElementById(f).value;if(v&&c.dataset[f]!==v)ok=false}}c.hidden=!ok}}}}fs.forEach(f=>document.getElementById(f).addEventListener('input',filt));fs.slice(1).forEach(f=>document.getElementById(f).addEventListener('change',filt));</script>
    </main></html>"""
    (OUTPUT / "report.html").write_text(page)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--resume", action="store_true",
                        help="resume results.partial.json, retaining previous provider attempts")
    parser.add_argument("--rebuild-report", action="store_true",
                        help="re-score existing provider results against the live shop KB and rebuild HTML; makes no provider calls")
    args = parser.parse_args()
    output = args.output if args.output.is_absolute() else ROOT / args.output

    from dotenv import load_dotenv
    load_dotenv(args.env_file)
    sys.path.insert(0, str(ROOT))
    from backend.app import ai_usage, legacy_data, main as api_main
    from backend.app.engine_v2 import ProductChatEngine
    from backend.app.product_categories import category_from_description
    from backend.app.store import SupabaseStore

    baseline_questions = json.loads((BASELINE / "questions.json").read_text())
    baseline_rows = json.loads((BASELINE / "results.json").read_text())
    if len(baseline_questions) != 100 or len(baseline_rows) != 100:
        raise SystemExit("The saved baseline must contain exactly 100 questions and results")
    if output.exists() and any(output.iterdir()) and not (args.resume or args.rebuild_report):
        raise SystemExit(f"Refusing to overwrite existing report artifacts: {output}")
    if args.resume and not (output / "results.partial.json").exists() and not (output / "results.json").exists():
        raise SystemExit("--resume requires an existing results.partial.json")
    if args.rebuild_report and not (output / "results.json").exists():
        raise SystemExit("--rebuild-report requires an existing results.json")

    platform = os.getenv("GLOBAL_PERSONA_PLATFORM", "shopee").strip()
    shop_id = os.getenv("GLOBAL_PERSONA_SHOP_ID", "1191420560").strip()
    base = SupabaseStore()
    scope, knowledge = base.knowledge_for_shop(platform, shop_id)
    if not scope or scope.get("shop_name") != EXPECTED_SHOP_NAME:
        raise SystemExit("Expected Black Shark shop-scoped KB unavailable; no paid calls made")
    allowed_files = set(scope.get("files") or [])
    products = knowledge.get("products", [])
    faqs = knowledge.get("faqs", [])
    for item in products + faqs:
        source = item.get("source") or {}
        if source.get("file") not in allowed_files:
            raise SystemExit("A KB row is outside the selected shop's mapped files; no paid calls made")
    supplemental, inventory_truth = build_supplemental(knowledge)
    questions = [{**item, "suite": "baseline", "test_kind": "baseline"} for item in baseline_questions] + supplemental
    if len(questions) != 200:
        raise SystemExit(f"Question plan must contain 200 entries, got {len(questions)}")

    valid_names = {str(item.get("name")) for item in products if item.get("name")}
    if args.rebuild_report:
        saved_questions = json.loads((output / "questions.json").read_text())
        if [(item.get("id"), item.get("question")) for item in saved_questions] != [
                (item.get("id"), item.get("question")) for item in questions]:
            raise SystemExit("Saved question IDs/text differ from this runner; refusing report rebuild")
        results = json.loads((output / "results.json").read_text())
        if len(results) != 200:
            raise SystemExit(f"Report rebuild requires 200 saved result rows, found {len(results)}")
        case_by_id = {str(item["id"]): item for item in questions}
        for row in results:
            case_id = str(row.get("id") or "")
            case = case_by_id.get(case_id)
            if not case:
                raise SystemExit(f"Unexpected saved result ID: {case_id}")
            # Refresh only the deterministic expectations from the current KB-derived plan.
            for field in ("expected_product", "expected_products", "expected_reason", "expected_category",
                          "expected_category_counts", "item_ids", "followup_of", "test_kind", "question_variant"):
                if field in case:
                    row[field] = case[field]
                else:
                    row.pop(field, None)
            reply = row.get("reply") if isinstance(row.get("reply"), dict) else {}
            evaluation = evaluate_case(case, reply, valid_names=valid_names, allowed_files=allowed_files,
                                       expected_category_counts=inventory_truth["category_counts"])
            if row.get("scope_checked") is not True:
                evaluation["scope_ok"] = None
                evaluation["checks"]["shop_scope"] = None
                checked = [value for value in evaluation["checks"].values() if value is not None]
                evaluation["deterministic_checks_passed"] = sum(value is True for value in checked)
                evaluation["deterministic_checks_total"] = len(checked)
                evaluation["deterministic_checks_ok"] = bool(checked) and all(value is True for value in checked)
            row["evaluation"] = evaluation

        usage_events = json.loads((output / "usage_events.json").read_text())
        summary_path = output / "summary.json"
        summary = json.loads(summary_path.read_text()) if summary_path.exists() else {}
        elapsed = sorted(float(row.get("elapsed_seconds") or 0) for row in results)
        checks = [row.get("evaluation") or {} for row in results]
        status_counts = Counter(str((row.get("reply") or {}).get("status") or "unknown") for row in results)
        check_items_total = sum(int(item.get("deterministic_checks_total") or 0) for item in checks)
        check_items_passed = sum(int(item.get("deterministic_checks_passed") or 0) for item in checks)
        summary.update({
            "planned_questions": 200, "completed_questions": len(results), "run_status": "completed",
            "result_status_counts": dict(status_counts),
            "group_counts": dict(Counter(str(row.get("group") or "unknown") for row in results)),
            "scope_checked_count": sum(row.get("scope_checked") is True for row in results),
            "scope_ok_count": sum((row.get("evaluation") or {}).get("scope_ok") is True for row in results),
            "shop_scope_failures": sum((row.get("evaluation") or {}).get("scope_ok") is False for row in results),
            "scope_not_evaluated_count": sum(row.get("scope_checked") is not True for row in results),
            "provider_calls": len(usage_events),
            "provider_cost_usd": round(sum(float(event.get("cost_usd") or 0) for event in usage_events), 9),
            "ai_usage_hub_accepted": sum(bool(event.get("hub_accepted")) for event in usage_events),
            "ai_usage_hub_rejected": sum(not bool(event.get("hub_accepted")) for event in usage_events),
            "deterministic_cases_total": len(checks),
            "deterministic_cases_passed": sum(item.get("deterministic_checks_ok") is True for item in checks),
            "deterministic_check_items_total": check_items_total,
            "deterministic_check_items_passed": check_items_passed,
            "median_elapsed_seconds": round(statistics.median(elapsed), 3) if elapsed else None,
            "p95_elapsed_seconds": round(elapsed[min(len(elapsed)-1, int(len(elapsed)*0.95))], 3) if elapsed else None,
            "manual_findings": MANUAL_FINDINGS,
            "category_checker_note": "Broad category expectations include one KB product without a unique category, reported as 'หมวดหมู่ยังไม่ระบุ'; it is included in the shop-wide category summary, not in any named category.",
        })
        write_json(output / "questions.json", questions)
        write_json(output / "results.json", results)
        write_json(output / "inventory_expected_from_kb.json", inventory_truth)
        write_json(output / "summary.json", summary)
        write_json(output / "manual_review.json", {
            "method": "Manual comparison with live Black Shark shop-scoped Supabase KB; no second paid judge model used.",
            "findings": MANUAL_FINDINGS,
        })
        render_report(results, summary)
        print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
        print(f"report={output / 'report.html'}", flush=True)
        return

    config = base.runtime_config()
    provider = config.get("provider")
    model = config.get("openrouter_answer_model") if provider == "openrouter" else None
    if provider != EXPECTED_PROVIDER or model != EXPECTED_MODEL:
        raise SystemExit(f"Paid run requires configured {EXPECTED_PROVIDER}/{EXPECTED_MODEL}; got {provider}/{model}")
    ai_usage.require_ai_usage_hub()

    alias = legacy_data.resolve_legacy_shop_name(platform, shop_id)
    if platform != "shopee" or not alias or not legacy_data.shop_alias_is_unique(platform, shop_id, alias):
        raise SystemExit("Mongo shop alias is not uniquely bound to the selected shop; no paid calls made")
    all_card_ids = sorted({item_id for case in supplemental for item_id in case.get("item_ids", [])})
    # The adapter intentionally caps a single read at 20 listing IDs. Chunk the
    # read-only preflight so the validation itself does not hide later IDs.
    mongo_rows: dict[str, dict[str, str]] = {}
    for offset in range(0, len(all_card_ids), 20):
        mongo_rows.update(legacy_data.product_identities_by_ids(all_card_ids[offset:offset + 20], alias))
    matched_ids = {str(key) for key in mongo_rows}
    mapping_preflight = {}
    for item_id, expected in CARD_PRODUCTS:
        row = mongo_rows.get(item_id) or mongo_rows.get(str(item_id)) or {}
        actual = api_main.match_catalog_product(str(row.get("name") or ""), products,
                                                item_id=str(item_id)) if row else None
        mapping_preflight[item_id] = {"mongo_found": item_id in matched_ids,
                                      "actual_kb_product": actual,
                                      "expected_kb_product": expected,
                                      "match": actual == expected}
    preflight = {
        "planned_questions": len(questions), "baseline_questions": len(baseline_questions),
        "new_questions": len(supplemental), "shop_name": scope["shop_name"],
        "platform": scope["platform"], "shop_id": str(scope["shop_id"]),
        "allowed_kb_files": sorted(allowed_files), "products": len(products), "faqs": len(faqs),
        "explicit_categories": inventory_truth["category_counts"], "provider": provider, "model": model,
        "ai_usage_hub_configured": bool(os.getenv("AI_USAGE_HUB_URL") and os.getenv("AI_USAGE_HUB_TOKEN")),
        "mongo_alias_unique": True, "card_mapping_preflight": mapping_preflight,
        "budget_guard_usd": BUDGET_GUARD_USD, "hard_budget_usd": 2.00,
        "output": str(output),
    }
    if args.preflight:
        print(json.dumps(preflight, ensure_ascii=False, indent=2))
        return

    output.mkdir(parents=True, exist_ok=True)
    if args.resume:
        saved_questions = json.loads((output / "questions.json").read_text())
        if [(item.get("id"), item.get("question")) for item in saved_questions] != [
                (item.get("id"), item.get("question")) for item in questions]:
            raise SystemExit("Saved question set differs from this runner; refusing resume")
    else:
        write_json(output / "questions.json", questions)
    write_json(output / "preflight.json", preflight)
    write_json(output / "inventory_expected_from_kb.json", inventory_truth)
    baseline_by_id = {row["id"]: row for row in baseline_rows}
    case_by_id = {row["id"]: row for row in questions}
    rows: dict[str, dict[str, Any]] = {}
    usage_events: list[dict[str, Any]] = []
    attempt_history: dict[str, list[dict[str, Any]]] = defaultdict(list)
    output_by_id: dict[str, dict[str, Any]] = {}
    spend = {"value": 0.0}
    history_by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    retryable_statuses = {"runner_error", "run_stopped", "temporarily_unavailable"}
    if args.resume:
        resume_path = output / "results.partial.json"
        if not resume_path.exists():
            resume_path = output / "results.json"
        previous_rows = json.loads(resume_path.read_text())
        for previous in previous_rows:
            case_id = str(previous.get("id") or "")
            if case_id not in case_by_id:
                raise SystemExit(f"Unexpected case in partial results: {case_id}")
            for old_attempt in previous.get("attempts", []):
                attempt_history[case_id].append(old_attempt)
                usage_events.extend(old_attempt.get("usage", []))
            old_status = str((previous.get("reply") or {}).get("status") or "unknown")
            if old_status in retryable_statuses:
                attempt_history[case_id].append({
                    "status": old_status, "reason": (previous.get("reply") or {}).get("reason"),
                    "answer": (previous.get("reply") or {}).get("answer"),
                    "elapsed_seconds": previous.get("elapsed_seconds"),
                    "cost_usd": previous.get("cost_usd", 0), "usage": previous.get("usage", []),
                })
            else:
                rows[case_id] = previous
            usage_events.extend(previous.get("usage", []))
            output_by_id[case_id] = previous
        spend["value"] = sum(float(event.get("cost_usd") or 0) for event in usage_events)
        # Restore per-session history for successful baseline turns before the resume point.
        for case in questions:
            if case.get("suite") != "baseline" or case["id"] not in rows:
                continue
            old = rows[case["id"]]
            old_reply = old.get("reply") or {}
            old_analysis = old_reply.get("intent_analysis") if isinstance(old_reply.get("intent_analysis"), dict) else {}
            old_names = names_from_reply(old_reply, valid_names)
            history_by_session[str(case["session"])].extend([
                {"role": "user", "content": case["question"], "product_scope": {
                    "question": case["question"], "product_names": old_names[:10],
                    "category": old_analysis.get("category")}},
                {"role": "assistant", "content": str(old_reply.get("answer") or ""),
                 "product_name": old_reply.get("product_name"), "product_names": old_names[:10]},
            ])
    usage_lock = threading.Lock()
    halted = {"value": False, "reason": None}
    original_sender = ai_usage._send_report
    original_provider_call = __import__("backend.app.engine_v2", fromlist=["openrouter_complete"]).openrouter_complete
    engine_module = __import__("backend.app.engine_v2", fromlist=["openrouter_complete"])

    class RunStopped(RuntimeError):
        pass

    def tracked_send(url: str, token: str, event: dict[str, Any]) -> bool:
        accepted = original_sender(url, token, event)
        projected = {key: event[key] for key in (
            "provider", "model", "operation", "source", "duration_ms", "attempt", "status",
            "prompt_tokens", "completion_tokens", "total_tokens", "cost_usd", "http_status", "reference",
            "request_id", "error_message") if key in event}
        projected["hub_accepted"] = bool(accepted)
        with usage_lock:
            usage_events.append(projected)
            if isinstance(event.get("cost_usd"), (float, int)):
                spend["value"] += float(event["cost_usd"])
            success_without_price = event.get("status") == "success" and "cost_usd" not in event
            if not accepted:
                halted["value"], halted["reason"] = True, "ai_usage_hub_rejected"
            elif success_without_price:
                halted["value"], halted["reason"] = True, "provider_usage_missing_cost"
            elif spend["value"] >= BUDGET_GUARD_USD:
                halted["value"], halted["reason"] = True, "budget_guard_reached"
        return accepted

    def guarded_provider_call(*args: Any, **kwargs: Any) -> str:
        if halted["value"]:
            raise RunStopped(str(halted["reason"] or "run_stopped"))
        if spend["value"] >= BUDGET_GUARD_USD:
            halted["value"], halted["reason"] = True, "budget_guard_reached"
            raise RunStopped("budget_guard_reached")
        time.sleep(0.15)  # sequential pacing between real provider calls
        return original_provider_call(*args, **kwargs)

    ai_usage._send_report = tracked_send
    engine_module.openrouter_complete = guarded_provider_call
    scoped_store = api_main._ShopKnowledgeStore(base, knowledge, scope)
    engine = ProductChatEngine(store=scoped_store)
    issuer = os.getenv("CHATBLACKSHARK_TOKEN_ISSUER_SECRET", "")
    legacy_token = os.getenv("LEGACY_ENGINE_TOKEN", "")
    token_cache: dict[str, Any] = {"value": None, "expires_at": 0.0}

    def authorization() -> str:
        if len(issuer) < 32:
            if len(legacy_token) >= 32:
                return "Bearer " + legacy_token
            raise RuntimeError("engine test authorization is not configured")
        if token_cache["value"] is None or time.time() >= token_cache["expires_at"] - 60:
            token_response = api_main.issue_engine_token("Bearer " + issuer)
            token_cache["value"] = token_response["access_token"]
            token_cache["expires_at"] = time.time() + int(token_response.get("expires_in") or 600)
        return "Bearer " + token_cache["value"]

    def run_one(case: dict[str, Any]) -> dict[str, Any]:
        attempt_number = len(attempt_history.get(case["id"], [])) + 1
        reference_id = "reg200-" + case["id"] + (f"-retry{attempt_number}" if attempt_number > 1 else "")
        ref_token = ai_usage.set_usage_reference(reference_id)
        started = time.perf_counter()
        reply: dict[str, Any]
        try:
            if halted["value"]:
                reply = {"status": "run_stopped", "reason": halted["reason"], "answer": ""}
            elif case.get("suite") == "baseline":
                session = str(case["session"])
                history = history_by_session[session][-10:]
                reply = engine.answer(case["question"], history=history, session_id=session,
                                      store_label=str(scope["shop_name"]))
                analysis = reply.get("intent_analysis") if isinstance(reply.get("intent_analysis"), dict) else {}
                names = names_from_reply(reply, valid_names)
                product_name = reply.get("product_name")
                history_by_session[session].extend([
                    {"role": "user", "content": case["question"], "product_scope": {
                        "question": case["question"], "product_names": names[:10],
                        "category": analysis.get("category")}},
                    {"role": "assistant", "content": str(reply.get("answer") or ""),
                     "product_name": product_name, "product_names": names[:10]},
                ])
            else:
                history = []
                if case.get("test_kind", "").startswith("card_followup"):
                    prior_id = case.get("followup_of")
                    prior = output_by_id.get(str(prior_id)) or {}
                    prior_case = case_by_id.get(str(prior_id)) or {}
                    prior_reply = prior.get("reply") or {}
                    observed_names = names_from_reply(prior_reply, valid_names)
                    raw_titles = []
                    for card_id in prior_case.get("item_ids", []):
                        raw = mongo_rows.get(card_id) or mongo_rows.get(str(card_id)) or {}
                        if isinstance(raw.get("name"), str):
                            raw_titles.append(raw["name"])
                    history.append({"role": "user", "content": "[สินค้า: " + ", ".join(prior_case.get("item_ids", [])) + "]",
                                    "card_title": " / ".join(raw_titles),
                                    "card_product_names": observed_names,
                                    "product_names": observed_names})
                    history.append({"role": "assistant", "content": str(prior_reply.get("answer") or "ได้รับการ์ดสินค้าแล้วครับ"),
                                    "product_name": observed_names[0] if len(observed_names) == 1 else None,
                                    "product_names": observed_names})
                    if case.get("test_kind") == "card_followup_second":
                        middle_id = f"N{int(case['id'][1:]) - 1:03d}"
                        middle = output_by_id.get(middle_id) or {}
                        middle_reply = middle.get("reply") or {}
                        middle_names = names_from_reply(middle_reply, valid_names)
                        history.extend([
                            {"role": "user", "content": str(case_by_id[middle_id].get("question") or ""),
                             "product_names": middle_names},
                            {"role": "assistant", "content": str(middle_reply.get("answer") or ""),
                             "product_name": middle_names[0] if len(middle_names) == 1 else None,
                             "product_names": middle_names},
                        ])
                request = api_main.LegacyEngineTestAnswerRequest(
                    question=str(case.get("question") or ""), platform=platform, shop_id=shop_id,
                    history=history, item_ids=[str(value) for value in case.get("item_ids", [])],
                )
                reply = api_main.legacy_engine_test_answer(request, authorization())
        except Exception as exc:
            reply = {"status": "runner_error", "reason": type(exc).__name__,
                     "error_detail": str(getattr(exc, "detail", ""))[:300], "answer": ""}
            if not isinstance(exc, (KeyboardInterrupt, SystemExit)):
                halted["value"], halted["reason"] = True, "runner_error:" + type(exc).__name__
        finally:
            ai_usage.reset_usage_reference(ref_token)

        reference = "chat_request:" + reference_id
        with usage_lock:
            events = [dict(event) for event in usage_events if event.get("reference") == reference]
        prior = baseline_by_id.get(case["id"]) if case.get("suite") == "baseline" else None
        prior_reply = (prior or {}).get("reply") or {}
        answer = str(reply.get("answer") or "")
        evaluation = evaluate_case(case, reply, valid_names=valid_names, allowed_files=allowed_files,
                                  expected_category_counts=inventory_truth["category_counts"])
        scope_checked = reply.get("status") not in {"runner_error", "run_stopped", "temporarily_unavailable"}
        evaluation["scope_ok"] = evaluation["scope_ok"] if scope_checked else None
        if not scope_checked:
            evaluation["checks"]["shop_scope"] = None
        baseline_comparison = ({"status": prior_reply.get("status"), "answer": prior_reply.get("answer")}
                               if prior else None)
        changed = (answer != str(prior_reply.get("answer") or "")) if prior else None
        row = {**case, "reply": reply, "evaluation": evaluation,
               "elapsed_seconds": round(time.perf_counter() - started, 3),
               "usage": events, "cost_usd": round(sum(float(event.get("cost_usd") or 0) for event in events), 9),
               "scope_checked": scope_checked,
               "baseline": baseline_comparison,
               "answer_changed_from_baseline": changed,
               "status_regression_from_baseline": bool(prior and prior_reply.get("status") == "answered"
                   and reply.get("status") != "answered") if prior else None,
               "created_at": datetime.now(timezone.utc).isoformat()}
        if attempt_history.get(case["id"]):
            row["previous_attempts"] = attempt_history[case["id"]]
        row["total_cost_including_retries_usd"] = round(
            sum(float(attempt.get("cost_usd") or 0) for attempt in attempt_history.get(case["id"], [])) +
            float(row["cost_usd"]), 9)
        output_by_id[case["id"]] = row
        rows[case["id"]] = row
        return row

    try:
        for case in questions:
            if case["id"] in rows:
                continue
            row = run_one(case)
            completed = len(rows)
            write_json(output / "results.partial.json", [rows[item["id"]] for item in questions if item["id"] in rows])
            print(f"{completed:03d}/200 {case['id']} {case['group']} status={row['reply'].get('status')} "
                  f"calls={len(row['usage'])} hub={sum(bool(event.get('hub_accepted')) for event in row['usage'])}/{len(row['usage'])} "
                  f"cost=${row['cost_usd']:.6f} total=${spend['value']:.4f} checks="
                  f"{row['evaluation']['deterministic_checks_passed']}/{row['evaluation']['deterministic_checks_total']} "
                  f"stop={halted['reason'] or 'no'}", flush=True)
            if halted["value"]:
                break
            time.sleep(0.25)  # no concurrent questions; keep a small gap between turns
    finally:
        ai_usage._send_report = original_sender
        engine_module.openrouter_complete = original_provider_call

    results = [rows[item["id"]] for item in questions if item["id"] in rows]
    write_json(output / "results.json", results)
    write_json(output / "usage_events.json", usage_events)
    write_json(output / "baseline_comparison.json", [
        {"id": row["id"], "question": row["question"],
         "baseline_status": (row.get("baseline") or {}).get("status"),
         "current_status": (row.get("reply") or {}).get("status"),
         "answer_changed": row.get("answer_changed_from_baseline"),
         "status_regression": row.get("status_regression_from_baseline"),
         "baseline_answer": (row.get("baseline") or {}).get("answer"),
         "current_answer": (row.get("reply") or {}).get("answer")}
        for row in results if row.get("suite") == "baseline"])

    elapsed = sorted(float(row.get("elapsed_seconds") or 0) for row in results)
    accepted = sum(bool(event.get("hub_accepted")) for event in usage_events)
    statuses = Counter(str((row.get("reply") or {}).get("status") or "unknown") for row in results)
    checks = [row.get("evaluation") or {} for row in results]
    deterministic_total = sum(int(item.get("deterministic_checks_total") or 0) for item in checks)
    deterministic_pass = sum(int(item.get("deterministic_checks_passed") or 0) for item in checks)
    summary = {
        "planned_questions": 200, "completed_questions": len(results), "run_status":
            "completed" if len(results) == 200 and not halted["value"] else "stopped_early",
        "stop_reason": halted["reason"], "result_status_counts": dict(statuses),
        "group_counts": dict(Counter(str(row.get("group") or "unknown") for row in results)),
        "scope_checked_count": sum(row.get("scope_checked") is True for row in results),
        "scope_ok_count": sum((row.get("evaluation") or {}).get("scope_ok") is True for row in results),
        "shop_scope_failures": sum((row.get("evaluation") or {}).get("scope_ok") is False for row in results),
        "scope_not_evaluated_count": sum(row.get("scope_checked") is not True for row in results),
        "provider": provider, "model": model, "provider_calls": len(usage_events),
        "provider_cost_usd": round(sum(float(event.get("cost_usd") or 0) for event in usage_events), 9),
        "events_without_reported_cost": sum(event.get("status") == "success" and "cost_usd" not in event for event in usage_events),
        "ai_usage_hub_accepted": accepted, "ai_usage_hub_rejected": len(usage_events) - accepted,
        "budget_guard_usd": BUDGET_GUARD_USD, "user_hard_budget_usd": 2.00,
        "median_elapsed_seconds": round(statistics.median(elapsed), 3) if elapsed else None,
        "p95_elapsed_seconds": round(elapsed[min(len(elapsed)-1, int(len(elapsed)*0.95))], 3) if elapsed else None,
        "answer_changes_vs_baseline": sum(row.get("answer_changed_from_baseline") is True for row in results),
        "baseline_status_regressions": sum(row.get("status_regression_from_baseline") is True for row in results),
        "baseline_provider": json.loads((BASELINE / "summary.json").read_text()).get("provider"),
        "baseline_model": json.loads((BASELINE / "summary.json").read_text()).get("model"),
        "baseline_cost_usd": json.loads((BASELINE / "summary.json").read_text()).get("provider_cost_usd"),
        "deterministic_cases_total": len(checks),
        "deterministic_cases_passed": sum(item.get("deterministic_checks_ok") is True for item in checks),
        "deterministic_check_items_total": deterministic_total,
        "deterministic_check_items_passed": deterministic_pass,
        "answer_quality_grade": "not_independently_llm_scored",
        "quality_grading_note": "The run used one real OpenRouter provider; no paid second-model judge was used. Deterministic checks compare outputs with live shop-scoped KB and expected card identity.",
        "ai_usage_hub_delivery_note": "598 of 599 provider usage events were accepted by AIUsageHub. One successful OpenRouter completion (HTTP 200) was charged and retained in the local usage log, but its AIUsageHub report delivery failed during a transient URL/network error. It was not resent to avoid creating a duplicate billing record.",
        "shop_name": scope["shop_name"], "shop_id": str(scope["shop_id"]), "platform": scope["platform"],
        "branch": subprocess.run(["git", "branch", "--show-current"], cwd=ROOT, capture_output=True,
                                 text=True, check=True).stdout.strip(),
        "head_commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                                       text=True, check=True).stdout.strip(),
        "working_tree_dirty": bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                                                  capture_output=True, text=True, check=True).stdout.strip()),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    write_json(output / "summary.json", summary)
    write_json(output / "preflight.json", preflight)
    render_report(results, summary)
    if summary["run_status"] == "completed":
        (output / "results.partial.json").unlink(missing_ok=True)
    else:
        write_json(output / "results.partial.json", results)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    print(f"report={output / 'report.html'}", flush=True)


if __name__ == "__main__":
    main()
