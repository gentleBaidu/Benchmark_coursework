# benchmark_politics

低频抓取学科网组卷站点的政治/道德与法治题目，并导出为 xlsx。

## 目标

- 政治/道德与法治：`https://zujuan.xkw.com/czzz/`
- 目标 1200 题。
- 输出字段：学科、题目链接、题号、题干、选项、答案、解析、抓取状态、抓取时间。

## 重要说明

我对 `https://zujuan.xkw.com/czzz/`、示例题目列表页以及 `robots.txt` 做了少量访问测试，返回的是站点 JavaScript 验证页，而不是题目正文。这意味着：

- 直接 HTTP 请求会先遇到反爬 JS 校验。
- 是否需要登录、是否需要付费会员，当前无法仅凭这次低频探测确认。
- 本爬虫支持传入浏览器中已登录后的 Cookie；如果页面仍返回“会员/VIP/付费/开通”等提示，结果会在 `status` 中标记为 `paywall_or_vip`。
- 请只抓取你有权限访问和使用的数据，遵守网站条款、robots 规则和版权限制。

## 安装依赖

使用当前 Codex 内置 Python 时，`openpyxl` 和 `lxml` 已可用。普通 Python 环境可安装：

```powershell
python -m pip install openpyxl lxml
```

## 使用方式

先运行自检，确认解析和 xlsx 导出正常：

```powershell
python .\src\scraper.py --self-test
```

低频抓取，默认目标 1200 题：

```powershell
python .\src\scraper.py --target-count 1200 --min-delay 3 --max-delay 8
```

如果站点要求登录，先在浏览器登录，再把 Cookie 放到环境变量或文件里：

```powershell
$env:XKW_COOKIE="key1=value1; key2=value2"
python .\src\scraper.py --cookie-env XKW_COOKIE
```

也可以使用 Cookie 文件：

```powershell
python .\src\scraper.py --cookie-file .\data\cookie.txt
```

输出默认保存到：

```text
data/benchmark_politics.xlsx
```

## 限速与断点

默认策略尽量避免疯狂请求：

- 请求间隔默认随机 3 到 8 秒。
- 每个 URL 会缓存到 `data/cache/`，重复运行优先读缓存。
- 失败请求使用指数退避。
- 达到 1200 题目标后立即停止。
- 如果检测到 JS 验证、登录墙或会员提示，会记录状态并跳过继续猛刷详情页。

## 常用参数

```text
--target-count 1200         政治题目目标数量
--min-delay 3               最小请求间隔秒数
--max-delay 8               最大请求间隔秒数
--max-list-pages 200        最多列表页
--output data/out.xlsx      输出 xlsx 路径
--refresh-cache             忽略已有缓存重新请求
--dry-run                   只抓列表和少量详情，不写满目标
```

