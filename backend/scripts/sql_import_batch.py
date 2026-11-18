"""Print one idempotent Supabase SQL import batch from the checked source artifact."""
import json
import sys
from pathlib import Path

data = json.loads((Path(__file__).resolve().parents[1] / "data" / "knowledge_base.json").read_text(encoding="utf-8"))
kind, start, count = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
items = data["products" if kind == "products" else "faqs"]
rows = []
for index in range(start, min(start + count, len(items))):
    item = items[index]
    if kind == "products":
        rows.append({"source_index": index, "name": item["name"], "description": item["description"], "source": item["source"]})
    else:
        rows.append({"source_index": index, "model_name": item["model"], "question": item["question"], "answer": item["answer"], "source": item["source"]})
payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).replace("'", "''")
if kind == "products":
    print("insert into public.bot_products(source_index,name,description,source) select source_index,name,description,source from jsonb_to_recordset('" + payload + "'::jsonb) as x(source_index int,name text,description text,source jsonb) on conflict(source_index) do update set name=excluded.name,description=excluded.description,source=excluded.source;")
else:
    print("insert into public.bot_faqs(source_index,model_name,question,answer,source) select source_index,model_name,question,answer,source from jsonb_to_recordset('" + payload + "'::jsonb) as x(source_index int,model_name text,question text,answer text,source jsonb) on conflict(source_index) do update set model_name=excluded.model_name,question=excluded.question,answer=excluded.answer,source=excluded.source;")
