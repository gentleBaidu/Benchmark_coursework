# -*- coding: utf-8 -*-
"""
初中历史选择题采集器（二一组卷 zujuan.21cnjy.com）

用法：
  python history_scraper_21cnjy.py crawl [页数] [起始页]   # 页数省略或填 0 = 抓到最后一页自动停
  python history_scraper_21cnjy.py login                   # 手动登录，保存 state21.json（为抓答案用）
  python history_scraper_21cnjy.py check                   # 检测 state21.json 登录态是否还有效
  python history_scraper_21cnjy.py ocr [条数] [--ana]    # 【推荐】OCR 补答案（答案/解析是图片，自动识别成文字）
  python history_scraper_21cnjy.py answers [条数]          # 备选：用浏览器逐题进详情页补答案（慢）

说明：
- 题干/选项/知识点/难度/题型/更新时间：列表页即可抓，无需登录。
- 【答案/解析】：需登录，且要进单题详情页 /question/detail/<qid> 抓。
- 该站用 JS/AJAX 翻页：直接给 URL 加 &page=N 无效，必须模拟点击“下一页”。
- 网站有反爬，务必慢速；本脚本已内置限速与断点续跑。

如何指定范围（重要）：
  在浏览器里打开 https://zujuan.21cnjy.com/question?tree_type=category&xd=2&chid=8
  用页面上的筛选（题型=选择题、适用年级、来源、版本等）选好范围，
  把地址栏 URL 复制到本文件下面的 BASE_URL，再运行 crawl。
"""
import json
import os
import re
import sys
import io
import time
import random
import urllib.request

from playwright.sync_api import sync_playwright

try:  # 保证终端能正常打印中文
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# ↓↓↓ 把你筛选好的列表页 URL 粘贴到这里（默认：初中历史 章节选题 全部）
BASE_URL = "https://zujuan.21cnjy.com/question?tree_type=category&xd=2&chid=8"
OUT = "history_21.jsonl"
STATE = "state21.json"
LOG = open("_log21.txt", "a", encoding="utf-8")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0")


def log(*a):
    msg = " ".join(str(x) for x in a)
    print(msg)              # 同时打印到终端，实时看进度
    print(msg, file=LOG)
    LOG.flush()


# 抽取当前列表页所有题目（只取顶层 li.QuestionView，跳过材料题子小题）
EXTRACT_JS = r"""
() => {
  const out = [];
  const nodes = document.querySelectorAll('.manual-list .items > ul > li.QuestionView');
  nodes.forEach(li => {
    const tit = li.querySelector('.q-tit');
    const qid = tit ? tit.getAttribute('data-qid') : '';
    let stem = '';
    if (tit) {
      const c = tit.cloneNode(true);
      c.querySelectorAll('a.ques-origin').forEach(a => a.remove());
      stem = c.innerText.replace(/\s+/g, ' ').trim().replace(/^\d+\.\s*/, '');
    }
    const opts = {};
    li.querySelectorAll('.exam-s > .op-item').forEach(op => {
      const k = op.querySelector('.op-item-nut');
      const v = op.querySelector('.op-item-meat');
      if (k && v) {
        const key = k.innerText.replace(/[^A-Da-d]/g, '').toUpperCase();
        if (key) opts[key] = v.innerText.trim();
      }
    });
    const attrs = [...li.querySelectorAll('.q-attribute .q-attr > span')]
      .filter(s => !/q-attr-btn/.test(s.className))
      .map(s => s.innerText.trim()).filter(Boolean);
    const kn = [...li.querySelectorAll('.q-analyize .knowledge_link')].map(a => a.innerText.trim());
    const imgs = [...li.querySelectorAll('.q-tit img')]
      .map(im => (im.getAttribute('src') || '').replace(/^\/\//, 'https://'));
    const anaBox = li.querySelector('.q-analyize');
    const anaText = anaBox ? anaBox.innerText : '';
    const pick2 = (labels) => {
      for (const lab of labels) {
        const i = anaText.indexOf(lab);
        if (i < 0) continue;
        let s = anaText.slice(i + lab.length);
        const m = s.search(/【[^】]+】/);
        if (m > 0) s = s.slice(0, m);
        s = s.replace(/\s+/g, ' ').trim();
        if (s) return s;
      }
      return '';
    };
    const ans = pick2(['【答案】', '答案：', '答案:']);
    const ana = pick2(['【解析】', '解析：', '解析:']);
    const detail = li.querySelector('.q-handle .J_analyize');
    const upd = li.querySelector('.q-msg .q-attr span');
    out.push({
      qid: qid, stem: stem, opts: opts, attrs: attrs, kn: kn, imgs: imgs,
      ans: ans, ana: ana, detail: detail ? detail.getAttribute('href') : '',
      update: upd ? upd.innerText.replace('更新时间：', '').trim() : ''
    });
  });
  return out;
}
"""

