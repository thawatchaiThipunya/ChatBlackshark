"""Run the saved 100-question Black Shark suite sequentially against the live provider."""
from __future__ import annotations

import argparse
import html
import json
import os
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "docs/test/results/blackshark_100_2026-11-26_current_logic"
DEFAULT_OUTPUT = ROOT / "docs/test/results/blackshark_100_after_handoff_fix_2026-11-27"
EXPECTED_SHOP = "Black Shark Thailand"
EXPECTED_PROVIDER = "openrouter"
EXPECTED_MODEL = "google/gemini-3.5-flash-lite"


def _names(reply: dict) -> list[str]:
    result: list[str] = []
    analysis = reply.get("intent_analysis") or {}
    if isinstance(analysis, dict) and isinstance(analysis.get("product_names"), list):
        result.extend(name for name in analysis["product_names"] if isinstance(name, str))
    if isinstance(reply.get("product_name"), str):
        result.append(reply["product_name"])
    for field in ("product_cards", "products"):
        for card in reply.get(field, []) if isinstance(reply.get(field), list) else []:
            if isinstance(card, dict):
                result.extend(card[key] for key in ("product_name", "name") if isinstance(card.get(key), str))
    return list(dict.fromkeys(result))


def _files(reply: dict) -> list[str]:
    result = []
    for source in reply.get("sources", []) if isinstance(reply.get("sources"), list) else []:
        if isinstance(source, dict):
            filename = source.get("file") or (source.get("source") or {}).get("file")
            if isinstance(filename, str) and filename:
                result.append(filename)
    return list(dict.fromkeys(result))


