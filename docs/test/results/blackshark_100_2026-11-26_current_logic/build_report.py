from __future__ import annotations
import html, json, statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent
BASELINE = ROOT / 'docs/test/results/blackshark_100_2026-11-24_kb_only'
GROUP_LABELS = {
    'conversation': 'บทสนทนาต่อเนื่อง', 'catalog': 'ค้นหารายการสินค้า',
    'spec': 'ถามสเปก', 'faq': 'FAQ / แก้ปัญหา',
    'shop_boundary': 'ขอบเขตร้าน', 'inventory_compare': 'แนะนำ / เปรียบเทียบ',
}
STATUS_LABELS = {
    'answered': 'ตอบแล้ว', 'handoff_mock': 'ส่งต่อ', 'handoff_required': 'ส่งต่อ',
    'need_model': 'ถามหารุ่น', 'temporarily_unavailable': 'provider ใช้ไม่ได้',
    'runner_error': 'runner error', 'unknown': 'ไม่ทราบสถานะ',
}
STATUS_COLORS = {
    'answered': '#16836b', 'handoff_mock': '#e7a338', 'handoff_required': '#e7a338',
    'need_model': '#5475b8', 'temporarily_unavailable': '#bd4452', 'runner_error': '#764a89',
    'unknown': '#7d8785',
}


def load(name: str):
    return json.loads((OUT / name).read_text())


def esc(value) -> str:
    return html.escape(str(value if value is not None else ''), quote=True)


def source_files(reply: dict) -> list[str]:
    result=[]
    for src in reply.get('sources', []) if isinstance(reply.get('sources'), list) else []:
        if not isinstance(src, dict): continue
        file=src.get('file') or (src.get('source') or {}).get('file')
        if file: result.append(str(file))
    return list(dict.fromkeys(result))


def format_answer(value) -> str:
    text=str(value or '')
    lines=text.splitlines()
    chunks=[]; i=0
    while i < len(lines):
        line=lines[i]
        if line.strip().startswith('|'):
            table=[]
            while i < len(lines) and lines[i].strip().startswith('|'):
                table.append(lines[i].strip()); i+=1
            parsed=[]
            for raw in table:
                cells=[cell.strip() for cell in raw.strip('|').split('|')]
                if cells and all(set(cell.replace(':','').replace('-','').replace(' ','').replace('–',''))==set() for cell in cells):
                    continue
                if cells and all(set(cell.replace(':','').replace('-','').replace(' ','').replace('–','')) <= set() for cell in cells):
                    continue
                if cells and all(cell and set(cell) <= {':','-',' '} for cell in cells):
                    continue
                parsed.append(cells)
            if parsed:
                head=parsed[0]
                body=parsed[1:]
                chunks.append('<div class="table-wrap"><table><thead><tr>'+''.join('<th>'+esc(c)+'</th>' for c in head)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+esc(c)+'</td>' for c in row)+'</tr>' for row in body)+'</tbody></table></div>')
            continue
        if line.strip(): chunks.append('<div class="answer-line">'+esc(line)+'</div>')
        else: chunks.append('<div class="answer-gap"></div>')
        i+=1
    return ''.join(chunks) or '<span class="muted">ไม่มีคำตอบ</span>'


def horizontal_chart(title: str, items: list[tuple[str, float, str]], suffix: str='') -> str:
    if not items: return ''
    max_value=max((v for _,v,_ in items), default=1) or 1
    width=820; row_h=33; top=42; height=top+len(items)*row_h+12
    parts=[f'<figure class="chart"><figcaption>{esc(title)}</figcaption><svg viewBox="0 0 {width} {height}" role="img" aria-label="{esc(title)}">']
    for idx,(label,value,color) in enumerate(items):
        y=top+idx*row_h
        label_text=label if len(label)<29 else label[:27]+'…'
        parts.append(f'<text x="4" y="{y+18}" class="chart-label">{esc(label_text)}</text>')
        parts.append(f'<rect x="220" y="{y+4}" width="510" height="19" rx="7" fill="#eef1ef"/>')
        bar=max(2,510*value/max_value) if value else 0
        if bar: parts.append(f'<rect x="220" y="{y+4}" width="{bar:.1f}" height="19" rx="7" fill="{color}"/>')
        parts.append(f'<text x="744" y="{y+18}" class="chart-value">{value:g}{esc(suffix)}</text>')
    parts.append('</svg></figure>')
    return ''.join(parts)


