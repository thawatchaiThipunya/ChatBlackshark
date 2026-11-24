"""Rerun the saved 100-question Black Shark set through the configured paid provider."""
from __future__ import annotations

import argparse
import html
import json
import os
import statistics
import sys
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "docs/test/results/blackshark_100_2026-11-24_kb_only"
OUTPUT = ROOT / "docs/test/results/blackshark_100_2026-11-24_provider_rerun"
USAGE_FIELDS = ("provider", "model", "operation", "source", "duration_ms", "attempt", "status",
                "prompt_tokens", "completion_tokens", "total_tokens", "cost_usd", "http_status", "reference")
MANUAL_FINDINGS = [
    {"id": "Q002", "severity": "medium", "type": "incomplete_answer",
     "note": "The customer asks whether GS3 Ultra can be worn for swimming. The answer cites 5ATM and swim tracking, which are present in KB B5, but never gives a clear recommendation or caveat."},
    {"id": "Q089", "severity": "medium", "type": "knowledge_base_duplicate",
     "note": "The watch list includes both Black Shark PureLoop and z Black Shark PureLoop. Both map to the same KB row B16, so the 15-model count is inflated by a duplicate source record; this issue also exists in the baseline."},
    {"id": "Q092", "severity": "high", "type": "cross_shop_attribution",
     "note": "The question asks for Youpin Online Store products, but the answer lists three products from this Black Shark shop's KB while attributing them to Youpin. Source-file isolation passed, but the answer crosses the requested shop boundary. Baseline handed off; the new answered status is not a quality improvement."},
    {"id": "Q100", "severity": "medium", "type": "incomplete_comparison",
     "note": "The comparison correctly gives S3 at 5ATM and S3 Classic at 3ATM and avoids asserting unverified swimming support, but it does not say which model is the more suitable choice based on the available evidence."},
]


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temp.replace(path)


def source_files(reply: dict) -> list[str]:
    files = []
    for source in reply.get("sources", []) if isinstance(reply.get("sources"), list) else []:
        if not isinstance(source, dict):
            continue
        file_name = source.get("file")
        if not file_name and isinstance(source.get("source"), dict):
            file_name = source["source"].get("file")
        if isinstance(file_name, str) and file_name:
            files.append(file_name)
    return list(dict.fromkeys(files))


def usage_projection(event: dict, accepted: bool) -> dict:
    projected = {key: event[key] for key in USAGE_FIELDS if key in event}
    projected["hub_accepted"] = bool(accepted)
    return projected


