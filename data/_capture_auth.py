# VIP 会话捕获：打开登录页，用户手动登录；脚本轮询验证"能否看到答案解析"，验证通过才保存。
import sys, time
from playwright.sync_api import sync_playwright
sys.path.insert(0, r"d:\coursework\中级实训\benchmark_politics\src")
import scraper

state_path = r"d:\coursework\中级实训\benchmark_politics\data\_auth21_vip.json"
LOGIN_URL = "https://passport.21cnjy.com/login?jump_url=https%3A%2F%2Fzujuan.21cnjy.com%2Fquestion"
CHECK_QID = "71256386"  # 已知有答案的题

def println(*a):
    print(*a, flush=True)

def has_answer(page):
    try:
        r = page.request.get(f"https://zujuan.21cnjy.com/question/detail/{CHECK_QID}",
                             headers={"Referer": "https://zujuan.21cnjy.com/"})
        d = scraper.extract_question_json(r.text()) or {}
        return bool(d.get("answer_text")), d.get("answer_text")
    except Exception as e:
        return False, type(e).__name__

with sync_playwright() as p:
    browser = p.chromium.launch(channel="msedge", headless=False)
    ctx = browser.new_context(
        user_agent=scraper.USER_AGENT, viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2000)
    println("已打开登录页:", page.url[:90])
    println("!!!! 请在此窗口用 VIP 账号登录；登录后脚本会自动检测并保存（最长 5 分钟） !!!!")

    ok = False
    deadline = time.time() + 300
    while time.time() < deadline:
        page.wait_for_timeout(6000)
        # 不主动跳转，避免打断登录流程；仅用当前 Cookie 请求验证
        ok, val = has_answer(page)
        println(f"  验证中… 能否看到答案={ok} (answer_text={val})")
        if ok:
            break

    if ok:
        ctx.storage_state(path=state_path)
        println(">>> 验证通过，已保存登录态 ->", state_path)
    else:
        println(">>> 仍未通过验证：可能未登录成功，或该账号今日额度已用尽。可稍后重试。")
    browser.close()
println("exit")