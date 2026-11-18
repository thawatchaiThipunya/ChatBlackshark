from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from openpyxl import load_workbook


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = PROJECT_ROOT.parent
DEFAULT_CATALOG = WORKSPACE_ROOT / "สินค้า - Black Shark Thailand (sellcenter).xlsx"
DEFAULT_FAQ = WORKSPACE_ROOT / "วิธีแก้ปัญหารายรุ่น-Black Shark-เฉพาะติ๊กส่งลูกค้าได้-20261118-0114.xlsx"
DEFAULT_OUTPUT = PROJECT_ROOT / "backend" / "data" / "knowledge_base.json"

CONTINUATION = re.compile(r"\s*\(ต่อ\s*\d+\)\s*$")
QA_BLOCK = re.compile(r"(?ms)^Q:\s*(.*?)\s*\r?\nA:\s*(.*?)(?=^Q:\s*|\Z)")


def text_value(value: Any) -> str:
    return "" if value is None else str(value).strip()


def source_ref(filename: str, sheet: str, column: str, row: int) -> dict[str, str | int]:
    return {
        "file": filename,
        "sheet": sheet,
        "cell": f"{column}{row}",
        "row": row,
    }


def read_products(path: Path) -> list[dict[str, Any]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook["ข้อมูลสินค้า"]
    products: list[dict[str, Any]] = []
    for row in range(2, sheet.max_row + 1):
        name = text_value(sheet.cell(row, 1).value)
        description = text_value(sheet.cell(row, 2).value)
        if not name:
            continue
        products.append(
            {
                "name": name,
                "description": description,
                "source": source_ref(path.name, sheet.title, "B", row),
            }
        )
    workbook.close()
    return products


def read_faqs(path: Path) -> list[dict[str, Any]]:
    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook["วิธีแก้ปัญหารายรุ่น"]
    faqs: list[dict[str, Any]] = []
    malformed: list[str] = []

    for row in range(3, sheet.max_row + 1):
        model = text_value(sheet.cell(row, 1).value)
        body = text_value(sheet.cell(row, 3).value)
        if not model or not body:
            continue

        model = CONTINUATION.sub("", model).strip()
        blocks = list(QA_BLOCK.finditer(body))
        if not blocks:
            malformed.append(f"{sheet.title}!C{row}")
            continue

        for block in blocks:
            question = block.group(1).strip()
            answer = block.group(2).strip()
            if not question or not answer:
                malformed.append(f"{sheet.title}!C{row}")
                continue
            faqs.append(
                {
                    "model": model,
                    "question": question,
                    "answer": answer,
                    "source": source_ref(path.name, sheet.title, "C", row),
                }
            )

    workbook.close()
    if malformed:
        raise ValueError("FAQ blocks could not be parsed: " + ", ".join(malformed))
    return faqs


def read_support_policy(path: Path) -> str:
    workbook = load_workbook(path, read_only=True, data_only=True)
    policy = text_value(workbook["วิธีแก้ปัญหารายรุ่น"].cell(2, 3).value)
    workbook.close()
    return policy


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract only the two supplied Black Shark workbooks into the backend knowledge base."
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--faq", type=Path, default=DEFAULT_FAQ)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    missing = [str(path) for path in (args.catalog, args.faq) if not path.is_file()]
    if missing:
        raise FileNotFoundError("Source workbook not found: " + ", ".join(missing))

    products = read_products(args.catalog)
    faqs = read_faqs(args.faq)
    support_models = list(dict.fromkeys(item["model"] for item in faqs))
    payload = {
        "schema_version": 1,
        "source_files": [args.catalog.name, args.faq.name],
        "products": products,
        "support_models": support_models,
        "faqs": faqs,
        "support_policy": read_support_policy(args.faq),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(args.output),
                "products": len(products),
                "support_models": len(support_models),
                "faq_entries": len(faqs),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
