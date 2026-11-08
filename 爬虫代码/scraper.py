# -*- coding: utf-8 -*-
"""二一组卷(m.chujuan.cn)题目批量采集脚本
- 断点续传: results.jsonl 里已有的 oid 自动跳过
- 字体混淆自动解码: 每页随机混淆字体 -> 下载 ttf -> 渲染成图 -> OCR 逐字识别
用法: python scraper.py [目标题数]   (默认400)
"""
import io, sys, re, json, os, time, html as htmlmod, hashlib, requests
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

D = os.path.dirname(os.path.abspath(__file__))
os.makedirs(os.path.join(D, 'fontmaps'), exist_ok=True)
os.makedirs(os.path.join(D, 'fonts'), exist_ok=True)
os.makedirs(os.path.join(D, 'pages'), exist_ok=True)
CANDS = os.path.join(D, 'cands.jsonl')
RESULTS = os.path.join(D, 'results.jsonl')
LOG = os.path.join(D, 'scrape.log')

COOKIE_FILE = os.path.join(D, 'cookie.txt')
if not os.path.exists(COOKIE_FILE):
    sys.exit('缺少 cookie.txt: 浏览器登录 m.chujuan.cn 后, F12 -> Network -> 任一请求 -> '
             '复制请求头里 Cookie 的完整内容, 存成 cookie.txt 放在本脚本同目录')
TARGET = int(sys.argv[1]) if len(sys.argv) > 1 else 400
cookie = open(COOKIE_FILE, encoding='utf-8').read().strip()
H = {'User-Agent': 'Mozilla/5.0 (Linux; Android 12)', 'Cookie': cookie}
S = requests.Session()
S.headers.update(H)

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont
import numpy as np
from rapidocr_onnxruntime import RapidOCR
OCR = RapidOCR()

_font_maps = {}


def get_font_map(font_url):
    if font_url in _font_maps:
        return _font_maps[font_url]
    key = hashlib.md5(font_url.encode()).hexdigest()[:16]
    mp = os.path.join(D, 'fontmaps', key + '.json')
    if os.path.exists(mp):
        m = {int(k): v for k, v in json.load(open(mp, encoding='utf-8')).items()}
        _font_maps[font_url] = m
        return m
    fp = os.path.join(D, 'fonts', key + '.ttf')
    if not os.path.exists(fp):
        data = S.get(font_url, timeout=20).content
        open(fp, 'wb').write(data)
    ft = TTFont(fp)
    cmap = ft.getBestCmap()
    puas = [cp for cp in cmap if 0xE000 <= cp <= 0xF8FF]
    font = None
    try:
        font = ImageFont.truetype(fp, 96)
    except Exception:
        font = None
    mapping = {}
    for cp in puas:
        if font is None:
            mapping[cp] = ''
            continue
        img = Image.new('RGB', (136, 136), (255, 255, 255))
        ImageDraw.Draw(img).text((20, 20), chr(cp), font=font, fill=(0, 0, 0))
        a = np.asarray(img.convert('L'))
        ys, xs = np.where(a < 128)
        if len(xs) == 0:
            mapping[cp] = ''
            continue
        pad = 4
        box = (max(xs.min()-pad, 0), max(ys.min()-pad, 0),
               min(xs.max()+pad, a.shape[1]), min(ys.max()+pad, a.shape[0]))
        gp = os.path.join(D, 'fonts', 'g.png')
        img.crop(box).save(gp)
        try:
            res, _ = OCR(gp, use_det=False, use_cls=False)
            mapping[cp] = res[0][0].strip() if res else ''
        except Exception:
            mapping[cp] = ''
    json.dump({str(k): v for k, v in mapping.items()},
              open(mp, 'w', encoding='utf-8'), ensure_ascii=False)
    _font_maps[font_url] = mapping
    return mapping


def decode(text, fmap):
    out = []
    for ch in text:
        cp = ord(ch)
        if 0xE000 <= cp <= 0xF8FF:
            out.append(fmap.get(cp, ''))
        else:
            out.append(ch)
    return ''.join(out)


def strip_tags(s):
    s = re.sub(r'<[^>]+>', '', s)
    s = htmlmod.unescape(s)
    s = s.replace(' ', ' ')
    return re.sub(r'\s+', ' ', s).strip()