ANS_JS = r"""
() => {
  const t = document.body ? document.body.innerText : '';
  const pick = (labels) => {
    for (const lab of labels) {
      const i = t.indexOf(lab);
      if (i < 0) continue;
      let s = t.slice(i + lab.length);
      const m = s.search(/【[^】]+】/);
      if (m > 0) s = s.slice(0, m);
      s = s.replace(/\s+/g, ' ').trim();
      if (s) return s;
    }
    return '';
  };
  return {
    answer: pick(['【答案】', '答案：', '答案:']),
    analysis: pick(['【解析】', '解析：', '解析:']),
    blocked_login: t.includes('未登录') || t.includes('无法查看')
  };
}
"""

# 点击“下一页”（站点用 JS/AJAX 翻页，直接改 URL 的 page 参数无效）
CLICK_NEXT_JS = r"""
() => {
  const a = [...document.querySelectorAll('a')].find(x => x.textContent.trim().includes('下一页'));
  if (a) { a.click(); return true; }
  return false;
}
"""

TOTAL_PAGES_JS = r"""
() => {
  const e = document.querySelector('#page_total');
  return e ? parseInt(e.getAttribute('data-total') || '0') : 0;
}
"""


def is_blocked(page):
    try:
        body = page.inner_text("body")[:1500]
        return "访问被阻断" in body or "被阻断" in body or "验证" in page.title()
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


def to_record(raw, page_url):
    attrs = raw["attrs"]
    return {
        "id": raw["qid"],
        "subject": "历史",
        "qtype": attrs[0] if attrs else "",
        "difficulty": attrs[1] if len(attrs) > 1 else "",
        "used_count": attrs[2] if len(attrs) > 2 else "",
        "stem": raw["stem"],
        "options": raw["opts"],
        "answer": raw["ans"],
        "analysis": raw.get("ana", ""),
        "knowledge": raw["kn"],
        "images": raw["imgs"],
        "update": raw["update"],
        "detail_url": ("https://zujuan.21cnjy.com" + raw["detail"]) if raw["detail"] else "",
        "list_url": page_url,
    }