def render_report(rows: list[dict], baseline_rows: list[dict], summary: dict) -> None:
    baseline_by_id = {row["id"]: row for row in baseline_rows}
    review_by_id = {row["id"]: row for row in summary.get("manual_findings", [])}
    counts = Counter(str(row.get("reply", {}).get("status") or "unknown") for row in rows)
    evaluated_rows = [row for row in rows if row.get("scope_checked", True)]
    changed = sum(str((row.get("reply") or {}).get("answer") or "") !=
                  str(((baseline_by_id.get(row["id"]) or {}).get("reply") or {}).get("answer") or "")
                  for row in evaluated_rows)
    regressions = [row for row in evaluated_rows if row.get("status_regression") or row.get("scope_ok") is False]
    cards = []
    for row in rows:
        prior = baseline_by_id.get(row["id"], {})
        prior_reply = prior.get("reply") or {}
        reply = row.get("reply") or {}
        status = html.escape(str(reply.get("status") or "unknown"))
        blocked = row.get("scope_checked") is False
        status_regression = bool(row.get("status_regression"))
        answer_changed = (str(reply.get("answer") or "") != str(prior_reply.get("answer") or "")) if not blocked else False
        regression = status_regression or (not blocked and row.get("scope_ok") is False)
        scope_label = "ไม่ได้ทดสอบ" if blocked else "ผ่าน" if row.get("scope_ok") else "ไม่ผ่าน"
        refs = ", ".join(f"{item.get('file', '')} {item.get('cell', '')}".strip()
                          for item in reply.get("sources", []) if isinstance(item, dict))
        usage_items = "".join(
            f"<li>{html.escape(str(event.get('source') or event.get('operation') or 'call'))} · "
            f"{html.escape(str(event.get('model') or ''))} · "
            f"{event.get('prompt_tokens', '?')} in / {event.get('completion_tokens', '?')} out · "
            f"${float(event.get('cost_usd') or 0):.6f} · "
            f"Hub {'✓' if event.get('hub_accepted') else '✗'}</li>"
            for event in row.get("usage", []))
        flags = []
        if status_regression:
            flags.append("สถานะถอยจาก baseline")
        if blocked:
            flags.append("provider ปฏิเสธก่อนตรวจ scope")
        elif row.get("scope_ok") is False:
            flags.append("scope ไม่ผ่าน")
        if answer_changed:
            flags.append("คำตอบเปลี่ยน")
        if row["id"] in review_by_id:
            flags.append("มีประเด็นตรวจมือ")
        cards.append(f"""<article class="case" data-group="{html.escape(row.get('group',''))}"
          data-status="{status}" data-changed="{'yes' if answer_changed else 'no'}"
          data-regression="{'yes' if regression else 'no'}"
          data-review="{'yes' if row['id'] in review_by_id else 'no'}">
          <div class="head"><b>{html.escape(row['id'])}</b><span class="badge">{status}</span>
          <span class="muted">{html.escape(row.get('group',''))} · {row.get('elapsed_seconds',0)}s · ${float(row.get('cost_usd') or 0):.6f}</span>
          <span class="flag">{html.escape(' · '.join(flags))}</span></div>
          <h3>{html.escape(row.get('question',''))}</h3>
          <p><b>ตอบรอบนี้</b><br><span class="answer">{html.escape(str(reply.get('answer') or ''))}</span></p>
          <details><summary>เทียบ baseline และดูหลักฐาน</summary>
          <p><b>Baseline ({html.escape(str(prior_reply.get('status') or '—'))})</b><br>
          <span class="answer">{html.escape(str(prior_reply.get('answer') or ''))}</span></p>
          <p><b>รุ่นที่จับได้:</b> {html.escape(', '.join((reply.get('intent_analysis') or {}).get('product_names') or []))}<br>
          <b>Intent:</b> {html.escape(str(reply.get('intent') or (reply.get('intent_analysis') or {}).get('intent') or '—'))}<br>
          <b>Source:</b> {html.escape(refs or 'ไม่มี source ref')}<br>
          <b>Scope:</b> {scope_label} · <b>จำนวน call:</b> {len(row.get('usage',[]))}</p>
          <ul>{usage_items}</ul><pre>{html.escape(json.dumps(reply.get('sources') or [], ensure_ascii=False, indent=2))}</pre>
          </details></article>""")
    groups = defaultdict(list)
    for row in rows:
        groups[row.get("group", "unknown")].append(row)
    group_rows = "".join(
        f"<tr><td>{html.escape(group)}</td><td>{len(items)}</td>"
        f"<td>{sum(item.get('reply',{}).get('status') == 'answered' for item in items)}</td>"
        f"<td>{sum(item.get('scope_checked', True) and item.get('scope_ok') is False for item in items)}</td>"
        f"<td>{sum(item.get('scope_checked', True) is False for item in items)}</td></tr>"
        for group, items in groups.items())
    manual_rows = "".join(
        f"<li><b>{html.escape(item['id'])} · {html.escape(item['severity'])} · "
        f"{html.escape(item['type'])}</b> — {html.escape(item['note'])}</li>"
        for item in summary.get("manual_findings", []))
    page = f"""<!doctype html><html lang="th"><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1"><title>Provider rerun · Black Shark</title>
    <style>
    :root{{--bg:#f4f2ec;--ink:#242a2a;--line:#d9ddd8;--accent:#075b50;--muted:#697371}}
    *{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,sans-serif}}
    main{{max-width:1200px;margin:auto;padding:28px 18px 70px}}h1{{font-size:clamp(1.8rem,4vw,3rem);line-height:1.1}}
    .panel,.case{{background:white;border:1px solid var(--line);border-radius:14px;padding:16px;margin:12px 0}}
    .stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:10px;margin:20px 0}}
    .stat{{background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px}}.stat b{{font-size:1.6rem;display:block}}
    .filters{{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0}}input,select{{font:inherit;padding:9px;border:1px solid var(--line);border-radius:8px;background:white}}
    input{{min-width:230px;flex:1}}.head{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}}.muted{{color:var(--muted)}}
    .badge{{background:#dcefe8;color:#075b50;border-radius:20px;padding:2px 8px;font-weight:700}}
    .flag{{color:#a33;font-weight:700}}.answer{{white-space:pre-wrap}}details{{border-top:1px solid var(--line);margin-top:12px;padding-top:10px}}
    summary{{cursor:pointer;color:var(--accent);font-weight:700}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f4f2;padding:10px;border-radius:8px}}
    table{{width:100%;border-collapse:collapse}}td,th{{padding:8px;border-bottom:1px solid var(--line);text-align:left}}
    </style><main><p class="muted">LIVE PROVIDER · SHOP SCOPED</p><h1>เทียบผล provider กับชุด 100 คำถาม</h1>
    <section class="panel"><b>ร้าน:</b> {html.escape(summary.get('shop_name',''))} · {html.escape(summary.get('platform',''))} / {html.escape(str(summary.get('shop_id','')))}<br>
    <b>Provider / model:</b> {html.escape(summary.get('provider',''))} / {html.escape(summary.get('model',''))}<br>
    <b>หมายเหตุ:</b> ผลนี้เทียบ status, answer, source และ shop scope กับ baseline ไม่มีการส่งคำตอบหรือ KB ไปให้ judge provider รายที่สอง
    </section><section class="stats">
    <div class="stat"><b>{len(rows)}/100</b>คำถามที่รัน</div><div class="stat"><b>{counts.get('answered',0)}</b>answered</div>
    <div class="stat"><b>{counts.get('need_model',0)}</b>need_model</div><div class="stat"><b>{counts.get('handoff_mock',0)+counts.get('handoff_required',0)+counts.get('escalated',0)}</b>handoff</div>
    <div class="stat"><b>{changed}</b>คำตอบที่เปลี่ยนจาก baseline</div><div class="stat"><b>{len(regressions)}</b>status regression / scope fail</div>
    <div class="stat"><b>${float(summary.get('provider_cost_usd') or 0):.4f}</b>ค่าใช้จ่าย provider</div>
    <div class="stat"><b>{summary.get('ai_usage_hub_accepted',0)}/{summary.get('provider_calls',0)}</b>AI Usage Hub accepted</div>
    <div class="stat"><b>{summary.get('median_elapsed_seconds','—')}s</b>median ต่อคำถาม</div></section>
    <section class="panel"><h2>แยกตามกลุ่มคำถาม</h2><table><thead><tr><th>กลุ่ม</th><th>จำนวน</th><th>answered</th><th>scope fail</th><th>scope ไม่ได้ทดสอบ</th></tr></thead><tbody>{group_rows}</tbody></table></section>
    <section class="panel"><h2>ประเด็นที่ตรวจเทียบกับ KB ด้วยมือ</h2><p>เป็นการตรวจเฉพาะรายการที่ระบุ ไม่ใช่คะแนน accuracy ครบทั้ง 100 ข้อ</p><ul>{manual_rows}</ul></section>
    <div class="filters"><input id="q" placeholder="ค้นคำถาม/คำตอบ/รุ่น"><select id="group"><option value="">ทุกกลุ่ม</option>{''.join(f'<option value="{html.escape(group)}">{html.escape(group)}</option>' for group in groups)}</select>
    <select id="status"><option value="">ทุก status</option>{''.join(f'<option value="{html.escape(status)}">{html.escape(status)}</option>' for status in sorted(counts))}</select>
    <select id="change"><option value="">ทุกแถว</option><option value="yes">คำตอบเปลี่ยน</option><option value="no">คำตอบไม่เปลี่ยน</option></select>
    <select id="regression"><option value="">รวมทุกผล</option><option value="yes">เฉพาะ regression/scope fail</option></select>
    <select id="review"><option value="">ทุกแถวตรวจมือ</option><option value="yes">มีประเด็นตรวจมือ</option><option value="no">ไม่มีประเด็นตรวจมือ</option></select></div>
    {''.join(cards)}</main><script>
    const fields=['q','group','status','change','regression','review'];function filter(){{const q=document.getElementById('q').value.toLowerCase();
    for(const c of document.querySelectorAll('.case')){{const s=(c.innerText||'').toLowerCase();let ok=!q||s.includes(q);
    for(const f of fields.slice(1)){{const v=document.getElementById(f).value;if(v&&c.dataset[f]!==v)ok=false}}c.hidden=!ok}}
    }}fields.forEach(f=>document.getElementById(f).addEventListener('input',filter));fields.slice(1).forEach(f=>document.getElementById(f).addEventListener('change',filter));
    </script></html>"""
    (OUTPUT / "report.html").write_text(page)


