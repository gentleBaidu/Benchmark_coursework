"""初中政治（道德与法治）选择题爬虫 - 二一组卷网(21cnjy) + Playwright。

流程：
1. 复用 VIP 登录态 data/_auth21_vip.json（答案需会员）。
2. 列表页（xd=2 初中, chid=9 道德与法治）点击"选择题"过滤，仅抓选择题。
3. 逐个请求详情页 HTML，解析内嵌 JSON：题干、选项、答案(answer_text)。
   —— 答案直接取自 JSON，无需 OCR。
4. 限速、断点续抓，自动补抓直到达到目标数量，导出 xlsx。

仅抓选择题，目标 target_count 道。
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

try:
    from lxml import html as lxml_html
except Exception:
    lxml_html = None

from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
STATE_PATH = DATA_DIR / "_auth21_vip.json"   # VIP 登录态（优先）
QID_FILE = DATA_DIR / "_qids.json"          # 已收集的题目ID（断点）
RESULT_FILE = DATA_DIR / "_result.jsonl"    # 已抓取的题目结果（断点）
DEFAULT_OUTPUT = DATA_DIR / "初中政治_道德与法治_选择题1200题.xlsx"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

LIST_URL = "https://zujuan.21cnjy.com/question?xd=2&chid=9&question_type_ids=1&is_explain=1"


@dataclass
class Question:
    subject: str = "初中道德与法治"
    question_id: str = ""
    source_url: str = ""
    question_text: str = ""
    options: str = ""
    answer: str = ""
    analysis: str = ""
    status: str = "ok"
    fetched_at: str = ""


def println(*a):
    print(*a, flush=True)


def normalize_text(value: str) -> str:
    value = re.sub(r"\s+", " ", (value or "")).strip()
    return value


class PageContext:
    """Playwright 封装：列表分页（选择题过滤）+ 详情解析。"""

    def __init__(self, state_path: Path, min_delay: float, max_delay: float):
        self.state_path = state_path
        self.min_delay = min_delay
        self.max_delay = max_delay

    def _sleep(self):
        time.sleep(random.uniform(self.min_delay, self.max_delay))

    # ---------------- 列表 ----------------
    def collect_qids(self, page, need: int) -> list[str]:
        """在已‘选择题’过滤的列表上分页收集题目ID，直到 need 个或分页耗尽。"""
        qids: list[str] = []
        seen: set[str] = set()
        page_no = 1
        while len(qids) < need and page_no <= 3900:
            # 解析本页所有选择题 ID + 题干避免重复
            items = page.evaluate(
                """
                () => {
                var out=[];
                document.querySelectorAll('li.QuestionView').forEach(function(v){
                  if(v.closest('#J_ConnectQuestionsdBox')) return;
                  if(v.parentElement && v.parentElement.tagName==='OL') return;
                  var tpEl=v.querySelector('.question-block > .q-attribute .q-attr span, .q-attribute .q-attr span');
                  var tp=tpEl?tpEl.innerText.trim():'';
                  var hasOl=v.querySelector('.q-bd-list')!==null;
                  var opts=v.querySelectorAll('.question-block > .q-mc .exam-s .op-item').length;
                  var tit=v.querySelector('.question-block > .q-mc > .q-tit, .q-tit');
                  if(/(单选|多选|选择)题/.test(tp) && !hasOl && opts>=2 && tit){
                    var id=tit.getAttribute('data-qid');
                    if(id) out.push(id);
                  }
                });
                return [...new Set(out)];
                }
                """
            )
            new_cnt = 0
            for q in items:
                if q not in seen:
                    seen.add(q)
                    qids.append(q)
                    new_cnt += 1
            println(f"  列表页 {page_no}: 新增 {new_cnt}，累计 {len(qids)}")
            if len(qids) >= need:
                break
            self._sleep()
            # 点击下一页
            nxt = page.locator("a[data-page]:has-text('下一页'), a[data-page]")
            next_link = None
            for i in range(nxt.count()):
                txt = (nxt.nth(i).inner_text() or "").strip()
                if txt == "下一页":
                    next_link = nxt.nth(i)
                    break
            if next_link is None:
                page_no += 1
                break
            # 判断是否为最后一页（下一页不可点）
            href = next_link.get_attribute("href") or ""
            if not href:
                break
            next_link.click()
            page.wait_for_timeout(random.randint(1500, 3000))
            page_no += 1
        return qids[:need]

    # ---------------- 详情 ----------------
    def fetch_detail(self, page, qid: str) -> Question:
        url = f"https://zujuan.21cnjy.com/question/detail/{qid}"
        q = Question(source_url=url, question_id=qid)
        # 详情页把题目数据以内嵌 JSON 形式输出（含答案字母 answer_text）。
        # 直接请求 HTML 解析，无需渲染、无需 OCR 取答案，快速且轻量。
        self._sleep()
        try:
            resp = page.request.get(url, headers={"Referer": "https://zujuan.21cnjy.com/"})
        except Exception as e:
            # 仅记录异常类型，避免把请求头/Cookie 等内容写入日志
            q.status = f"http_error:{type(e).__name__}"
            return q
        if resp.status != 200:
            q.status = f"http_{resp.status}"
            return q
        html = resp.text()
        if "已达上限" in html or "过于频繁" in html or "太频繁" in html:
            q.status = "rate_limited"
            return q

        data = extract_question_json(html)
        if not data:
            q.status = "no_question_json"
            return q

        q.question_text = html_to_text(data.get("title") or data.get("question_text") or "")
        qtype = str(data.get("channel_type_name") or data.get("question_type") or "")
        options = data.get("options") or {}
        opts = []
        if isinstance(options, dict):
            opts = [html_to_text(str(options[k])) for k in sorted(options.keys())]
        else:
            opts = [o for o in options]
        opts = [o for o in opts if o]
        q.options = "\n".join(f"{chr(65+i)}. {o}" for i, o in enumerate(opts))

        # 只接受选择题
        if qtype and not any(k in qtype for k in ("单选题", "多选题", "选择题")):
            q.status = "not_choice"
            return q
        if not is_choice(opts):
            q.status = "not_choice"
            return q

        ans = str(data.get("answer_text") or "").strip().upper()
        if ans and ans[0] in "ABCD":
            q.answer = ans[0]
        if not q.answer:
            # 站点对该题未提供答案数据（answer_text/answer/explanation 均为空）→ 终态，不再重试
            q.status = "no_answer_data"
        q.fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return q


def extract_question_json(html: str):
    """从详情页 HTML 中提取主问题的内嵌 JSON（question:{...}）。"""
    mk = html.find("QuestionDetailView")
    k = html.find('{"question_id"', mk if mk >= 0 else 0)
    if k < 0:
        k = html.find('{"question_id"')
    if k < 0:
        return None
    depth = 0
    end = -1
    for idx in range(k, len(html)):
        c = html[idx]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = idx + 1
                break
    if end < 0:
        return None
    try:
        return json.loads(html[k:end])
    except Exception:
        return None


def html_to_text(fragment: str) -> str:
    if not fragment:
        return ""
    try:
        if lxml_html is not None:
            el = lxml_html.fromstring(f"<div>{fragment}</div>")
            return normalize_text(el.text_content())
    except Exception:
        pass
    return normalize_text(re.sub(r"<[^>]+>", " ", fragment))


def is_choice(opts) -> bool:
    return isinstance(opts, list) and 2 <= len(opts) <= 8


def load_ids() -> list[str]:
    if QID_FILE.exists():
        try:
            return json.loads(QID_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def load_results() -> dict[str, Question]:
    out: dict[str, Question] = {}
    if RESULT_FILE.exists():
        for line in RESULT_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                out[d["question_id"]] = Question(**d)
            except Exception:
                continue
    return out


def save_results(results: dict[str, Question]):
    with RESULT_FILE.open("w", encoding="utf-8") as f:
        for q in results.values():
            f.write(json.dumps(asdict(q), ensure_ascii=False) + "\n")


def export_questions(questions: list[Question], output: Path):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    good = [q for q in questions if q.status == "ok" and q.answer]
    output.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "选择题"
    headers = ["题号", "题目", "选项", "正确答案", "来源"]
    ws.append(headers)
    for i, q in enumerate(good, 1):
        ws.append([
            i,
            q.question_text,
            q.options,
            q.answer,
            q.source_url,
        ])
    fill = PatternFill("solid", fgColor="1F4E78")
    font = Font(color="FFFFFF", bold=True)
    for cell in ws[1]:
        cell.fill = fill
        cell.font = font
        cell.alignment = Alignment(horizontal="center", vertical="center")
    widths = {"A": 8, "B": 70, "C": 50, "D": 12, "E": 40}
    for col, width in widths.items():
        ws.column_dimensions[col].width = width
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(ws.max_column)}{ws.max_row}"
    wb.save(output)
    println(f"已导出 {len(good)} 道选择题 -> {output}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-count", type=int, default=1200, help="目标有效（含答案）选择题数量")
    ap.add_argument("--collect-count", type=int, default=0,
                    help="本次向列表收集的题目ID总数（默认 target*2.2，用于抵消无答案题占比）")
    ap.add_argument("--min-delay", type=float, default=0.6)
    ap.add_argument("--max-delay", type=float, default=1.8)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--collect-only", action="store_true")
    args = ap.parse_args()

    target = args.target_count
    collect_count = args.collect_count or int(target * 2.2)

    # 断点：已有结果 / 已有ID
    results = load_results()
    qids = load_ids()
    good_now = sum(1 for q in results.values() if q.status == "ok" and q.answer)
    println(f"断点：已抓 {len(results)}（有效 {good_now}），已收集ID {len(qids)}")

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        ctx = browser.new_context(
            storage_state=str(STATE_PATH),
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 900},
        )
        ctx.set_default_timeout(60000)
        page = ctx.new_page()
        sc = PageContext(STATE_PATH, args.min_delay, args.max_delay)

        # 补足题目ID（列表 URL 已带"含解析(is_explain=1)+选择题"筛选，均为有答案题）
        if len(qids) < collect_count:
            page.goto(LIST_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4000)
            println(f"已进入列表（含解析筛选），落点：{page.url[:90]}")
            new_ids = sc.collect_qids(page, collect_count)
            qids = qids + [q for q in new_ids if q not in qids]
            QID_FILE.write_text(json.dumps(qids), encoding="utf-8")
            println(f"共收集 {len(qids)} 个题目ID")

        # 抓取详情，直到有效题数达到目标（no_answer_data 视为可重试：会话失效时会误判）
        terminal = ("ok", "not_choice")
        todo = [qid for qid in qids
                if qid not in results or results[qid].status not in terminal]
        println(f"待抓取 {len(todo)} 道")
        if not args.collect_only:
            good_count = good_now
            saved_at = 0
            for idx, qid in enumerate(todo, 1):
                if good_count >= target:
                    println(f"已达到目标 {target} 道有效选择题，停止抓取")
                    break
                try:
                    q = sc.fetch_detail(page, qid)
                except Exception as e:
                    q = Question(source_url=f"https://zujuan.21cnjy.com/question/detail/{qid}",
                                 question_id=qid, status=f"error:{type(e).__name__}")
                results[qid] = q
                if q.status == "ok" and q.answer:
                    good_count += 1
                    println(f"[{idx}/{len(todo)}] 有效{good_count} {qid} 答案 {q.answer} | {q.question_text[:32]}")
                elif q.status != "no_answer_data":
                    println(f"[{idx}/{len(todo)}] {qid} 状态 {q.status}")
                if idx - saved_at >= 10:
                    save_results(results)
                    saved_at = idx
                if q.status == "rate_limited":
                    println("命中站点查看频率限制，本轮暂停（稍后或次日重试）")
                    break
        browser.close()

    save_results(results)
    good = [q for q in results.values() if q.status == "ok" and q.answer]
    println(f"OK题数：{len(good)} / 已处理 {len(results)}")
    export_questions(good[:target], args.output)


if __name__ == "__main__":
    sys.exit(main())