def save(recs):
    with open(OUT, "a", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def dump_all(recs):
    with open(OUT, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def new_browser(pw, headless=True, state=None):
    b = pw.chromium.launch(headless=headless, channel="msedge")
    kw = {"user_agent": UA}
    if state and os.path.exists(state):
        kw["storage_state"] = state
    return b, b.new_context(**kw)


def do_login():
    with sync_playwright() as pw:
        b, ctx = new_browser(pw, headless=False)
        page = ctx.new_page()
        page.goto("https://zujuan.21cnjy.com/", wait_until="commit", timeout=60000)
        input(">>> 请在浏览器里点右上角“登录”完成登录；\n"
              "    确认页面右上角已经显示你的用户名/头像后，再回终端按回车...")
        ctx.storage_state(path=STATE)
        # 用一张需要登录的题目详情页验证登录是否真的生效
        log("正在校验登录态...")
        page.goto("https://zujuan.21cnjy.com/question/detail/52321706",
                  wait_until="commit", timeout=60000)
        page.wait_for_timeout(3500)
        body = page.inner_text("body")
        if "未登录" in body:
            log("!! 校验失败：仍显示“未登录”，说明登录没有成功保存。")
            log("   请重新运行 login，务必在浏览器里真正登录成功（右上角显示你的账号）后再按回车。")
        elif "【答案】" in body or "【解析】" in body:
            log("登录成功，且能看到答案解析 —— 已保存 ->", STATE)
        else:
            log("已保存登录态 ->", STATE)
            log("（提示：该账号若看不到答案解析，可能是权限/会员问题，请看页面提示。）")
        b.close()


def do_crawl(pages, start=1):
    """用浏览器点击“下一页”翻页（URL 加 page 参数无效）。"""
    if pages <= 0:
        pages = 10 ** 9  # 不限页数，跑到最后一页为止
    done = load_done_ids()
    log(f"已有 {len(done)} 题，将跳过重复。开始抓取...")
    with sync_playwright() as pw:
        b, ctx = new_browser(pw, state=STATE)   # 若已登录，可顺带把列表页里的答案一起抓到
        page = ctx.new_page()
        page.goto(BASE_URL, wait_until="commit", timeout=60000)
        page.wait_for_timeout(4500)
        if is_blocked(page):
            log("!! 被拦截，停止。"); b.close(); return
        try:
            total_pages = page.evaluate(TOTAL_PAGES_JS)
        except Exception:
            total_pages = 0
        log(f"该范围共约 {total_pages} 页" if total_pages else "未取到总页数，按空页/无下一页停止")

        pageno = 1
        # 先点 (start-1) 次跳过前面的页
        while pageno < start:
            if not page.evaluate(CLICK_NEXT_JS):
                log("无法跳到起始页，结束"); b.close(); return
            pageno += 1
            page.wait_for_timeout(random.uniform(3000, 5000))

        count = 0
        while count < pages:
            raw = page.evaluate(EXTRACT_JS)
            if not raw:
                log(f"第 {pageno} 页无题目 —— 抓取结束。"); break
            recs = [to_record(r, page.url) for r in raw]
            new = [r for r in recs if r["id"] and r["id"] not in done]
            save(new)
            for r in new:
                done.add(r["id"])
            count += 1
            log(f"[{pageno}/{total_pages or '?'}] 本页 {len(recs)} 题，新增 {len(new)}，累计 {len(done)}")
            if total_pages and pageno >= total_pages:
                log(f"已到最后第 {total_pages} 页 —— 抓取结束。"); break
            if count >= pages:
                log(f"已抓够 {pages} 页，结束。"); break
            if not page.evaluate(CLICK_NEXT_JS):
                log("没有“下一页”了 —— 抓取结束。"); break
            pageno += 1
            page.wait_for_timeout(random.uniform(3500, 6000))
            if is_blocked(page):
                log("!! 被拦截，停止。等冷却后重跑会自动续抓。"); break
        b.close()
    log(f"===== 抓取完成：本次 {count} 页，累计 {len(done)} 题 -> {OUT} =====")


def do_refresh_answers(min_sleep=3, max_sleep=6):
    """从列表页批量补答案（需已登录）。比逐题进详情页快很多。"""
    if not os.path.exists(STATE):
        log("没有 state21.json，请先运行: python history_scraper_21cnjy.py login"); return
    if not os.path.exists(OUT):
        log("没有 history_21.jsonl，先跑 crawl"); return
    recs = [json.loads(l) for l in open(OUT, encoding="utf-8")]
    index = {r["id"]: r for r in recs}
    log(f"载入 {len(recs)} 题；开始从列表页补答案（需登录态有效）")
    updated = 0
    with sync_playwright() as pw:
        b, ctx = new_browser(pw, state=STATE)
        page = ctx.new_page()
        page.goto(BASE_URL, wait_until="commit", timeout=60000)
        page.wait_for_timeout(4500)
        if is_blocked(page):
            log("!! 被拦截，停止。"); b.close(); return
        total_pages = page.evaluate(TOTAL_PAGES_JS)
        log(f"共约 {total_pages} 页")
        pageno = 1
        while True:
            raw = page.evaluate(EXTRACT_JS)
            if not raw:
                log("无题目，结束"); break
            got_this = 0
            for item in raw:
                r = index.get(item["qid"])
                if r is None:
                    continue
                if item["ans"] and not r.get("answer"):
                    r["answer"] = item["ans"]
                    r["analysis"] = item.get("ana", "")
                    got_this += 1
            updated += got_this
            log(f"[{pageno}/{total_pages or '?'}] 本页 {len(raw)} 题，补到答案 {got_this}，累计 {updated}")
            if pageno % 10 == 0:
                dump_all(recs)
            if pageno == 1 and updated == 0:
                log("!! 列表页没出现答案 —— 可能未登录或需会员，请改用: python history_scraper_21cnjy.py answers")
                break
            if total_pages and pageno >= total_pages:
                break
            if not page.evaluate(CLICK_NEXT_JS):
                break
            pageno += 1
            page.wait_for_timeout(random.uniform(min_sleep, max_sleep) * 1000)
            if is_blocked(page):
                log("!! 被拦截，停止"); break
        dump_all(recs)
        b.close()
    log(f"===== 列表页补答案完成：本次新增 {updated} 条答案 =====")


def _cookie_header():
    st = json.load(open(STATE, encoding="utf-8"))
    return "; ".join(f'{c["name"]}={c["value"]}' for c in st.get("cookies", []))


def _http_bytes(url, cookie):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Cookie": cookie, "Referer": "https://zujuan.21cnjy.com/"})
    return urllib.request.urlopen(req, timeout=30).read()


def _html_login_expired(html):
    """详情页头部出现登录入口 —— 说明 cookie 已失效（登录成功时这里显示用户名）。"""
    return ("top-login-item" in html) or ("/site/login" in html) or ("未登录" in html) or ("登录查看" in html)


def do_check():
    """检测 state21.json 的登录态是否仍然有效。"""
    if not os.path.exists(STATE):
        log("未找到 state21.json —— 尚未登录，请运行: python history_scraper_21cnjy.py login"); return
    try:
        cookie = _cookie_header()
        html = _http_bytes("https://zujuan.21cnjy.com/question/detail/52321706",
                           cookie).decode("utf-8", "ignore")
    except Exception as e:
        log("检测失败（网络错误）：", repr(e)); return
    if _html_login_expired(html):
        log("登录态：已失效 —— 请重新运行: python history_scraper_21cnjy.py login")
    else:
        log("登录态：有效")


def _ocr_text(ocr, img_bytes, np, Image):
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    res, _ = ocr(np.array(img))
    if not res:
        return ""
    return " ".join(t[1] for t in res)


def do_ocr_answers(limit=0, with_analysis=False, min_sleep=0.6, max_sleep=1.2):
    """不用浏览器：带 cookie 取详情页里的「带签名答案图片」URL，下载后 OCR 识别并回填。"""
    if not os.path.exists(STATE):
        log("没有 state21.json，请先运行: python history_scraper_21cnjy.py login"); return
    if not os.path.exists(OUT):
        log("没有 history_21.jsonl"); return
    try:
        import numpy as np
        from PIL import Image
        from rapidocr_onnxruntime import RapidOCR
    except Exception as e:
        log("OCR 依赖缺失（需 rapidocr-onnxruntime / numpy / Pillow）：", repr(e)); return
    ocr = RapidOCR()
    recs = [json.loads(l) for l in open(OUT, encoding="utf-8")]
    # 只补「单选题」且还没有答案的（其余题型没有字母答案图，跳过以免反复白跑）
    todo = [r for r in recs if not r.get("answer") and r.get("qtype") == "单选题"]
    if limit:
        todo = todo[:limit]
    log(f"待补答案 {len(todo)} 题（OCR 模式，含解析={with_analysis}）")
    cookie = _cookie_header()
    got = 0
    no_img = 0
    for i, r in enumerate(todo):
        q = r["id"]
        try:
            html = _http_bytes(f"https://zujuan.21cnjy.com/question/detail/{q}",
                               cookie).decode("utf-8", "ignore")
            if _html_login_expired(html):
                log("!! 登录态已失效，停止。请重新运行: python history_scraper_21cnjy.py login")
                break
            m = re.search(r'(//webshot[^"\']*_%san\.png[^"\']*)' % q, html)
            if m:
                no_img = 0
                ans_img = _http_bytes("https:" + m.group(1), cookie)
                txt = _ocr_text(ocr, ans_img, np, Image)
                letters = re.findall(r"[A-Da-d]", txt)
                if letters:
                    r["answer"] = letters[0].upper()
                    got += 1
            else:
                no_img += 1
                if no_img >= 8 and got == 0:
                    log("!! 连续多题取不到答案图片 —— 疑似登录失效或被限流。请重新 login 后再跑。")
                    break
            if with_analysis and not r.get("analysis"):
                m2 = re.search(r'(//webshot[^"\']*_%sex\.png[^"\']*)' % q, html)
                if m2:
                    ex_img = _http_bytes("https:" + m2.group(1), cookie)
                    r["analysis"] = _ocr_text(ocr, ex_img, np, Image)
            log(f"  [{i+1}/{len(todo)}] {q} -> {r.get('answer')!r}")
        except Exception as e:
            log("  ERR", q, repr(e))
        if (i + 1) % 20 == 0:
            dump_all(recs)
        time.sleep(random.uniform(min_sleep, max_sleep))
    dump_all(recs)
    log(f"===== OCR 补答案完成：本次新增 {got} 条答案 -> {OUT} =====")


def do_answers(limit=0, min_sleep=10, max_sleep=18):
    if not os.path.exists(OUT):
        log("没有 history_21.jsonl，先跑 crawl"); return
    recs = [json.loads(l) for l in open(OUT, encoding="utf-8")]
    todo = [r for r in recs if not r.get("answer")]
    if limit:
        todo = todo[:limit]
    log(f"待补答案 {len(todo)} 题")
    got = 0
    with sync_playwright() as pw:
        b, ctx = new_browser(pw, state=STATE)
        page = ctx.new_page()
        for i, r in enumerate(todo):
            url = r.get("detail_url") or f"https://zujuan.21cnjy.com/question/detail/{r['id']}"
            try:
                page.goto(url, wait_until="commit", timeout=60000)
                page.wait_for_timeout(3500)
                if is_blocked(page):
                    log("!! 被拦截，停止"); break
                d = page.evaluate(ANS_JS)
                if d.get("answer"):
                    r["answer"] = d["answer"]; r["analysis"] = d.get("analysis", "")
                    got += 1
                    log(f"  [{i+1}/{len(todo)}] {r['id']} -> {d['answer']!r}")
                else:
                    log(f"  [{i+1}/{len(todo)}] {r['id']} -> 未取到({d.get('blocked_login') and '需登录/会员' or '无解析'})")
            except Exception as e:
                log("  ERR", r["id"], repr(e))
            if (i + 1) % 5 == 0:
                dump_all(recs)
            time.sleep(random.uniform(min_sleep, max_sleep))
        dump_all(recs)
        b.close()
    log(f"answers done, 新增 {got}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "login":
        do_login()
    elif mode == "check":
        do_check()
    elif mode == "crawl":
        do_crawl(int(sys.argv[2]) if len(sys.argv) > 2 else 0,
                 int(sys.argv[3]) if len(sys.argv) > 3 else 1)
    elif mode == "refresh":
        do_refresh_answers()
    elif mode == "ocr":
        do_ocr_answers(int(sys.argv[2]) if len(sys.argv) > 2 else 0,
                       ("--ana" in sys.argv))
    elif mode == "answers":
        do_answers(int(sys.argv[2]) if len(sys.argv) > 2 else 0)
    else:
        print(__doc__)