def main() -> None:
    global OUTPUT
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--output", type=Path, default=OUTPUT,
                        help="write this run separately so earlier provider results remain unchanged")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--render-only", action="store_true")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    OUTPUT = args.output if args.output.is_absolute() else ROOT / args.output

    if args.render_only:
        results = json.loads((OUTPUT / "results.json").read_text())
        baseline_rows = json.loads((BASELINE / "results.json").read_text())
        summary = json.loads((OUTPUT / "summary.json").read_text())
        summary["manual_findings"] = MANUAL_FINDINGS
        write_json(OUTPUT / "summary.json", summary)
        write_json(OUTPUT / "manual_review.json", MANUAL_FINDINGS)
        render_report(results, baseline_rows, summary)
        print(f"rendered={OUTPUT / 'report.html'} manual_findings={len(MANUAL_FINDINGS)}")
        return

    from dotenv import load_dotenv
    load_dotenv(args.env_file)
    sys.path.insert(0, str(ROOT))
    from backend.app import ai_usage, main as api_main
    from backend.app.engine_v2 import ProductChatEngine
    from backend.app.store import SupabaseStore

    baseline_questions = json.loads((BASELINE / "questions.json").read_text())
    baseline_rows = json.loads((BASELINE / "results.json").read_text())
    if len(baseline_questions) != 100 or len(baseline_rows) != 100:
        raise SystemExit("The baseline must contain exactly 100 questions and 100 results")

    platform = os.getenv("GLOBAL_PERSONA_PLATFORM", "shopee").strip()
    shop_id = os.getenv("GLOBAL_PERSONA_SHOP_ID", "1191420560").strip()
    base = SupabaseStore()
    scope, knowledge = base.knowledge_for_shop(platform, shop_id)
    if not scope or scope.get("shop_name") != "Black Shark Thailand":
        raise SystemExit("Black Shark shop scope unavailable; paid run stopped")
    allowed_files = set(scope.get("files") or [])
    for item in knowledge.get("products", []) + knowledge.get("faqs", []):
        source = item.get("source") or {}
        if source.get("file") not in allowed_files:
            raise SystemExit("Live KB has a source outside the exact Black Shark shop scope; paid run stopped")
    config = base.runtime_config()
    baseline_summary = json.loads((BASELINE / "summary.json").read_text())
    if args.preflight:
        print(json.dumps({"question_count": len(baseline_questions), "shop_name": scope["shop_name"],
            "shop_id": str(scope["shop_id"]), "platform": scope["platform"],
            "products": len(knowledge.get("products", [])), "faqs": len(knowledge.get("faqs", [])),
            "provider": config.get("provider"),
            "model": config.get("openrouter_answer_model") if config.get("provider") == "openrouter"
                else config.get("gemini_answer_model"),
            "baseline_provider": baseline_summary.get("provider"), "baseline_model": baseline_summary.get("model"),
            "baseline_cost_usd": baseline_summary.get("provider_cost_usd"),
            "estimated_provider_cost_usd": baseline_summary.get("provider_cost_usd"),
            "ai_usage_hub_configured": bool(os.getenv("AI_USAGE_HUB_URL") and os.getenv("AI_USAGE_HUB_TOKEN"))},
            ensure_ascii=False, indent=2))
        return

    ai_usage.require_ai_usage_hub()
    provider = config.get("provider")
    model = config.get("openrouter_answer_model") if provider == "openrouter" else config.get("gemini_answer_model")
    if not provider or not model:
        raise SystemExit("Provider/model is not configured; paid run stopped")

    scoped_store = api_main._ShopKnowledgeStore(base, knowledge, scope)
    engine = ProductChatEngine(store=scoped_store)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    write_json(OUTPUT / "questions.json", baseline_questions)
    write_json(OUTPUT / "kb_snapshot.json", {"scope": scope, "knowledge": knowledge})
    histories: dict[str, list[dict]] = defaultdict(list)
    rows: dict[str, dict] = {}
    usage_events: list[dict] = []
    usage_lock = threading.Lock()
    result_lock = threading.Lock()
    stopped = threading.Event()
    original_sender = ai_usage._send_report

    def tracked_send(url, token, event):
        accepted = original_sender(url, token, event)
        projected = usage_projection(event, accepted)
        with usage_lock:
            usage_events.append(projected)
        if not accepted:
            stopped.set()
        return accepted

    ai_usage._send_report = tracked_send
    by_session: dict[str, list[dict]] = defaultdict(list)
    for question in baseline_questions:
        by_session[question["session"]].append(question)

    def run_session(session_questions: list[dict]) -> None:
        session = session_questions[0]["session"]
        history: list[dict] = []
        for item in session_questions:
            if stopped.is_set():
                return
            usage_reference = ai_usage.set_usage_reference("provider-rerun-" + item["id"])
            started = time.perf_counter()
            try:
                reply = engine.answer(item["question"], history=history[-10:], session_id=session,
                                      store_label=scope["shop_name"])
            except Exception as exc:
                reply = {"status": "runner_error", "reason": type(exc).__name__, "answer": ""}
                stopped.set()
            finally:
                ai_usage.reset_usage_reference(usage_reference)
            reference = "chat_request:provider-rerun-" + item["id"]
            with usage_lock:
                calls = [event.copy() for event in usage_events if event.get("reference") == reference]
            analysis = reply.get("intent_analysis") if isinstance(reply.get("intent_analysis"), dict) else {}
            product_names = analysis.get("product_names") if isinstance(analysis.get("product_names"), list) else []
            product_name = reply.get("product_name")
            valid_names = {row.get("name") for row in knowledge.get("products", [])}
            files = source_files(reply)
            scope_checked = reply.get("status") not in {"temporarily_unavailable", "runner_error"}
            scope_ok = (all(name in valid_names for name in product_names if name) \
                and (not product_name or product_name in valid_names) \
                and all(file_name in allowed_files for file_name in files)) if scope_checked else None
            row = {**item, "reply": reply, "elapsed_seconds": round(time.perf_counter() - started, 2),
                   "usage": calls, "cost_usd": round(sum(float(call.get("cost_usd") or 0) for call in calls), 9),
                   "scope_checked": scope_checked, "scope_ok": scope_ok, "source_files": files,
                   "created_at": datetime.now(timezone.utc).isoformat()}
            with result_lock:
                rows[item["id"]] = row
                current = [rows[key] for key in sorted(rows)]
                write_json(OUTPUT / "results.partial.json", current)
            names = product_names or ([product_name] if product_name else [])
            history.extend([
                {"role": "user", "content": item["question"], "product_scope": {
                    "question": item["question"], "product_names": names[:10],
                    "category": analysis.get("category")}},
                {"role": "assistant", "content": str(reply.get("answer") or ""),
                 "product_name": product_name, "product_names": names[:10]},
            ])
            print(f"{item['id']} {item['group']} {reply.get('status')} calls={len(calls)} "
                  f"hub={sum(bool(call.get('hub_accepted')) for call in calls)}/{len(calls)} "
                  f"cost=${row['cost_usd']:.6f} scope={scope_ok}", flush=True)

    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 6))) as executor:
            futures = [executor.submit(run_session, items) for items in by_session.values()]
            for future in as_completed(futures):
                future.result()
    finally:
        ai_usage._send_report = original_sender

    results = [rows[key] for key in sorted(rows)]
    write_json(OUTPUT / "results.json", results)
    write_json(OUTPUT / "usage_events.json", usage_events)
    baseline_by_id = {row["id"]: row for row in baseline_rows}
    comparisons = []
    for row in results:
        prior = baseline_by_id[row["id"]]
        prior_reply, reply = prior.get("reply") or {}, row.get("reply") or {}
        scope_checked = bool(row.get("scope_checked", True))
        status_regression = (scope_checked and prior_reply.get("status") == "answered"
                             and reply.get("status") != "answered")
        comparisons.append({"id": row["id"], "question": row["question"],
            "baseline_status": prior_reply.get("status"), "current_status": reply.get("status"),
            "evaluation_status": "evaluated" if scope_checked else "provider_blocked",
            "status_regression": status_regression,
            "answer_changed": (str(prior_reply.get("answer") or "") != str(reply.get("answer") or ""))
                if scope_checked else None,
            "baseline_source_files": source_files(prior_reply), "current_source_files": row["source_files"],
            "scope_checked": scope_checked, "scope_ok": row["scope_ok"], "elapsed_delta_seconds": round(
                float(row.get("elapsed_seconds") or 0) - float(prior.get("elapsed_seconds") or 0), 2),
            "baseline_answer": prior_reply.get("answer", ""), "current_answer": reply.get("answer", "")})
    write_json(OUTPUT / "comparison.json", comparisons)
    statuses = Counter(str(row.get("reply", {}).get("status") or "unknown") for row in results)
    elapsed = sorted(float(row.get("elapsed_seconds") or 0) for row in results)
    total_cost = round(sum(float(row.get("cost_usd") or 0) for row in results), 9)
    summary = {"question_count": 100, "completed_questions": len(results),
        "result_status_counts": dict(statuses), "group_counts": dict(Counter(row["group"] for row in results)),
        "scope_checked_count": sum(bool(row.get("scope_checked", True)) for row in results),
        "scope_ok_count": sum(row.get("scope_ok") is True for row in results),
        "shop_scope_failures": sum(row.get("scope_ok") is False for row in results),
        "scope_not_evaluated_count": sum(row.get("scope_checked") is False for row in results),
        "provider": provider, "model": model, "provider_calls": len(usage_events),
        "provider_cost_usd": total_cost,
        "ai_usage_hub_accepted": sum(bool(event.get("hub_accepted")) for event in usage_events),
        "ai_usage_hub_rejected": sum(not bool(event.get("hub_accepted")) for event in usage_events),
        "median_elapsed_seconds": round(statistics.median(elapsed), 2) if elapsed else None,
        "p95_elapsed_seconds": round(elapsed[min(len(elapsed)-1, int(len(elapsed)*0.95))], 2) if elapsed else None,
        "shop_name": scope["shop_name"], "shop_id": str(scope["shop_id"]), "platform": scope["platform"],
        "baseline_provider": baseline_summary.get("provider"), "baseline_model": baseline_summary.get("model"),
        "baseline_provider_cost_usd": baseline_summary.get("provider_cost_usd"),
        "manual_findings": MANUAL_FINDINGS,
        "answer_quality_grade": "not_independently_scored",
        "quality_grading_note": "Answers are compared with the saved provider run and shop-scoped KB sources; no second provider judge was called.",
        "created_at": datetime.now(timezone.utc).isoformat()}
    write_json(OUTPUT / "summary.json", summary)
    render_report(results, baseline_rows, summary)
    if len(results) == 100 and not stopped.is_set():
        (OUTPUT / "results.partial.json").unlink(missing_ok=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
