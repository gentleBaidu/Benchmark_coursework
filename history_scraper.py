# -*- coding: utf-8 -*-
"""
初中历史题目采集器（组卷网 zujuan.xkw.com）
用法：
  python history_scraper.py login            # 第一步：打开浏览器手动登录，保存登录态 state.json
  python history_scraper.py paper <url>      # 抓单份试卷，输出 history_data.jsonl（测试用）
  python history_scraper.py crawl <页数>     # 抓列表页前 N 页下的所有试卷
  python history_scraper.py answers [条数]   # 给 history_data.jsonl 里缺答案的题，逐题去详情页补答案解析
说明：题干/选项/知识点/难度/题型无需登录即可抓；【答案】需登录后才可见。
警告：网站有 WAF 限流，务必慢速运行，被"阻断页面"拦截时立即停止、等待冷却。
"""
import json
import re
import sys
import time
import os
import random

from playwright.sync_api import sync_playwright

STATE = "state.json"
OUT = "history_data.jsonl"
LIST_URL = "https://zujuan.xkw.com/czls/shijuan/jdcs/"
LOG = open("_scraper_log.txt", "a", encoding="utf-8")


def log(*a):
    msg = " ".join(str(x) for x in a)
    print(msg)              # 同时打印到终端，实时看进度
    print(msg, file=LOG)
    LOG.flush()


UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0")

# 在页面里执行：抽取当前页所有题目
EXTRACT_JS = r"""
() => {
  const out = [];
  document.querySelectorAll('div.tk-quest-item.quesroot').forEach(q => {
    const qid = q.getAttribute('questionid');
    const cnt = q.querySelector('.exam-item__cnt');
    let stemText = '', imgs = [];
    const opts = [];
    if (cnt) {
      // 选项在题干容器内的 optionsTable 里，先取选项，再克隆去掉它得到纯题干
      cnt.querySelectorAll('table[name=optionsTable] td').forEach(td => opts.push(td.innerText.trim()));
      const clone = cnt.cloneNode(true);
      clone.querySelectorAll('table[name=optionsTable]').forEach(t => t.remove());
      stemText = clone.innerText.trim();
      imgs = [...cnt.querySelectorAll('img')].map(im => im.getAttribute('src') || '');
    }
    const infos = [...q.querySelectorAll('.msg-box .info-cnt')].map(e => e.innerText.trim());
    const kn = [...q.querySelectorAll('.knowledge-name')].map(e => e.getAttribute('cname'));
    const ansEl = q.querySelector('.item.answer');
    const ans = ansEl ? ansEl.innerText.trim() : '';
    const anaEl = q.querySelector('.item.analysis, .exam-analyze__cnt');
    const ana = anaEl ? anaEl.innerText.trim() : '';
    const dateEl = q.querySelector('.exam-item__info .no-bound');
    out.push({
      qid: qid, stemText: stemText, opts: opts, infos: infos,
      kn: kn, ans: ans, ana: ana, imgs: imgs,
      date: dateEl ? dateEl.innerText.trim() : ''
    });
  });
  return out;
}
"""

PAPER_META_JS = r"""
() => {
  const q = s => document.querySelector(s);
  const t = q('.exam-title') || q('h2.jingpin');
  let title = t ? t.innerText.trim() : document.title.trim();
  title = title.split('\n')[0].trim();
  return { title: title };
}
"""

# 单题详情页：抓【答案】和【解析】
ANS_JS = r"""
() => {
  const bodyText = document.body ? document.body.innerText : '';
  const pick = (labels) => {
    for (const lab of labels) {
      const i = bodyText.indexOf(lab);
      if (i < 0) continue;
      let s = bodyText.slice(i + lab.length);
      const m = s.search(/【[^】]+】/);
      if (m > 0) s = s.slice(0, m);
      s = s.replace(/\s+/g, ' ').trim();
      if (s) return s;
    }
    return '';
  };
  let answer = pick(['【答案】', '答案：', '答案:']);
  let analysis = pick(['【解析】', '解析：', '解析:']);
  const pre = bodyText.includes('不能查看答案');
  return { answer: answer, analysis: analysis, need_login_or_vip: pre };
}
"""

