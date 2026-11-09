# 教育大模型评测题库

本项目保存初中历史单选题的原始导出和清洗后的题库。

## 文件清单

| 文件 | 用途 |
| --- | --- |
| `README.md` | 记录项目文件的用途和题库格式。 |
| [`.gitignore`](.gitignore) | 忽略本地环境变量文件，并为 `.env.example` 保留例外。 |
| [`data/raw/history_21_answered.csv`](data/raw/history_21_answered.csv) | 1,402 条原始题目记录的表格导出。A、B、C、D 选项各占一列，便于查看和筛选。 |
| [`data/raw/history_21_answered.jsonl`](data/raw/history_21_answered.jsonl) | 同一批 1,402 条原始记录的结构化导出，保留题干、选项、参考答案、图片链接和来源信息。 |
| [`data/processed/questions.jsonl`](data/processed/questions.jsonl) | 去重后的完整题库，共 1,388 道，用于查看全部清洗结果。 |
| [`data/processed/usable_text.jsonl`](data/processed/usable_text.jsonl) | 通过自动检查的 1,140 道纯文本题，用于纯文本评测。 |
| [`data/processed/image_questions.jsonl`](data/processed/image_questions.jsonl) | 带图片链接且通过其余自动检查的 192 道题，用于图文评测。 |
| [`data/processed/review_questions.jsonl`](data/processed/review_questions.jsonl) | 自动检查发现疑点的 56 道题，附逐题复核原因，供人工核对。 |

## 题库格式

JSONL 文件采用 UTF-8 编码，每行保存一道题。清洗后的题目用 `stem` 保存题干，`options` 保存选项，`answer` 保存参考答案，`images` 保存图片链接。`source_records` 记录原始题目 ID 和行号，便于回查。待复核原因记录在 `quality_flags` 中。

清洗时先归类待复核题，再按是否带图片链接区分其余题目。三份分类文件互不重叠，合起来正好是 `questions.jsonl` 中的 1,388 道题。
