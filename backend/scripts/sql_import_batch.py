"""Print one idempotent Supabase SQL import batch from the checked source artifact."""
import argparse
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.app.product_categories import category_from_description

default_data = Path(__file__).resolve().parents[1] / "data" / "knowledge_base.json"
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("kind", choices=("products", "faqs"))
parser.add_argument("start", type=int, help="zero-based item position in the input JSON")
parser.add_argument("count", type=int, help="maximum number of items in this batch")
parser.add_argument("--data", type=Path, default=default_data, help="knowledge-base JSON file")
parser.add_argument("--offset", type=int, default=0, help="offset for the database source_index")
args = parser.parse_args()
data = json.loads(args.data.read_text(encoding="utf-8"))
kind, start, count, offset = args.kind, args.start, args.count, args.offset
items = data["products" if kind == "products" else "faqs"]
rows = []
for index in range(start, min(start + count, len(items))):
    item = items[index]
    source_index = offset + index
    if kind == "products":
        rows.append({"source_index": source_index, "name": item["name"], "description": item["description"],
                     "source": item["source"],
                     "category": item.get("category") or category_from_description(item["description"])})
    else:
        rows.append({"source_index": source_index, "model_name": item["model"], "question": item["question"], "answer": item["answer"], "source": item["source"]})
payload = base64.b64encode(
    json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
).decode("ascii")
if kind == "products":
    print("insert into public.bot_products(source_index,name,description,source,category) select source_index,name,description,source,category from jsonb_to_recordset(convert_from(decode('" + payload + "','base64'),'UTF8')::jsonb) as x(source_index int,name text,description text,source jsonb,category text) on conflict(source_index) do update set name=excluded.name,description=excluded.description,source=excluded.source,category=coalesce(excluded.category,bot_products.category);")
else:
    print("insert into public.bot_faqs(source_index,model_name,question,answer,source) select source_index,model_name,question,answer,source from jsonb_to_recordset(convert_from(decode('" + payload + "','base64'),'UTF8')::jsonb) as x(source_index int,model_name text,question text,answer text,source jsonb) on conflict(source_index) do update set model_name=excluded.model_name,question=excluded.question,answer=excluded.answer,source=excluded.source;")