def stacked_group_chart(rows: list[dict]) -> str:
    groups=list(dict.fromkeys(r.get('group','unknown') for r in rows))
    statuses=['answered','handoff_mock','need_model','temporarily_unavailable','runner_error']
    totals={g:Counter(r.get('reply',{}).get('status') or 'unknown' for r in rows if r.get('group')==g) for g in groups}
    max_total=max(sum(totals[g].values()) for g in groups) or 1
    width=880; top=44; row_h=38; height=top+len(groups)*row_h+52
    pieces=['<figure class="chart"><figcaption>สถานะคำตอบแยกตามกลุ่ม</figcaption>',f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="สถานะคำตอบแยกตามกลุ่ม">']
    for i,g in enumerate(groups):
        y=top+i*row_h
        pieces.append(f'<text x="4" y="{y+18}" class="chart-label">{esc(GROUP_LABELS.get(g,g))}</text>')
        x=225
        for s in statuses:
            count=totals[g].get(s,0)
            w=520*count/max_total
            if count:
                pieces.append(f'<rect x="{x:.1f}" y="{y+3}" width="{w:.1f}" height="21" fill="{STATUS_COLORS[s]}" rx="4"><title>{esc(STATUS_LABELS[s])}: {count}</title></rect>')
                if w>28: pieces.append(f'<text x="{x+w/2:.1f}" y="{y+18}" class="inside-label">{count}</text>')
                x+=w
        pieces.append(f'<text x="760" y="{y+18}" class="chart-value">{sum(totals[g].values())} ข้อ</text>')
    y=height-22; x=4
    for s in statuses:
        pieces.append(f'<rect x="{x}" y="{y-11}" width="11" height="11" rx="2" fill="{STATUS_COLORS[s]}"/><text x="{x+16}" y="{y}" class="legend">{esc(STATUS_LABELS[s])}</text>')
        x += 145 if s not in {'temporarily_unavailable'} else 190
    pieces.append('</svg></figure>')
    return ''.join(pieces)


def main():
    rows=load('results.json'); baseline=load('results.json') if False else json.loads((BASELINE/'results.json').read_text())
    baseline_summary=json.loads((BASELINE/'summary.json').read_text())
    usage=load('usage_events.json')
    aborted=load('aborted_attempts.json')
    kb=load('kb_snapshot.json')
    by_base={r['id']:r for r in baseline}
    status_counts=Counter(str(r.get('reply',{}).get('status') or 'unknown') for r in rows)
    group_counts=Counter(r.get('group','unknown') for r in rows)
    scope_checked=sum(bool(r.get('scope_checked')) for r in rows)
    scope_ok=sum(r.get('scope_ok') is True for r in rows)
    scope_fail=sum(r.get('scope_ok') is False for r in rows)
    scope_skipped=sum(r.get('scope_checked') is False for r in rows)
    answer_changed=0; transitions=Counter(); changed_ids=set(); comparison=[]
    regressions=[]; improvements=[]
    for r in rows:
        prev=by_base.get(r['id'],{}); pr=prev.get('reply') or {}; cur=r.get('reply') or {}
        changed=str(pr.get('answer') or '')!=str(cur.get('answer') or '')
        if changed: answer_changed+=1; changed_ids.add(r['id'])
        transition=(pr.get('status') or 'unknown',cur.get('status') or 'unknown')
        transitions[transition]+=1
        reg=transition[0]=='answered' and transition[1]!='answered'
        imp=transition[0]!='answered' and transition[1]=='answered'
        if reg: regressions.append(r['id'])
        if imp: improvements.append(r['id'])
        comparison.append({'id':r['id'],'group':r.get('group'),'question':r.get('question'),
          'baseline_status':transition[0],'current_status':transition[1],
          'status_regression':reg,'status_improvement':imp,'answer_changed':changed,
          'scope_checked':r.get('scope_checked'),'scope_ok':r.get('scope_ok'),
          'baseline_source_files':source_files(pr),'current_source_files':source_files(cur),
          'baseline_answer':pr.get('answer',''),'current_answer':cur.get('answer',''),
          'elapsed_seconds':r.get('elapsed_seconds'),'elapsed_delta_seconds':round(float(r.get('elapsed_seconds') or 0)-float(prev.get('elapsed_seconds') or 0),2)})
    (OUT/'comparison.json').write_text(json.dumps(comparison,ensure_ascii=False,indent=2))
    latency=sorted(float(r.get('elapsed_seconds') or 0) for r in rows)
    med=round(statistics.median(latency),2) if latency else None
    p95=round(latency[min(len(latency)-1,int(len(latency)*.95))],2) if latency else None
    response_cost=round(sum(float(r.get('cost_usd') or 0) for r in rows),9)
    aborted_cost=round(float(aborted.get('cost_usd') or 0),9)
    judge_events=load('judge_usage.json') if (OUT/'judge_usage.json').exists() else []
    judge_cost=round(sum(float(e.get('cost_usd') or 0) for e in judge_events),9)
    all_events=[]
    for r in rows:
        for event in r.get('usage',[]):
            all_events.append({'question_id':r.get('id'),'group':r.get('group'),'attempt_group':'100_question_results',**event})
    for r in aborted.get('rows',[]):
        for event in r.get('usage',[]):
            all_events.append({'question_id':r.get('id'),'group':r.get('group'),'attempt_group':'aborted_initial_run',**event})
    for event in judge_events:
        all_events.append({'attempt_group':'quality_judge',**event})
    (OUT/'all_usage_events.json').write_text(json.dumps(all_events,ensure_ascii=False,indent=2))
    hub_accepted=sum(bool(e.get('hub_accepted')) for e in all_events)
    hub_rejected=sum(not bool(e.get('hub_accepted')) for e in all_events)
    total_calls=len(all_events)
    group_stats={}
    for group in group_counts:
        values=[r for r in rows if r.get('group')==group]
        group_stats[group]={'count':len(values),'status_counts':dict(Counter(r.get('reply',{}).get('status') or 'unknown' for r in values)),
          'provider_calls':sum(len(r.get('usage',[])) for r in values),
          'provider_cost_usd':round(sum(float(r.get('cost_usd') or 0) for r in values),9),
          'median_elapsed_seconds':round(statistics.median(float(r.get('elapsed_seconds') or 0) for r in values),2) if values else None,
          'scope_failures':sum(r.get('scope_ok') is False for r in values)}
    summary={'question_count':100,'completed_questions':len(rows),'result_status_counts':dict(status_counts),'group_counts':dict(group_counts),'group_stats':group_stats,
      'scope_checked_count':scope_checked,'scope_ok_count':scope_ok,'shop_scope_failures':scope_fail,'scope_not_evaluated_count':scope_skipped,
      'provider':'openrouter','model':'google/gemini-3.5-flash-lite','shop_name':kb['scope']['shop_name'],'shop_id':str(kb['scope']['shop_id']),'platform':kb['scope']['platform'],
      'provider_calls_in_100_question_results':sum(len(r.get('usage',[])) for r in rows),'provider_cost_usd':response_cost,
      'ai_usage_hub_accepted_in_100_question_results':sum(bool(e.get('hub_accepted')) for e in usage),'ai_usage_hub_rejected_in_100_question_results':sum(not bool(e.get('hub_accepted')) for e in usage),
      'aborted_attempt_questions':aborted.get('question_count',0),'aborted_attempt_provider_calls':aborted.get('provider_calls',0),'aborted_attempt_cost_usd':aborted_cost,
      'judge_provider_calls':len(judge_events),'judge_cost_usd':judge_cost,'total_paid_cost_usd':round(response_cost+aborted_cost+judge_cost,9),
      'all_attempt_provider_calls_including_aborted_and_judge':total_calls,'all_usage_hub_accepted':hub_accepted,'all_usage_hub_rejected':hub_rejected,
      'median_elapsed_seconds':med,'p95_elapsed_seconds':p95,'answers_changed_vs_baseline':answer_changed,
      'status_transitions':{' -> '.join(k):v for k,v in transitions.items()},'status_regressions':regressions,'status_improvements':improvements,
      'baseline_provider_cost_usd':baseline_summary.get('provider_cost_usd'),'baseline_provider_calls':baseline_summary.get('provider_calls'),
      'baseline_median_elapsed_seconds':baseline_summary.get('median_elapsed_seconds'),'baseline_p95_elapsed_seconds':baseline_summary.get('p95_elapsed_seconds'),
      'answer_quality_grade':'not_independently_scored','quality_grading_note':'Second-provider judge request was blocked by automatic approval review because it would send private shop KB and all answers externally. Do not interpret answered status as correctness.',
      'created_at':datetime.now(timezone.utc).isoformat(),'tested_code_branch':'codex/fix-shop-kb-followups','tested_code_commit':'51c45e4'}
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    review=[
      {'id':'Q069','severity':'expected_handoff','type':'missing_specification','note':'KB product row B23 has no network-band/network-type specification. Handoff is consistent with the rule to escalate unsupported KB questions; the answer does not invent bands.'},
      {'id':'Q076','severity':'high','type':'faq_not_answered','note':'A3 sleep-tracking troubleshooting answer exists in shop KB FAQ C27 and that FAQ source is included in the reply, yet the engine returns handoff_mock / insufficient_shop_evidence. This is a concrete FAQ-routing/evidence-use issue.'},
      {'id':'Q085','severity':'medium','type':'indirect_availability_answer','note':'Asked whether the shop sells Kieslect watches; reply says it cannot confirm because this shop has Black Shark watches. It avoids asserting Kieslect availability, but does not plainly say the KB lists only Black Shark watches.'},
      {'id':'Q092','severity':'passed_shop_boundary','type':'other_shop_request','note':'Explicit Youpin Online Store request gets a polite different-shop response, status answered, no handoff, and no foreign shop sources.'},
      {'id':'Q094','severity':'context_limit','type':'standalone_reference','note':'This case is run in its own session without prior recommendation history, so “the product you recommended” has no referent in the test context; asking which model is reasonable for this isolated input and does not test multi-turn reference resolution.'},
      {'id':'Q098-Q100','severity':'render_check','type':'markdown_comparison_tables','note':'All three compare answers contain Markdown pipe tables. This report renders them as HTML tables; the production chat UI rendering was not browser-tested in this run.'},
    ]
    (OUT/'manual_review.json').write_text(json.dumps(review,ensure_ascii=False,indent=2))
    # Every item remains filterable; this summary is intentionally not a semantic correctness score.
    all_products={p.get('name') for p in kb['knowledge'].get('products',[])}
    cards=[]
    for r in rows:
        prev=by_base.get(r['id'],{}); pr=prev.get('reply') or {}; cur=r.get('reply') or {}
        st=str(cur.get('status') or 'unknown'); grp=str(r.get('group') or 'unknown')
        refs=[]
        for src in cur.get('sources',[]) if isinstance(cur.get('sources'),list) else []:
            if isinstance(src,dict): refs.append(' '.join(str(x) for x in (src.get('file') or (src.get('source') or {}).get('file'),src.get('sheet'),src.get('cell')) if x))
        usage_html=''.join(f'<li>{esc(e.get("source") or e.get("operation") or "call")} · {esc(e.get("model") or "")} · {e.get("prompt_tokens","?")} in / {e.get("completion_tokens","?")} out · ${float(e.get("cost_usd") or 0):.6f} · Hub {"รับแล้ว" if e.get("hub_accepted") else "ไม่รับ"}</li>' for e in r.get('usage',[]))
        flags=[]
        if r['id'] in changed_ids: flags.append('คำตอบต่างจาก baseline')
        if r.get('scope_ok') is False: flags.append('scope fail')
        if not r.get('scope_checked'): flags.append('scope ไม่ได้ตรวจ')
        if r['id'] in {x['id'] for x in review if x['severity'] not in {'passed_shop_boundary'}}: flags.append('spot check')
        attempts=''
        if r.get('attempts'):
            attempts='<div class="attempts"><b>มี retry</b>: '+''.join(f'<p>ครั้งที่ {a.get("attempt")}: {esc(a.get("status"))} · ${float(a.get("cost_usd") or 0):.6f} · Hub {sum(bool(e.get("hub_accepted")) for e in a.get("usage",[]))}/{len(a.get("usage",[]))}</p>' for a in r['attempts'])+'</div>'
        products=(cur.get('intent_analysis') or {}).get('product_names') or []
        cards.append(f'''<article class="case" data-group="{esc(grp)}" data-status="{esc(st)}" data-changed="{'yes' if r['id'] in changed_ids else 'no'}" data-scope="{'pass' if r.get('scope_ok') is True else 'fail' if r.get('scope_ok') is False else 'unchecked'}" data-review="{'yes' if r['id'] in {x['id'] for x in review} else 'no'}">
        <div class="case-head"><b>{esc(r['id'])}</b><span class="badge {esc(st)}">{esc(STATUS_LABELS.get(st,st))} · {esc(st)}</span><span class="muted">{esc(GROUP_LABELS.get(grp,grp))} · {float(r.get('elapsed_seconds') or 0):.2f}s · ${float(r.get('cost_usd') or 0):.6f}</span><span class="flag">{esc(' · '.join(flags))}</span></div>
        <h3>{esc(r.get('question',''))}</h3><div class="answer">{format_answer(cur.get('answer'))}</div>{attempts}
        <details><summary>intent, KB หลักฐาน, baseline และ usage</summary>
        <p><b>Intent:</b> {esc(cur.get('intent') or (cur.get('intent_analysis') or {}).get('intent') or '—')} · <b>รุ่นที่ระบุ:</b> {esc(', '.join(products) or cur.get('product_name') or '—')} · <b>Scope:</b> {'ผ่าน' if r.get('scope_ok') else 'ไม่ผ่าน' if r.get('scope_ok') is False else 'ไม่ได้ประเมิน'}<br><b>Reason:</b> {esc(cur.get('reason') or '—')}<br><b>แหล่งข้อมูล:</b> {esc('; '.join(refs) or 'ไม่มี source reference')}</p>
        <p><b>คำตอบ baseline ({esc(pr.get('status') or '—')}):</b></p><div class="answer prior">{format_answer(pr.get('answer'))}</div>
        <p><b>Provider usage ({len(r.get('usage',[]))} calls):</b></p><ul>{usage_html}</ul>
        <pre>{esc(json.dumps(cur.get('sources') or [],ensure_ascii=False,indent=2))}</pre></details></article>''')
    status_items=[(STATUS_LABELS.get(s,s),n,STATUS_COLORS.get(s,'#7d8785')) for s,n in status_counts.items()]
    cost_items=[(GROUP_LABELS.get(g,g),float(group_stats[g]['provider_cost_usd']),'#176e60') for g in group_counts]
    transition_items=[]
    for (b,c),n in transitions.items():
        label=f'{STATUS_LABELS.get(b,b)} → {STATUS_LABELS.get(c,c)}'
        color='#bd4452' if b=='answered' and c!='answered' else '#16836b' if b!='answered' and c=='answered' else '#88938f'
        transition_items.append((label,n,color))
    answer_table_count=sum('|' in str(r.get('reply',{}).get('answer') or '') and '---' in str(r.get('reply',{}).get('answer') or '') for r in rows)
    group_table=''.join(f'<tr><td>{esc(GROUP_LABELS.get(g,g))}</td><td>{d["count"]}</td><td>{d["status_counts"].get("answered",0)}</td><td>{sum(v for s,v in d["status_counts"].items() if s!="answered")}</td><td>{d["provider_calls"]}</td><td>${d["provider_cost_usd"]:.4f}</td><td>{d["median_elapsed_seconds"]}s</td></tr>' for g,d in group_stats.items())
    review_html=''.join(f'<li><b>{esc(x["id"])} · {esc(x["severity"])}</b> — {esc(x["note"])}</li>' for x in review)
    groups_options=''.join(f'<option value="{esc(g)}">{esc(GROUP_LABELS.get(g,g))}</option>' for g in group_counts)
    status_options=''.join(f'<option value="{esc(s)}">{esc(STATUS_LABELS.get(s,s))}</option>' for s in sorted(status_counts))
    pct=round(100*scope_ok/len(rows)) if rows else 0
    report=f'''<!doctype html><html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Black Shark · ผลทดสอบ 100 คำถาม · 2026-11-26</title>
<style>
:root{{--bg:#f3f5f2;--paper:#fff;--ink:#202b2a;--muted:#687471;--line:#dfe5e1;--green:#176e60;--red:#bd4452;--amber:#e7a338;--blue:#5475b8}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 system-ui,-apple-system,sans-serif}}main{{max-width:1320px;margin:auto;padding:26px 20px 70px}}h1{{font-size:clamp(2rem,4vw,3.2rem);line-height:1.12;margin:.15em 0}}h2{{font-size:1.35rem;margin:.1em 0 .65em}}.eyebrow{{font-size:.78rem;letter-spacing:.1em;color:var(--green);font-weight:800;text-transform:uppercase}}.muted,.sub{{color:var(--muted)}}.panel,.case,.stat{{background:var(--paper);border:1px solid var(--line);border-radius:15px;padding:17px;margin:12px 0}}.scope{{display:grid;grid-template-columns:1fr 1fr;gap:5px 20px}}.stats{{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:10px;margin:20px 0}}.stat strong{{display:block;font-size:1.65rem;line-height:1.25}}.stat span{{color:var(--muted);font-size:.87rem}}.charts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,500px),1fr));gap:10px}}.chart{{background:#fff;border:1px solid var(--line);border-radius:13px;padding:14px;margin:0;overflow:hidden}}.chart figcaption{{font-weight:750;margin:0 0 7px}}.chart svg{{width:100%;height:auto;display:block}}.chart-label{{font:13px system-ui;fill:#35423f}}.chart-value{{font:700 13px system-ui;fill:#283330}}.inside-label{{font:700 12px system-ui;fill:#fff;text-anchor:middle}}.legend{{font:11px system-ui;fill:#596461}}table{{border-collapse:collapse;width:100%;font-size:.92rem}}th,td{{text-align:left;vertical-align:top;border-bottom:1px solid var(--line);padding:8px}}.table-wrap{{overflow:auto;margin:9px 0}}.answer table{{min-width:500px;background:#fbfcfb}}.answer th{{background:#eef3f0}}.controls{{display:flex;gap:8px;flex-wrap:wrap;margin:20px 0}}input,select{{font:inherit;padding:9px 11px;border:1px solid #cbd4cf;border-radius:9px;background:white}}input{{flex:1;min-width:240px}}.case{{margin:10px 0}}.case-head{{display:flex;align-items:center;gap:9px;flex-wrap:wrap}}.badge{{font-size:.79rem;font-weight:750;border-radius:22px;padding:3px 9px;background:#e6ebe8}}.badge.answered{{background:#d8f1e9;color:#12604f}}.badge.handoff_mock,.badge.handoff_required{{background:#fff0d6;color:#815100}}.badge.need_model{{background:#e1e9f7;color:#344f85}}.flag{{color:#a14049;font-weight:700}}h3{{font-size:1.08rem;margin:11px 0 7px}}.answer{{border-left:3px solid #d5e3dd;padding-left:12px;overflow-wrap:anywhere}}.answer-line{{min-height:1.1em}}.answer-gap{{height:.45em}}details{{border-top:1px solid var(--line);margin-top:13px;padding-top:10px}}summary{{cursor:pointer;color:var(--green);font-weight:750}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f5f3;border-radius:9px;padding:12px;max-height:320px;overflow:auto}}.note{{border-left:4px solid var(--amber);background:#fff8e8;padding:12px 14px;border-radius:7px}}.attempts{{border:1px dashed #d9b56d;background:#fff9eb;padding:8px 12px;margin:8px 0;border-radius:8px}}@media(max-width:700px){{main{{padding:18px 12px}}.scope{{grid-template-columns:1fr}}th,td{{padding:6px}}}}
</style></head><body><main>
<div class="eyebrow">Live provider · OpenRouter · 26 Nov 2026</div><h1>รายงานทดสอบ 100 คำถาม</h1><p class="sub">คำถามและคำตอบจาก engine code ใน local worktree · กรองรายข้อและเปิดดูหลักฐานได้</p>
<section class="panel scope"><div><b>ร้าน:</b> {esc(kb['scope']['shop_name'])}</div><div><b>แพลตฟอร์ม / Shop ID:</b> {esc(kb['scope']['platform'])} / {esc(kb['scope']['shop_id'])}</div><div><b>Provider / Model:</b> OpenRouter / google/gemini-3.5-flash-lite</div><div><b>โค้ดที่ทดสอบ:</b> branch {esc(summary['tested_code_branch'])} · commit {esc(summary['tested_code_commit'])}</div><div><b>ข้อมูล KB ใน scope:</b> {len(kb['knowledge']['products'])} สินค้า · {len(kb['knowledge']['faqs'])} FAQ</div><div><b>ไฟล์ KB:</b> {esc(', '.join(kb['scope'].get('files') or []))}</div><div><b>วิธีทดสอบ:</b> เรียก ProductChatEngine ในเครื่องโดยใช้ shop-scoped Supabase KB และ OpenRouter จริง; ไม่ใช่ browser/Vercel E2E</div><div><b>ชุดอ้างอิง:</b> baseline 24 พ.ย. 2026 · provider/model เดียวกัน</div></section>
<section class="stats"><div class="stat"><strong>{len(rows)}/100</strong><span>คำถามเสร็จ</span></div><div class="stat"><strong>{status_counts.get('answered',0)}</strong><span>ตอบแล้ว</span></div><div class="stat"><strong>{status_counts.get('handoff_mock',0)+status_counts.get('handoff_required',0)}</strong><span>handoff</span></div><div class="stat"><strong>{status_counts.get('need_model',0)}</strong><span>ถามหารุ่น</span></div><div class="stat"><strong>{scope_ok}/{scope_checked}</strong><span>ผ่านตรวจ scope จากชื่อรุ่นและ source file</span></div><div class="stat"><strong>${response_cost:.4f}</strong><span>provider cost ของผล 100 ข้อ รวม retry Q060</span></div><div class="stat"><strong>${aborted_cost:.4f}</strong><span>provider cost รอบที่หยุดก่อนเริ่มใหม่</span></div><div class="stat"><strong>${response_cost+aborted_cost+judge_cost:.4f}</strong><span>รวมค่าใช้จ่ายทั้งหมดที่ทราบ</span></div><div class="stat"><strong>{hub_accepted}/{total_calls}</strong><span>AI Usage Hub รับ event รวมทุก attempt</span></div><div class="stat"><strong>{med}s / {p95}s</strong><span>median / p95 เวลาตอบ</span></div></section>
<div class="note"><b>การอ่านผล:</b> “ตอบแล้ว” ไม่ได้แปลว่าคำตอบถูกทุกข้อ การส่งข้อมูลไปให้โมเดลภายนอกตัวที่สองเพื่อให้คะแนน correctness ถูกปฏิเสธโดย automatic review เพราะจะส่ง KB ร้านและคำตอบ 100 ข้อออกไปเพิ่มเติม จึงไม่มีคะแนน semantic accuracy ในรายงานนี้ มี 1 provider usage event ที่ Hub ไม่ตอบรับ; event และ cost ยังอยู่ในไฟล์ local log ค่าใช้จ่ายรวมด้านบนรวม event นี้แล้ว</div>
<section class="charts" style="margin-top:12px">{horizontal_chart('สถานะคำตอบรวม',status_items,' ข้อ')}{stacked_group_chart(rows)}{horizontal_chart('ค่า provider แยกตามกลุ่ม',cost_items,' USD')}{horizontal_chart('การเปลี่ยน status เทียบ baseline',transition_items,' ข้อ')}</section>
<section class="panel"><h2>เทียบกับ baseline</h2><p>สถานะ baseline: {esc(json.dumps(baseline_summary.get('result_status_counts',{}),ensure_ascii=False))} · รอบนี้: {esc(json.dumps(dict(status_counts),ensure_ascii=False))}</p><p>คำตอบเปลี่ยนจาก baseline {answer_changed}/100 ข้อ · status ถอยจาก answered เป็นสถานะอื่น {len(regressions)} ข้อ ({esc(', '.join(regressions) or 'ไม่มี')}) · เปลี่ยนจาก need_model/handoff เป็น answered {len(improvements)} ข้อ ({esc(', '.join(improvements) or 'ไม่มี')})</p><p>เวลาตอบ median/p95: {esc(baseline_summary.get('median_elapsed_seconds'))}/{esc(baseline_summary.get('p95_elapsed_seconds'))}s → {med}/{p95}s. ค่า provider ของผลหลัก ${response_cost:.4f} เทียบ baseline ${float(baseline_summary.get('provider_cost_usd') or 0):.4f}; รอบนี้มี retry Q060 จึงไม่ใช่การเทียบ cost แบบคำถามต่อคำถามโดยตรง</p></section>
<section class="panel"><h2>ผลแยกกลุ่ม</h2><div style="overflow:auto"><table><thead><tr><th>กลุ่ม</th><th>จำนวน</th><th>ตอบแล้ว</th><th>สถานะอื่น</th><th>Provider calls</th><th>ค่าใช้จ่าย</th><th>median</th></tr></thead><tbody>{group_table}</tbody></table></div></section>
<section class="panel"><h2>Spot check และข้อสังเกต</h2><p>ตรวจด้วยการอ่านผลและเทียบตัวอย่างกับ snapshot KB ในเครื่อง ไม่ใช่ semantic judge ครบ 100 ข้อ</p><ul>{review_html}</ul></section>
<section class="panel"><h2>ค่าใช้จ่ายและ AI Usage Hub</h2><p>ชุดคำตอบหลัก: {summary['provider_calls_in_100_question_results']} provider calls · ${response_cost:.6f} · Hub รับ {summary['ai_usage_hub_accepted_in_100_question_results']} / {summary['provider_calls_in_100_question_results']}, ปฏิเสธ/ไม่ตอบรับ {summary['ai_usage_hub_rejected_in_100_question_results']}</p><p>รอบเริ่มต้นที่หยุด: {aborted.get('question_count',0)} rows, {aborted.get('provider_calls',0)} provider calls · ${aborted_cost:.6f} · Hub รับ {aborted.get('hub_accepted',0)}/{aborted.get('provider_calls',0)}</p><p>รวมใน Hub/local records: {total_calls} events · รับ {hub_accepted} · ไม่รับ {hub_rejected} · cost รวม ${response_cost+aborted_cost+judge_cost:.6f} · ไม่พบการยิง AI Usage Hub สำหรับ judge เพราะไม่ได้เริ่มเรียก judge</p><p>รายละเอียดแต่ละ event อยู่ใน <code>usage_events.json</code>; รอบที่หยุดอยู่ใน <code>aborted_attempts.json</code>.</p></section>
<div class="controls"><input id="search" placeholder="ค้นคำถาม คำตอบ รุ่น หรือ source"><select id="group"><option value="">ทุกกลุ่ม</option>{groups_options}</select><select id="status"><option value="">ทุก status</option>{status_options}</select><select id="changed"><option value="">เทียบ baseline ทั้งหมด</option><option value="yes">คำตอบเปลี่ยน</option><option value="no">คำตอบไม่เปลี่ยน</option></select><select id="scope"><option value="">scope ทั้งหมด</option><option value="pass">scope ผ่าน</option><option value="fail">scope ไม่ผ่าน</option><option value="unchecked">ไม่ได้ประเมิน</option></select><select id="review"><option value="">spot check ทั้งหมด</option><option value="yes">เฉพาะ spot check</option></select></div><p id="shown" class="muted"></p>{''.join(cards)}</main>
<script>const ids=['search','group','status','changed','scope','review'];function applyFilter(){{const q=document.querySelector('#search').value.toLowerCase();let visible=0;document.querySelectorAll('.case').forEach(c=>{{let ok=!q||c.innerText.toLowerCase().includes(q);for(const id of ids.slice(1)){{const v=document.getElementById(id).value;if(v&&c.dataset[id]!==v)ok=false}}c.hidden=!ok;if(ok)visible++}});document.querySelector('#shown').textContent=`แสดง ${{visible}} / {len(rows)} ข้อ`}}ids.forEach(id=>document.getElementById(id).addEventListener(id==='search'?'input':'change',applyFilter));applyFilter();</script></body></html>'''
    (OUT/'report.html').write_text(report)
    print(json.dumps({'report':str(OUT/'report.html'),'results':len(rows),'status_counts':dict(status_counts),'response_cost_usd':response_cost,'aborted_cost_usd':aborted_cost,'total_cost_usd':round(response_cost+aborted_cost+judge_cost,9),'usage_events':total_calls,'hub_accepted':hub_accepted,'hub_rejected':hub_rejected,'answers_changed':answer_changed,'scope_ok':scope_ok},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
