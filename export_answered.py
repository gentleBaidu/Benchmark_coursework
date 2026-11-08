# -*- coding: utf-8 -*-
"""把 history_21.jsonl 中「已有答案」的题目导出为 CSV 表格。

用法：
  python export_answered.py                     # 默认 history_21.jsonl -> history_21_answered.csv
  python export_answered.py 输入.jsonl 输出.csv   # 也可自定义
说明：只导出 answer 非空的题；UTF-8-BOM 编码，Excel/WPS 双击可直接打开。
"""
import csv
import json
import sys

IN = sys.argv[1] if len(sys.argv) > 1 else "history_21.jsonl"
OUT = sys.argv[2] if len(sys.argv) > 2 else "history_21_answered.csv"

COLUMNS = ["id", "subject", "grade", "qtype", "difficulty", "used_count",
           "stem", "A", "B", "C", "D", "answer", "analysis",
           "knowledge", "images", "update", "date",
           "detail_url", "paper_url", "paper_title", "list_url"]


def flatten(rec):
    row = {k: rec.get(k, "") for k in COLUMNS}
    opts = rec.get("options") or {}
    for x in "ABCD":
        row[x] = opts.get(x, "")
    for k in ("knowledge", "images"):
        v = rec.get(k) or []
        row[k] = "；".join(map(str, v)) if isinstance(v, list) else v
    return row


def main():
    rows = []
    with open(IN, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r.get("answer"):          # 只保留已经有答案的题
                rows.append(flatten(r))
    with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"{IN} 中有答案的题目 {len(rows)} 条 -> {OUT}")


if __name__ == "__main__":
    main()
