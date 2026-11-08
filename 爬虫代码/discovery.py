# -*- coding: utf-8 -*-
"""二一组卷(m.chujuan.cn)候选题目海选脚本
按关键词扫描站内搜索列表页, 收集候选题目 id, 写入同目录 cands.jsonl。
自动跳过 used.txt 里列过的旧题 id(去重, 可选)。
用法: python discovery.py
"""
import io, sys, re, json, os, time, requests
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

D = os.path.dirname(os.path.abspath(__file__))
CANDS = os.path.join(D, 'cands.jsonl')

COOKIE_FILE = os.path.join(D, 'cookie.txt')
if not os.path.exists(COOKIE_FILE):
    sys.exit('缺少 cookie.txt: 浏览器登录 m.chujuan.cn 后, F12 -> Network -> 任一请求 -> '
             '复制请求头里 Cookie 的完整内容, 存成 cookie.txt 放在本脚本同目录')
cookie = open(COOKIE_FILE, encoding='utf-8').read().strip()
H = {'User-Agent': 'Mozilla/5.0 (Linux; Android 12)', 'Cookie': cookie}

# --- used set (可选去重) ---
used = set()
used_file = os.path.join(D, 'used.txt')
if os.path.exists(used_file):
    for l in open(used_file, encoding='utf-8'):
        l = l.strip()
        if l:
            used.add(l)
print('used set:', len(used), flush=True)

# --- keyword sweep ---
KWS = [
    '经纬网', '地球自转', '地球公转', '等高线', '地图三要素', '海陆分布', '板块',
    '天气', '气温', '降水', '气候', '人口', '民族', '聚落', '疆域', '行政区划',
    '河流', '自然资源', '土地资源', '水资源', '农业', '工业', '交通', '四大地理区域',
    '北方地区', '南方地区', '西北地区', '青藏地区', '长江', '黄河', '日本', '东南亚',
    '印度', '俄罗斯', '中东', '欧洲西部', '撒哈拉以南非洲', '澳大利亚', '美国', '巴西',
    '极地地区', '半球', '大洲', '太平洋', '地形', '季风气候', '自然灾害', '发展差异',
    '国际合作', '宗教', '人种', '语言', '经线', '纬线', '比例尺', '方向', '图例',
    '卫星', '航天', '地球仪', '海拔', '等温线', '等降水量', '气温曲线', '传统民居',
    '经度', '纬度', '本初子午线', '时区', '昼夜', '四季', '五带', '地壳', '海陆变迁',
    '大陆漂移', '天气预报', '湿度', '风力', ' 梅雨', '台风', '寒潮', '水土流失',
    '土地荒漠化', '耕地', '林地', '草地', '南水北调', '西气东输', '铁路干线',
    '高速铁路', '京津冀', '长三角', '珠三角', '珠江', '塔里木', '三江源', '黄土高原',
    '华北平原', '东北平原', '四川盆地', '云贵高原', '内蒙古高原', '青藏高原',
    '台湾省', '香港', '澳门', '新疆', '/china', '世界人口', '城市化', '发达国家',
    '发展中国家', '南北对话', '南南合作', '联合国', 'WTO', '热带雨林', '地中海',
    '温带海洋性', '温带季风', '热带草原', '高原山地', '尼罗河', '亚马逊', '密西西比',
]
CAP = 2600  # 候选池上限, 实际成题率约两成, 想抓400道建议至少海选2000
seen = []
done_kws = set()
if os.path.exists(CANDS):
    for l in open(CANDS, encoding='utf-8'):
        if l.strip():
            d = json.loads(l)
            seen.append(d['oid'])
            done_kws.add(d.get('kw', ''))
seen_set = set(seen)
out = open(CANDS, 'a', encoding='utf-8')

s = requests.Session()
s.headers.update(H)
n_new = 0
for kw in KWS:
    if kw in done_kws:
        print('skip done kw:', kw, flush=True)
        continue
    for page in range(1, 11):
        url = (f'https://m.chujuan.cn/question/search?xd=2&chid=10'
               f'&type=question&page={page}&content={kw}')
        try:
            r = s.get(url, timeout=20)
        except Exception as e:
            print('ERR', kw, page, e, flush=True)
            time.sleep(2)
            continue
        qids = re.findall(r'data-qid="(\d+)"', r.text)
        if not qids:
            break
        added = 0
        for q in qids:
            if q in used or q in seen_set:
                continue
            seen_set.add(q)
            seen.append(q)
            out.write(json.dumps({'oid': q, 'kw': kw, 'page': page},
                                 ensure_ascii=False) + '\n')
            out.flush()
            added += 1
            n_new += 1
        print(f'{kw} p{page}: +{added} (total {n_new})', flush=True)
        time.sleep(0.4)
        if n_new >= CAP:
            break
    if n_new >= CAP:
        break
out.close()
print('done. candidates:', n_new, flush=True)
