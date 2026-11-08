# -*- coding: utf-8 -*-
"""把 *.jsonl 转成 Excel 可直接打开的表格（优先 .xlsx，否则 .csv）。

用法： python jsonl2excel.py
每个 xxx.jsonl 会在同目录生成 xxx.xlsx（若装了 openpyxl/xlsxwriter）或 xxx.csv（UTF-8 BOM，Excel 双击可开）。
"""
import csv
import glob
import json
import os

LIST_KEYS = ("knowledge", "images")

# 记录里各来源字段不完全一致，统一表头
COLUMNS = ["id", "subject", "grade", "qtype", "difficulty", "used_count",
           "stem", "A", "B", "C", "D", "answer", "analysis",
           "knowledge", "images", "update", "date",
           "detail_url", "paper_url", "paper_title", "list_url"]


def flatten(rec):
    row = {}
    for k in COLUMNS:
        row[k] = rec.get(k, "")
    opts = rec.get("options") or {}
    for x in ("A", "B", "C", "D"):
        row[x] = opts.get(x, "")
    for k in LIST_KEYS:
        v = rec.get(k) or []
        row[k] = "；".join(map(str, v)) if isinstance(v, list) else v
    return row


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)


def write_xlsx(path, rows):
    try:
        from openpyxl import Workbook
    except Exception:
        return False
    wb = Workbook()
    ws = wb.active
    ws.title = "data"
    ws.append(COLUMNS)
    for r in rows:
        ws.append([r.get(c, "") for c in COLUMNS])
    wb.save(path)
    return True


def main():
    for jf in glob.glob("*.jsonl"):
        recs = []
        with open(jf, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        recs.append(json.loads(line))
                    except Exception:
                        pass
        if not recs:
            continue
        rows = [flatten(r) for r in recs]
        base = os.path.splitext(jf)[0]
        if write_xlsx(base + ".xlsx", rows):
            print(f"{jf} -> {base}.xlsx ({len(rows)} 行)")
        else:
            write_csv(base + ".csv", rows)
            print(f"{jf} -> {base}.csv ({len(rows)} 行)")


if __name__ == "__main__":
    main()