OPT_RE = re.compile(r"^([A-Da-d])[．.、]\s*(.+)$")


def clean_stem(text):
    """清理题干文本：去题号、统一空格、规范化括号。"""
    t = text.replace("&nbsp;", " ").replace("\xa0", " ").replace("\u3000", " ")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\s*\n\s*", " ", t)
    t = t.strip()
    t = re.sub(r"^\d+[．.、]\s*", "", t)  # 去掉题号
    t = re.sub(r"[（(]\s*[)）]", "( )", t)
    return t


def infer_grade(title):
    for g in ("七年级", "八年级", "九年级"):
        if g in title:
            return g
    if "中考" in title:
        return "九年级"
    return ""


def parse_options(opts):
    d = {}
    for o in opts:
        m = OPT_RE.match(o.strip())
        if m:
            d[m.group(1).upper()] = m.group(2).strip()
    return d


def to_record(raw, paper_url, paper_meta):
    opts = parse_options(raw["opts"])
    infos = raw["infos"]
    qtype = infos[0] if infos else ""
    diff = ""
    for x in infos:
        if "(" in x:
            diff = x
    return {
        "id": raw["qid"],
        "subject": "历史",
        "grade": paper_meta.get("grade", ""),
        "stem": clean_stem(raw["stemText"]),
        "options": opts,
        "answer": raw["ans"],
        "analysis": raw["ana"],
        "qtype": qtype,
        "difficulty": diff,
        "knowledge": raw["kn"],
        "images": raw["imgs"],
        "date": raw["date"],
        "paper_title": paper_meta.get("title", ""),
        "paper_url": paper_url,
    }


def is_blocked(page):
    """检测是否被 WAF 阻断。"""
    try:
        t = page.title()
        if "阻断" in t or "Blocked" in t:
            return True
        body = page.inner_text("body")[:2000]
        return "被阻断" in body or "访问被阻断" in body
    except Exception:
        return False


def load_done_ids():
    ids = set()
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            for line in f:
                try:
                    ids.add(json.loads(line)["id"])
                except Exception:
                    pass
    return ids


def scrape_paper(page, url, extra_meta=None):
    page.goto(url, wait_until="domcontentloaded", timeout=40000)
    page.wait_for_timeout(2500)
    if is_blocked(page):
        raise RuntimeError("BLOCKED")
    # 懒加载：滚到底部触发
    for _ in range(6):
        page.mouse.wheel(0, 6000)
        page.wait_for_timeout(500)
    meta = page.evaluate(PAPER_META_JS)
    meta["grade"] = infer_grade(meta.get("title", ""))
    if extra_meta:
        meta.update(extra_meta)
    raw = page.evaluate(EXTRACT_JS)
    recs = [to_record(r, url, meta) for r in raw]
    return [r for r in recs if r["stem"]]


def do_login():
    with sync_playwright() as pw:
        b = pw.chromium.launch(headless=False, channel="msedge")
        ctx = b.new_context()
        pg = ctx.new_page()
        pg.goto("https://zujuan.xkw.com/", wait_until="domcontentloaded")
        input(">>> 在浏览器里完成登录（扫码/账号），完成后回终端按回车保存登录态...")
        ctx.storage_state(path=STATE)
        b.close()
    log("login state saved ->", STATE)


def open_ctx(pw):
    b = pw.chromium.launch(headless=True, channel="msedge")
    kw = {"user_agent": UA}
    if os.path.exists(STATE):
        kw["storage_state"] = STATE
    ctx = b.new_context(**kw)
    return b, ctx