def _report_html(summary: dict, rows: list[dict], output: Path) -> None:
    esc = lambda value: html.escape(str(value if value is not None else ""), quote=True)
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[str(row.get("group") or "unknown")].append(row)
    status_counts = Counter(str((row.get("reply") or {}).get("status") or "unknown") for row in rows)
    status_cards = "".join(f'<div class="stat"><b>{count}</b>{esc(status)}</div>'
                           for status, count in sorted(status_counts.items()))
    group_rows = "".join(
        f'<tr><td>{esc(group)}</td><td>{len(items)}</td>'
        f'<td>{esc(dict(Counter(str((item.get("reply") or {}).get("status") or "unknown") for item in items)))}</td>'
        f'<td>${sum(float(item.get("cost_usd") or 0) for item in items):.5f}</td>'
        f'<td>{sum(not item.get("scope_ok") for item in items)}</td></tr>'
        for group, items in sorted(groups.items()))
    group_options = "".join(f'<option>{esc(group)}</option>' for group in sorted(groups))
    status_options = "".join(f'<option>{esc(status)}</option>' for status in sorted(status_counts))
    cards = []
    for row in rows:
        reply = row.get("reply") or {}
        usage = "".join(
            f'<tr><td>{esc(event.get("source"))}</td><td>{esc(event.get("model"))}</td>'
            f'<td>{esc(event.get("status"))}</td><td>${float(event.get("cost_usd") or 0):.6f}</td>'
            f'<td>{"รับแล้ว" if event.get("hub_accepted") else "ไม่รับ"}</td></tr>'
            for event in row.get("usage", []))
        sources = " · ".join(esc(f'{source.get("file")} {source.get("cell", "")}')
                             for source in reply.get("sources", []) if isinstance(source, dict)) or "ไม่มี source reference"
        flags = row.get("review_flags") or []
        cards.append(
            f'<article class="case" data-group="{esc(row.get("group"))}" '
            f'data-status="{esc(reply.get("status"))}" '
            f'data-reg="{"yes" if row.get("status_regression_from_baseline") else "no"}">'
            f'<header><b>{esc(row.get("id"))}</b><span class="pill">{esc(reply.get("status"))}</span>'
            f'<span>{esc(row.get("group"))} · {float(row.get("elapsed_seconds") or 0):.2f}s · '
            f'${float(row.get("cost_usd") or 0):.5f}</span></header>'
            f'<h3>{esc(row.get("question"))}</h3><p><b>คำตอบจริง</b></p>'
            f'<div class="answer">{esc(reply.get("answer"))}</div>'
            f'<p><b>Intent / reason:</b> {esc(reply.get("intent"))} / {esc(reply.get("reason"))} · '
            f'<b>รุ่น:</b> {esc(", ".join(row.get("product_names") or []) or "—")}</p>'
            f'<p><b>Shop scope:</b> {"ผ่าน" if row.get("scope_ok") else "ไม่ผ่าน"} · '
            f'<b>KB:</b> {sources} · <b>เทียบรอบก่อน:</b> {esc(row.get("baseline_status"))} · '
            f'{"status ถอย" if row.get("status_regression_from_baseline") else "คำตอบเปลี่ยน" if row.get("answer_changed_from_baseline") else "สถานะ/คำตอบเดิม"}</p>'
            f'{f"<p class=warn>ตรวจทาน: {esc(", ".join(flags))}</p>" if flags else ""}'
            f'<details><summary>คำตอบรอบก่อน: {esc(row.get("baseline_status"))} · '
            f'{"เปลี่ยน" if row.get("answer_changed_from_baseline") else "เหมือนเดิม"}</summary>'
            f'<div class="answer">{esc(row.get("baseline_answer"))}</div></details>'
            f'<details><summary>Provider usage ({len(row.get("usage", []))})</summary>'
            f'<table><thead><tr><th>Stage</th><th>Model</th><th>Status</th><th>Cost</th><th>AIUsageHub</th></tr></thead>'
            f'<tbody>{usage}</tbody></table></details>'
            f'<details><summary>Response JSON</summary><pre>{esc(json.dumps(reply, ensure_ascii=False, indent=2))}</pre></details></article>')
    html_page = (
        '<!doctype html><html lang="th"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Black Shark 100-question live regression</title><style>'
        ':root{--bg:#f4f6f9;--line:#dbe2e9;--ink:#18202b;--muted:#627080;--red:#b42318}'
        '*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,sans-serif}'
        'main{max-width:1180px;margin:auto;padding:28px 18px 60px}.panel,.case,.stat{background:white;border:1px solid var(--line);'
        'border-radius:13px;padding:16px;margin:14px 0;box-shadow:0 2px 8px #1018280a}.stats{display:grid;'
        'grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}.stat{margin:0}.stat b{display:block;font-size:24px}'
        '.filters{display:flex;gap:8px;flex-wrap:wrap;position:sticky;top:0;background:#fffffff0;padding:10px;'
        'border:1px solid var(--line);border-radius:12px}input,select{padding:9px;border:1px solid var(--line);border-radius:8px}'
        'input{min-width:220px;flex:1}table{border-collapse:collapse;width:100%;font-size:13px}th,td{border-bottom:1px solid var(--line);'
        'padding:8px;text-align:left}.answer{white-space:pre-wrap;background:#f7f8fa;border-radius:8px;padding:12px}'
        'header{display:flex;gap:12px;align-items:center;flex-wrap:wrap;color:var(--muted);font-size:13px}'
        '.pill{background:#eaf0f7;border-radius:30px;padding:3px 10px;color:var(--ink)}.warn{color:var(--red)}'
        'pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:500px;overflow:auto}[hidden]{display:none!important}</style><main>'
        '<h1>Regression test · 100 คำถามจริง</h1>'
        f'<p>รัน {esc(summary.get("created_at"))} · {esc(summary.get("shop_name"))} · '
        f'{esc(summary.get("provider"))} / {esc(summary.get("model"))} · ยิงเรียงทีละข้อ</p>'
        f'<section class="panel"><b>ขอบเขต:</b> {esc(summary.get("platform"))} / {esc(summary.get("shop_id"))} · '
        f'KB {summary.get("products_in_shop_kb")} สินค้า + {summary.get("faqs_in_shop_kb")} FAQ · '
        f'<b>งบ guard:</b> ${summary.get("budget_guard_usd", 0):.2f} · '
        f'<b>สถานะ:</b> {esc(summary.get("run_status"))} {esc(summary.get("stop_reason") or "")}</section>'
        f'<section class="stats"><div class="stat"><b>{summary.get("completed_questions")}/100</b>เสร็จ</div>{status_cards}'
        f'<div class="stat"><b>{summary.get("provider_calls")}</b>provider calls</div>'
        f'<div class="stat"><b>${summary.get("provider_cost_usd", 0):.4f}</b>ค่าใช้จ่ายจริง</div>'
        f'<div class="stat"><b>{summary.get("ai_usage_hub_accepted")}/{summary.get("provider_calls")}</b>AIUsageHub accepted</div>'
        f'<div class="stat"><b>{summary.get("shop_scope_failures")}</b>scope errors</div>'
        f'<div class="stat"><b>{summary.get("median_elapsed_seconds") or 0:.2f}s</b>median / p95 {summary.get("p95_elapsed_seconds") or 0:.2f}s</div></section>'
        f'<section class="panel"><h2>สรุปตามกลุ่ม</h2><table><thead><tr><th>กลุ่ม</th><th>จำนวน</th><th>สถานะ</th>'
        f'<th>ค่าใช้จ่าย</th><th>Scope errors</th></tr></thead><tbody>{group_rows}</tbody></table>'
        f'<p>คำตอบเปลี่ยน {summary.get("answer_changes_vs_previous_run")} ข้อ · '
        f'status regression: {esc(", ".join(summary.get("status_regressions_vs_previous_run", [])) or "ไม่มี")} · '
        f'ดีขึ้น: {esc(", ".join(summary.get("status_improvements_vs_previous_run", [])) or "ไม่มี")}</p>'
        '<p>สถานะและ shop scope ตรวจอัตโนมัติ; ใช้ source ที่แสดงในแต่ละข้อช่วยตรวจความถูกต้องเชิงเนื้อหา ไม่มี LLM judge เพิ่ม</p></section>'
        f'<div class="filters"><input id="q" placeholder="ค้นหา ID / คำถาม / คำตอบ / รุ่น">'
        f'<select id="g"><option value="">ทุกกลุ่ม</option>{group_options}</select>'
        f'<select id="s"><option value="">ทุกสถานะ</option>{status_options}</select>'
        '<select id="r"><option value="">ทุก transition</option><option value="yes">status regression</option>'
        '<option value="no">อื่น ๆ</option></select></div>' + "".join(cards)
        + '<script>function f(){const q=document.getElementById("q").value.toLowerCase();for(const c of document.querySelectorAll(".case")){'
        'let ok=!q||c.innerText.toLowerCase().includes(q);for(const [i,k] of [["g","group"],["s","status"],["r","reg"]]){'
        'const v=document.getElementById(i).value;if(v&&c.dataset[k]!==v)ok=false}c.hidden=!ok}}'
        'for(const i of ["q","g","s","r"])document.getElementById(i).addEventListener("input",f);</script></main></html>')
    (output / "report.html").write_text(html_page)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--budget-usd", type=float, default=1.50)
    args = parser.parse_args()
    load_dotenv(args.env_file)
    sys.path.insert(0, str(ROOT))
    from backend.app import ai_usage, engine_v2
    from backend.app.engine_v2 import ProductChatEngine
    from backend.app.main import _ShopKnowledgeStore
    from backend.app.store import SupabaseStore

    questions = json.loads((BASELINE / "questions.json").read_text())
    baseline_rows = json.loads((BASELINE / "results.json").read_text())
    if len(questions) != 100 or len(baseline_rows) != 100:
        raise SystemExit("Expected the saved 100-question suite; no provider calls made")
    output = args.output if args.output.is_absolute() else ROOT / args.output
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"Refusing to overwrite report artifacts: {output}")

    platform = os.getenv("GLOBAL_PERSONA_PLATFORM", "shopee").strip()
    shop_id = os.getenv("GLOBAL_PERSONA_SHOP_ID", "1191420560").strip()
    base = SupabaseStore()
    scope, knowledge = base.knowledge_for_shop(platform, shop_id)
    if not scope or scope.get("shop_name") != EXPECTED_SHOP:
        raise SystemExit("Expected Black Shark shop-scoped KB; no provider calls made")
    allowed_files = set(scope.get("files") or [])
    for row in (knowledge.get("products") or []) + (knowledge.get("faqs") or []):
        if (row.get("source") or {}).get("file") not in allowed_files:
            raise SystemExit("A KB row is outside this shop's file mapping; no provider calls made")
    config = base.runtime_config()
    if config.get("provider") != EXPECTED_PROVIDER or config.get("openrouter_answer_model") != EXPECTED_MODEL:
        raise SystemExit("Configured provider/model does not match the approved live test; no provider calls made")
    ai_usage.require_ai_usage_hub()

    output.mkdir(parents=True, exist_ok=True)
    valid_names = {str(row.get("name")) for row in knowledge.get("products", []) if row.get("name")}
    baseline_by_id = {row["id"]: row for row in baseline_rows}
    usage_events: list[dict] = []
    results: list[dict] = []
    history_by_session: dict[str, list[dict]] = defaultdict(list)
    spend = [0.0]
    halted = {"value": False, "reason": None}
    original_sender = ai_usage._send_report
    original_call = engine_v2.openrouter_complete

    def tracked_send(url: str, token: str, event: dict) -> bool:
        accepted = original_sender(url, token, event)
        projected = {key: event[key] for key in (
            "provider", "model", "operation", "source", "duration_ms", "attempt", "status",
            "prompt_tokens", "completion_tokens", "total_tokens", "cost_usd", "http_status",
            "reference", "request_id", "error_message") if key in event}
        projected["hub_accepted"] = bool(accepted)
        usage_events.append(projected)
        if isinstance(event.get("cost_usd"), (float, int)):
            spend[0] += float(event["cost_usd"])
        if not accepted:
            halted.update(value=True, reason="ai_usage_hub_rejected")
        elif event.get("status") == "success" and "cost_usd" not in event:
            halted.update(value=True, reason="provider_usage_missing_cost")
        elif spend[0] >= args.budget_usd:
            halted.update(value=True, reason="budget_guard_reached")
        return accepted

    def guarded_call(*call_args, **call_kwargs):
        if halted["value"] or spend[0] >= args.budget_usd:
            halted.update(value=True, reason=halted["reason"] or "budget_guard_reached")
            raise RuntimeError("live regression stopped by safety guard")
        time.sleep(0.35)
        return original_call(*call_args, **call_kwargs)

    ai_usage._send_report = tracked_send
    engine_v2.openrouter_complete = guarded_call
    engine = ProductChatEngine(store=_ShopKnowledgeStore(base, knowledge, scope))
    try:
        for index, case in enumerate(questions, 1):
            if halted["value"]:
                break
            reference = f"reg100-{case['id']}"
            usage_context = ai_usage.set_usage_reference(reference)
            started = time.perf_counter()
            try:
                session = str(case.get("session") or "")
                history = history_by_session[session][-10:]
                reply = engine.answer(str(case.get("question") or ""), history=history,
                                      session_id=session, store_label=str(scope["shop_name"]))
            except Exception as exc:
                reply = {"status": "runner_error", "reason": type(exc).__name__, "answer": ""}
                halted.update(value=True, reason="runner_error:" + type(exc).__name__)
            finally:
                ai_usage.reset_usage_reference(usage_context)

            names = _names(reply)
            files = _files(reply)
            scope_ok = all(name in valid_names for name in names) and all(filename in allowed_files for filename in files)
            old_reply = (baseline_by_id.get(str(case["id"])) or {}).get("reply") or {}
            events = [dict(event) for event in usage_events
                      if event.get("reference") == "chat_request:" + reference]
            answer = str(reply.get("answer") or "")
            flags = []
            if not scope_ok:
                flags.append("shop_scope_or_product_name_mismatch")
            if old_reply.get("status") == "answered" and reply.get("status") != "answered":
                flags.append("status_regression_from_previous_run")
            if reply.get("status") == "answered" and reply.get("intent") not in {"smalltalk", "safety_refusal"} and not files:
                flags.append("answered_without_source_reference")
            row = {**case, "reply": reply, "elapsed_seconds": round(time.perf_counter() - started, 3),
                   "usage": events, "cost_usd": round(sum(float(event.get("cost_usd") or 0) for event in events), 9),
                   "scope_checked": True, "scope_ok": scope_ok, "source_files": files, "product_names": names,
                   "baseline_status": old_reply.get("status"), "baseline_answer": old_reply.get("answer"),
                   "answer_changed_from_baseline": answer != str(old_reply.get("answer") or ""),
                   "status_regression_from_baseline": old_reply.get("status") == "answered" and reply.get("status") != "answered",
                   "status_improvement_from_baseline": old_reply.get("status") != "answered" and reply.get("status") == "answered",
                   "review_flags": flags, "created_at": datetime.now(timezone.utc).isoformat()}
            results.append(row)
            (output / "results.partial.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
            print(f"{index:03d}/100 {case['id']} {case.get('group')} status={reply.get('status')} "
                  f"calls={len(events)} hub={sum(bool(event.get('hub_accepted')) for event in events)}/{len(events)} "
                  f"cost=${row['cost_usd']:.5f} total=${spend[0]:.4f} scope={'ok' if scope_ok else 'FAIL'} "
                  f"stop={halted['reason'] or 'no'}", flush=True)
            if halted["value"]:
                break
            time.sleep(0.2)

            analysis = reply.get("intent_analysis") or {}
            history_names = [name for name in names if name in valid_names]
            history_by_session[session].extend([
                {"role": "user", "content": str(case.get("question") or ""),
                 "product_scope": {"question": str(case.get("question") or ""),
                                  "product_names": history_names[:10],
                                  "category": analysis.get("category") if isinstance(analysis, dict) else None}},
                {"role": "assistant", "content": answer, "product_name": reply.get("product_name"),
                 "product_names": history_names[:10]},
            ])
    finally:
        ai_usage._send_report = original_sender
        engine_v2.openrouter_complete = original_call

    statuses = Counter(str((row.get("reply") or {}).get("status") or "unknown") for row in results)
    by_group: dict[str, list[dict]] = defaultdict(list)
    for row in results:
        by_group[str(row.get("group") or "unknown")].append(row)
    elapsed = sorted(float(row.get("elapsed_seconds") or 0) for row in results)
    summary = {
        "run_id": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "created_at": datetime.now(timezone.utc).isoformat(), "planned_questions": 100,
        "completed_questions": len(results), "run_status": "completed" if len(results) == 100 else "stopped",
        "stop_reason": halted["reason"], "shop_name": scope["shop_name"], "platform": platform,
        "shop_id": str(shop_id), "allowed_kb_files": sorted(allowed_files),
        "products_in_shop_kb": len(knowledge.get("products") or []), "faqs_in_shop_kb": len(knowledge.get("faqs") or []),
        "provider": config.get("provider"), "model": config.get("openrouter_answer_model"),
        "provider_calls": len(usage_events), "provider_cost_usd": round(sum(float(event.get("cost_usd") or 0) for event in usage_events), 9),
        "budget_guard_usd": args.budget_usd, "ai_usage_hub_accepted": sum(bool(event.get("hub_accepted")) for event in usage_events),
        "ai_usage_hub_rejected": sum(not bool(event.get("hub_accepted")) for event in usage_events),
        "result_status_counts": dict(statuses), "shop_scope_failures": sum(not row["scope_ok"] for row in results),
        "answer_changes_vs_previous_run": sum(bool(row["answer_changed_from_baseline"]) for row in results),
        "status_regressions_vs_previous_run": [row["id"] for row in results if row["status_regression_from_baseline"]],
        "status_improvements_vs_previous_run": [row["id"] for row in results if row["status_improvement_from_baseline"]],
        "review_flags": [{"id": row["id"], "flags": row["review_flags"]} for row in results if row["review_flags"]],
        "group_stats": {group: {
            "count": len(rows), "status_counts": dict(Counter(str((row.get("reply") or {}).get("status") or "unknown") for row in rows)),
            "provider_calls": sum(len(row.get("usage") or []) for row in rows),
            "provider_cost_usd": round(sum(float(row.get("cost_usd") or 0) for row in rows), 9),
            "median_elapsed_seconds": round(statistics.median(float(row.get("elapsed_seconds") or 0) for row in rows), 3),
            "scope_failures": sum(not row["scope_ok"] for row in rows),
        } for group, rows in by_group.items()},
        "median_elapsed_seconds": round(statistics.median(elapsed), 3) if elapsed else None,
        "p95_elapsed_seconds": elapsed[min(len(elapsed) - 1, int(len(elapsed) * 0.95))] if elapsed else None,
        "accuracy_note": "รายงานแสดงคำตอบและ KB source ทุกข้อ; ตรวจ status/shop scope อัตโนมัติ ส่วนความถูกต้องเชิงความหมายต้องอ่านคำตอบเทียบกับ source ไม่มี LLM judge เพิ่ม",
    }
    (output / "questions.json").write_text(json.dumps(questions, ensure_ascii=False, indent=2))
    (output / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
    (output / "usage_events.json").write_text(json.dumps(usage_events, ensure_ascii=False, indent=2))
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    _report_html(summary, results, output)
    print(json.dumps({"run_status": summary["run_status"], "completed": len(results), "statuses": dict(statuses),
                      "calls": summary["provider_calls"], "hub": f"{summary['ai_usage_hub_accepted']}/{summary['provider_calls']}",
                      "cost_usd": summary["provider_cost_usd"], "scope_errors": summary["shop_scope_failures"],
                      "status_regressions": summary["status_regressions_vs_previous_run"],
                      "report": str(output / "report.html"), "summary": str(output / "summary.json")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