def parse_page(oid, htmltext):
    """Return record dict or (None, reason)."""
    if '试题已下架' in htmltext or len(htmltext) < 8000:
        return None, 'delisted'
    # main block slice
    j = htmltext.find(f'data-qid="{oid}"')
    if j < 0:
        return None, 'no-main-block'
    k = htmltext.find('question__item"', j + 10)
    end = k if k > 0 else j + 40000
    blk = htmltext[j:end]
    if '<img' in blk:
        return None, 'has-image'
    # stem
    m = re.search(r'class="question__title[^"]*"[^>]*>(.*?)</div>', blk, re.S)
    if not m:
        return None, 'no-stem'
    stem = strip_tags(m.group(1))
    stem = re.sub(r'^\d+[\.、]\s*', '', stem)
    # composite skip
    if '小题' in stem or '完成下列' in stem or '完成下面' in stem:
        return None, 'composite'
    # options
    opts = {}
    for mm in re.finditer(r'option__num">([A-D])\.</span>\s*<span class="option__meat">(.*?)</span>', blk, re.S):
        opts[mm.group(1)] = strip_tags(mm.group(2))
    # answer
    m = re.search(r'question__answer"[^>]*>.*?analyze__meat">\s*<p>(.*?)</p>', blk, re.S)
    if not m:
        return None, 'no-answer-block'
    ans_raw = m.group(1).strip()
    if not ans_raw:
        return None, 'answer-empty(composite?)'
    # font map
    fmap = {}
    ft = re.search(r'(https?://[^"\' ]+?\.ttf)', htmltext)
    if ft:
        try:
            fmap = get_font_map(ft.group(1))
        except Exception as e:
            return None, f'font-err:{e}'
    answer = decode(ans_raw, fmap).strip()
    if not answer:
        return None, 'answer-decode-empty'
    # explanation
    exp = ''
    m = re.search(r'question__analyze">\s*<div class="analyze__tit">【解析】</div>\s*<div class="analyze__meat">(.*?)</div>', blk, re.S)
    if m:
        exp = decode(m.group(1).strip(), fmap)
        exp = strip_tags(exp) if '<' in exp else exp.strip()
        if '敬请' in exp and len(exp) < 15:
            exp = ''
    # kp
    kp = []
    m = re.search(r'question__point">\s*<div class="analyze__tit">【考点】</div>\s*<div class="analyze__meat">\s*(.*?)\s*</div>', blk, re.S)
    if m:
        kp = [t.strip() for t in strip_tags(m.group(1)).split(';') if t.strip()]
    # tags: type + difficulty
    tags = re.findall(r'tag__btn">([^<]+)<', blk)
    qtype = tags[0] if tags else ''
    diff = tags[1] if len(tags) > 1 else ''
    # origin
    origin = ''
    m = re.search(r'question__origin[^>]*>([^<]+)<', blk)
    if m:
        origin = m.group(1).strip()
    if not stem or not opts:
        return None, 'stem-or-options-empty'
    rec = {'oid': oid, 'question_type': qtype, 'question_text': stem,
           'options': opts, 'answer': answer, 'explanation': exp,
           'knowledge_points': kp, 'difficulty_original': diff, 'origin': origin}
    return rec, 'ok'


def main():
    done_oids = set()
    if os.path.exists(RESULTS):
        for l in open(RESULTS, encoding='utf-8'):
            if l.strip():
                done_oids.add(json.loads(l)['oid'])
    ok_count = len(done_oids)
    print(f'resume: {ok_count} ok already', flush=True)
    out = open(RESULTS, 'a', encoding='utf-8')
    skips = {}
    for l in open(CANDS, encoding='utf-8'):
        if not l.strip():
            continue
        try:
            oid = json.loads(l)['oid']
        except Exception:
            break  # truncated line from concurrent discovery append
        if oid in done_oids:
            continue
        if ok_count >= TARGET:
            break
        url = f'https://m.chujuan.cn/question/detail-{oid}.shtml'
        try:
            r = S.get(url, timeout=25)
        except Exception as e:
            skips[f'net:{type(e).__name__}'] = skips.get(f'net:{type(e).__name__}', 0) + 1
            time.sleep(2)
            continue
        if r.status_code != 200 or len(r.text) < 8000:
            skips['bad-status'] = skips.get('bad-status', 0) + 1
            time.sleep(0.7)
            continue
        htmltext = r.text
        try:
            rec, why = parse_page(oid, htmltext)
        except Exception as e:
            rec, why = None, f'exc:{type(e).__name__}:{e}'
        if rec is None:
            skips[why] = skips.get(why, 0) + 1
            time.sleep(0.5)
            continue
        out.write(json.dumps(rec, ensure_ascii=False) + '\n')
        out.flush()
        done_oids.add(oid)
        ok_count += 1
        if ok_count % 10 == 0:
            print(f'ok={ok_count} skips={skips}', flush=True)
        time.sleep(0.7)
    out.close()
    print(f'FINAL ok={ok_count} skips={skips}', flush=True)


if __name__ == '__main__':
    main()