def save(recs):
    with open(OUT, "a", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def dump_all(recs):
    with open(OUT, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def do_paper(url):
    with sync_playwright() as pw:
        b, ctx = open_ctx(pw)
        page = ctx.new_page()
        recs = scrape_paper(page, url)
        save(recs)
        log(f"paper {url} -> {len(recs)} questions, 答案非空={sum(1 for r in recs if r['answer'])}")
        b.close()


def do_answers(limit=0, min_sleep=12, max_sleep=20):
    if not os.path.exists(OUT):
        log("没有 history_data.jsonl，先跑 crawl"); return
    recs = [json.loads(l) for l in open(OUT, encoding="utf-8")]
    todo = [r for r in recs if not r.get("answer")]
    if limit:
        todo = todo[:limit]
    log(f"待补答案 {len(todo)} 题（间隔 {min_sleep}-{max_sleep}s）")
    got = 0
    with sync_playwright() as pw:
        b, ctx = open_ctx(pw)
        page = ctx.new_page()
        for i, r in enumerate(todo):
            url = f"https://zujuan.xkw.com/8q{r['id']}.html"
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=40000)
                page.wait_for_timeout(3000)
                if is_blocked(page):
                    log("!! 被 WAF 阻断，停止。等冷却后再跑本命令即可续抓。")
                    break
                data = page.evaluate(ANS_JS)
                if data.get("answer"):
                    r["answer"] = data["answer"]
                    r["analysis"] = data.get("analysis", "")
                    got += 1
                    log(f"  [{i+1}/{len(todo)}] {r['id']} -> {data['answer']!r}")
                else:
                    flag = "需登录/会员" if data.get("need_login_or_vip") else "未解析到"
                    log(f"  [{i+1}/{len(todo)}] {r['id']} -> {flag}")
            except Exception as e:
                log("  ERR", r["id"], repr(e))
            if (i + 1) % 5 == 0:
                dump_all(recs)
            time.sleep(random.uniform(min_sleep, max_sleep))
        dump_all(recs)
        b.close()
    log(f"完成，本次新增答案 {got} 条 -> {OUT}")


def do_crawl(pages, start=1):
    done = load_done_ids()
    log(f"已有题目 {len(done)} 条，将跳过重复")
    with sync_playwright() as pw:
        b, ctx = open_ctx(pw)
        page = ctx.new_page()
        seen = set()
        for pageno in range(start, start + pages):
            url = LIST_URL if pageno == 1 else f"{LIST_URL}?page={pageno}"
            page.goto(url, wait_until="domcontentloaded", timeout=40000)
            page.wait_for_timeout(3000)
            if is_blocked(page):
                log("!! 被 WAF 阻断，已停止。请等待一段时间（数小时）后再运行。")
                break
            links = page.eval_on_selector_all(
                "a[href*='/8p']", "els => [...new Set(els.map(e => e.href))]")
            new_links = [l for l in links if l not in seen]
            log(f"list page {pageno}: {len(links)} papers (新增 {len(new_links)})")
            if pageno > 1 and not new_links:
                log("  本页与之前完全相同，说明 ?page=N 分页无效，请改用带筛选条件的列表URL")
            for link in new_links:
                seen.add(link)
                try:
                    recs = scrape_paper(page, link)
                    recs = [r for r in recs if r["id"] not in done]
                    save(recs)
                    for r in recs:
                        done.add(r["id"])
                    log(f"  {link} -> {len(recs)}")
                except RuntimeError:
                    log("  被阻断，停止")
                    b.close()
                    return
                except Exception as e:
                    log("  ERR", link, repr(e))
                time.sleep(random.uniform(5, 9))  # 限速，避免触发 WAF
        b.close()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "login":
        do_login()
    elif mode == "paper":
        do_paper(sys.argv[2])
    elif mode == "answers":
        do_answers(int(sys.argv[2]) if len(sys.argv) > 2 else 0)
    elif mode == "crawl":
        do_crawl(int(sys.argv[2]) if len(sys.argv) > 2 else 1,
                 int(sys.argv[3]) if len(sys.argv) > 3 else 1)
    else:
        print(__doc__)
