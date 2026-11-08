二一组卷题目采集脚本 使用说明

一、这是什么
1. 两个 Python 脚本: discovery.py 按关键词扫描站内搜索, 收集候选题目; scraper.py 逐题抓取题干、选项、答案、解析、考点, 并自动破解官网的字体混淆(混淆字体渲染成图后 OCR 逐字识别)。
2. 支持断点续传: 中途停掉再跑, 已抓过的题自动跳过。

二、运行环境
1. 需要 Python 3.9 及以上。
2. 命令行执行下面一条命令装依赖:
   pip install requests fonttools pillow numpy rapidocr-onnxruntime

三、准备 cookie
1. 浏览器打开 m.chujuan.cn 并登录。
2. 按 F12 打开开发者工具, 选 Network(网络)标签, 刷新页面, 点任意一个请求, 在 Request Headers(请求标头)里找到 Cookie 一行, 复制它的完整内容。
3. 新建文本文件 cookie.txt, 把复制的内容粘进去保存, 和脚本放在同一个文件夹。

四、运行顺序
1. 先跑海选: python discovery.py
   同目录生成 cands.jsonl(候选题目列表)。
2. 再跑采集: python scraper.py 400
   数字是想要抓的题数, 不写默认 400。结果写入 results.jsonl。
3. 想多抓就再跑一次 scraper.py, 已抓的自动跳过; 候选不够就再跑一次 discovery.py。

五、去重(可选)
1. 如果之前采集过题目, 把旧题的 original_question_id 每行一个存进 used.txt 放同目录, discovery.py 会自动避开这些题。
2. scraper.py 本身按题干前 40 字去重, 同一批内不会出重复题。
3. 注意: 站上带图片的题约占七成半、材料组合大题也不少, 实际出题率约两成。想要 400 道净题, 建议候选池海选到 2000 以上, 不够就多跑几轮 discovery.py。

六、结果字段说明(results.jsonl 每行一条)
1. oid: 官网题目 ID
2. question_type: 题型
3. question_text: 题干
4. options: 选项(A 到 D)
5. answer: 答案
6. explanation: 解析(可能为空)
7. knowledge_points: 考点列表
8. difficulty_original: 官网难度标签

七、注意
1. 脚本已内置 0.4 到 0.7 秒的请求间隔, 请勿改成并发, 以免账号被限制。
2. 带图片的题、材料组合大题会自动跳过, 本脚本只采纯文字的单题。
3. 请仅用于自己有授权的用途。
