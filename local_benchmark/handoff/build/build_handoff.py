from pathlib import Path
import json, hashlib, importlib.util
from datetime import datetime, timezone, timedelta
from docx import Document
from docx.shared import Inches, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.enum.text import WD_ALIGN_PARAGRAPH

ROOT=Path('D:/benchmark/local_benchmark')
OUT=ROOT/'handoff/大模型测评项目PPT交接说明.docx'
spec=importlib.util.spec_from_file_location('ev',ROOT/'evaluate_local.py')
ev=importlib.util.module_from_spec(spec);spec.loader.exec_module(ev)
snap=[]
for subj in ['道德与法治','历史','地理']:
 for model in ['qwen3_1.7b','deepseek-r1_8b','qwen3_8b']:
  folder=ROOT/'results'/subj/model
  if not (folder/'summary.json').exists(): continue
  s=json.loads((folder/'summary.json').read_text(encoding='utf-8'))
  c=json.loads((folder/'config.json').read_text(encoding='utf-8'))
  records=ev.read_jsonl(folder/'scored_results.jsonl')
  assert len(records)==s['attempted_count']
  assert sum(bool(r['correct']) for r in records)==s['correct_count']
  # 独立按当前脚本核对已导出的评分，避免手抄历史口径。
  assert all(ev.rescore_record(r,r['expected'])['correct']==r['correct'] for r in records)
  snap.append({'subject':subj,'folder':str(folder),'summary':s,'config':c})
(ROOT/'handoff/build/result_snapshot.json').write_text(json.dumps(snap,ensure_ascii=False,indent=2),encoding='utf-8')

d=Document();sec=d.sections[0]
sec.page_width=Inches(8.5);sec.page_height=Inches(11)
sec.top_margin=sec.bottom_margin=Inches(.65)
sec.left_margin=sec.right_margin=Inches(.72)
styles=d.styles
for name in ['Normal','Title','Subtitle','Heading 1','Heading 2','Heading 3']:
 st=styles[name];st.font.name='Microsoft YaHei';st.font.color.rgb=RGBColor(0,0,0)
 st.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'微软雅黑')
styles['Normal'].font.size=Pt(11)
styles['Normal'].paragraph_format.line_spacing=Pt(16)
styles['Normal'].paragraph_format.space_after=Pt(5)
styles['Title'].font.size=Pt(23)
styles['Heading 1'].font.size=Pt(17)
styles['Heading 1'].paragraph_format.space_after=Pt(10)
styles['Heading 2'].font.size=Pt(12)
styles['Heading 2'].paragraph_format.space_before=Pt(9)
styles['Heading 2'].paragraph_format.space_after=Pt(5)
footer=sec.footer.paragraphs[0];footer.alignment=WD_ALIGN_PARAGRAPH.RIGHT
footer.add_run('PPT 交接说明  ·  ')
field=OxmlElement('w:fldSimple');field.set(qn('w:instr'),'PAGE');footer._p.append(field)
for r in footer.runs:r.font.size=Pt(9)

def p(t):return d.add_paragraph(t)
def h(t):d.add_heading(t,2)
def page(title):
 para=d.add_heading(title,1);para.paragraph_format.page_break_before=True
def table(head,rows,widths):
 t=d.add_table(rows=1,cols=len(head));t.autofit=False
 for c,w in zip(t.columns,widths):c.width=Inches(w)
 for i,txt in enumerate(head):t.rows[0].cells[i].text=txt
 for row in rows:
  cells=t.add_row().cells
  for i,txt in enumerate(row):cells[i].text=str(txt)
 for ri,row in enumerate(t.rows):
  pr=row._tr.get_or_add_trPr();no=OxmlElement('w:cantSplit');pr.append(no)
  if ri==0:
   repeat=OxmlElement('w:tblHeader');pr.append(repeat)
  for ci,cell in enumerate(row.cells):
   cell.width=Inches(widths[ci]);tc=cell._tc.get_or_add_tcPr()
   margins=OxmlElement('w:tcMar')
   for side in ['top','bottom','left','right']:
    a=OxmlElement('w:'+side);a.set(qn('w:w'),'75');a.set(qn('w:type'),'dxa');margins.append(a)
   tc.append(margins)
   if ri==0:
    shade=OxmlElement('w:shd');shade.set(qn('w:fill'),'E8EEF3');tc.append(shade)
   for para in cell.paragraphs:
    para.paragraph_format.space_after=Pt(2);para.paragraph_format.line_spacing=Pt(14)
    for run in para.runs:run.font.size=Pt(10);run.bold=ri==0
 p('')
 return t
def code(t):
 for line in t.splitlines():
  para=p(line);para.paragraph_format.space_after=Pt(2)
  for r in para.runs:r.font.name='Consolas';r.font.size=Pt(9)

d.add_heading('大模型学科测评项目交接说明',0)
p('供 PPT 制作使用    整理日期 2026 年 11 月 10 日')
p('本项目使用同一套本地脚本，评测不同模型在初中道德与法治、历史和地理纯文本单项选择题上的表现。本文说明数据范围、脚本流程、状态与指标口径，并提供可用于汇报的结果和页面安排。')
p('目前 Qwen3 1.7B 与 DeepSeek-R1 8B 已完成三科测评；Qwen3 8B 仅完成道德与法治前 378 题。完整结果与阶段结果需要分开展示。当前脚本只接入 Ollama 本地服务，付费云端 API 尚未在本脚本中实现。')
h('1 数据范围与模型范围')
table(['学科','实际题量','整理与使用边界'],[
 ['道德与法治','1087','原始 1200 题中，105 题待复核、8 题重复归档；使用清洗候选集。'],
 ['历史','1140','来自 usable_text(2).jsonl；检查结构与题号，题目、选项和答案保留。'],
 ['地理','185','原包 1200 题，968 题有答案；其中 783 道含图题排除，保留 185 道纯文本题。']],[1.05,.65,5.36])
p('地理题保留题组共用材料与已有表格文字；不能将该子集得分表述为整包地理成绩或读图能力。三个学科的参考答案均未在本项目中经过学科专家逐题认证。')
p('模型使用调用标签 qwen3:1.7b、deepseek-r1:8b、qwen3:8b。DeepSeek-R1 8B 是 DeepSeek-R1-0528-Qwen3-8B 蒸馏版本，并非完整的大参数 DeepSeek-R1。配置中三者均记录为 Q4_K_M 量化。模型标签中的规模与 Ollama 元数据口径可能不同：1.7B 标签的 parameter_size 记录为 2.0B，两个 8B 标签记录为 8.2B；汇报保留标签名称，不混用口径。')
h('2 当前统一生成设置')
table(['项目','取值','说明'],[
 ['思考模式 / 温度','on / 0.0','仍记录思考文本；最终答案独立评分。'],
 ['随机种子','20261109','固定种子不等于跨设备绝对可复现。'],
 ['上下文 / 输出上限','8192 / 4096 Token','输出预算包含思考；达到长度上限不计正确。'],
 ['调用与超时','串行 / 每题 900 秒','一次请求一题；Ollama 版本记录为 0.40.15。']],[1.48,1.65,3.93])

page('脚本设计与一次测评的流程')
p('入口文件为 evaluate_local.py，仅使用 Python 标准库。默认连接 http://127.0.0.1:11434，以 JSON 请求调用 Ollama 的 /api/chat。每道题使用新的单条 user 消息，不保留上题对话，也未接入检索或联网工具。')
h('3 从题库到成绩的处理顺序')
for title,body in [
 ('读取与校验','load_dataset 读取题目与答案两份 JSONL，检查 sample_id 唯一且两边完全匹配、题干非空、A–D 选项齐全、答案合法。输入文件只允许 sample_id、question、options 三个字段。'),
 ('确认模型与实验配置','读取服务版本、模型清单、模型摘要及详细配置，保存 config.json。续跑时比较生成配置、模型版本和数据哈希，避免不同实验混写。'),
 ('构造独立请求','make_payload 拼接学科提示词、题干和 A–D 选项。标准答案只进入本地评分逻辑，不发送给模型。使用非流式响应，按题串行执行。'),
 ('拆分回答与思考','读取 message.content 作为最终回答，message.thinking 单独保留；最终回答中的完整 think 标签块会在提取答案时移除。'),
 ('提取与判分','extract_answer 按明确选项标记或结论提取单个答案，score_answer 结合生成结束原因和参考答案生成状态、正确性及格式合规字段。'),
 ('保存与汇总','每题先追加 attempts.jsonl 并 flush、fsync，再重建汇总与当前评分导出。中断时汇总已完成记录，后续可续跑。')]:
 h(title);p(body)
h('4 提示词与答案隔离')
p('提示词统一为“请回答以下初中〔学科〕单项选择题。最终回答仅输出一个大写字母A、B、C或D，不要附加解释。”随后附题干与选项。提示词仍要求简短格式，但评分已允许附带解析的明确答案。')
p('evaluation_inputs.jsonl 只负责题目输入；answer_key.jsonl 通过 sample_id 关联参考答案。知识点、来源、标准答案与解析不作为提示词输入。这样可以避免将评分答案直接泄露给模型。')

page('状态含义与答案判定规则')
h('5 状态和正确性必须分开解释')
table(['status','触发条件','评分及续跑'],[
 ['ok','成功提取单个明确答案，且未报告长度截断。','可能答对也可能答错；只有 prediction 等于 expected 才 correct=true。续跑跳过。'],
 ['invalid','未提取到可靠单选答案，如空回答、多答案、明确结论冲突或规则无法识别。','按错误计入分母；续跑不重答。可人工复核解析规则。'],
 ['truncated','done_reason 为 length，达到输出长度上限。','即使已出现正确字母也不计正确；续跑不重答。'],
 ['error','调用失败、超时、服务错误或响应不完整。','按错误计入分母；下次运行重试。连续 3 题接口错误后停止。']],[.85,3.2,3.01])
p('优先判断长度截断，再区分 ok 与 invalid；API 异常由调用层记为 error。pending_count 表示尚未尝试的题目数，并不是逐题 status 的第五种取值。状态不能单独证明进程当前是否在运行。')
h('6 典型回答的处理方式')
table(['模型最终回答','参考答案','处理结果'],[
 ['B','B','ok，答对，格式合规。'],
 ['B. ①③ 或 B 后附解析','B','ok，答对，但格式不合规。'],
 ['正确答案：C','B','ok，但答错；不是 invalid。'],
 ['答案是 B 或 C','B','invalid，不任选其中一个字母。'],
 ['先写 B，后明确写最终答案 C','B','invalid，判为结论冲突。'],
 ['B，但 done_reason=length','B','truncated，不计正确。']],[3.05,.7,3.31])
p('规则兼容部分 Markdown 加粗、反引号、大小写和全角选项字母。format_compliant 仍按原始最终回答去除首尾空白后是否只有单个大写 A/B/C/D 判定，因此内容正确与格式合规可以不同。')
p('parse_method 用于定位提取原因，例如 single_letter、explicit_answer、multiple_options、conflicting_answers、no_clear_answer 或 api_error。规则解析器不能覆盖全部自然语言表述；invalid 不一定意味着模型知识回答错误，需结合原文检查。')

page('成绩口径与当前结果')
h('7 汇报时使用的指标')
p('内容准确率＝正确题数 ÷ 已尝试题数。invalid、truncated、error 均进入分母且不计正确；尚未尝试的题不进入分母。格式合规率＝仅输出单个大写选项字母的题数 ÷ 已尝试题数，它衡量格式遵循情况，不等于答题准确率。')
p('判定全量完成，应同时查看 full_dataset=true、attempted_count=selected_count=题库总数，以及 complete_without_api_errors=true。full_dataset 仅说明本次选取了整个题库；complete_without_api_errors 仅说明所选题均已尝试且没有接口错误，仍允许 invalid 和 truncated。')
h('8 当前结果快照')
result_rows=[]
for x in snap:
 s=x['summary']; result_rows.append([x['subject'],s['model'],f"{s['attempted_count']}/{s['selected_count']}",str(s['correct_count']),f"{s['accuracy']:.2%}",'完整' if s['complete_without_api_errors'] and s['full_dataset'] else '阶段'])
table(['学科','模型标签','已测 / 目标','正确','准确率','范围'],result_rows,[1.0,1.55,1.12,.55,.86,1.98])
p('快照以 2026 年 11 月 10 日交接时本地结果文件为准。Qwen3 8B 的 96.30% 只对应已测的 378 道道德与法治题，另有 709 题待测；历史和地理尚无其结果记录。未测不应画为 0 分，也不能直接用于全量排行榜。')
h('9 可直接用于 PPT 的完整结果比较')
for subj in ['道德与法治','历史','地理']:
 a=next(x['summary'] for x in snap if x['subject']==subj and x['summary']['model']=='qwen3:1.7b')
 b=next(x['summary'] for x in snap if x['subject']==subj and x['summary']['model']=='deepseek-r1:8b')
 p(f"{subj}：Qwen3 1.7B 为 {a['accuracy']:.2%}，DeepSeek-R1 8B 为 {b['accuracy']:.2%}，后者高 {(b['accuracy']-a['accuracy'])*100:.2f} 个百分点。")
p('这些差异来自具体模型、训练方式、参数规模、量化及生成预算的共同影响，不能单独归因于品牌或参数量。建议每科独立作图，避免用题量不同的三科简单混合得分代替学科比较。')

page('结果文件与续跑机制')
h('10 交接目录与文件用途')
p('项目根目录为 D:\\benchmark\\local_benchmark。道德与法治数据在 data 根目录，历史在 data/history，地理在 data/geography。结果统一为 results/学科/模型名称，例如 results/历史/deepseek-r1_8b。Windows 目录中的模型冒号替换为下划线。')
table(['文件','交接时如何使用'],[
 ['summary.json','制作成绩表、完成度与状态统计的首选；含准确率、题量、状态数量、格式合规率和更新时间。'],
 ['results.csv','逐题查看题干、选项、参考答案、预测答案、对错、状态、最终回答和耗时。'],
 ['scored_results.jsonl','当前评分版本下各题最近一次作答结果；用于复核、分析和重新汇总。'],
 ['attempts.jsonl','追加保留每次调用记录，包括重试、原始最终回答和思考。历史评分字段不随离线重算改写。'],
 ['config.json','生成配置、模型摘要、量化信息、Ollama 版本、模板及题目/答案文件哈希，支持复现与续跑校验。'],
 ['logs 下的服务日志','排查加载、服务或连接问题；不作为答题成绩来源。']],[1.72,5.34])
h('11 已有记录如何复用')
p('同一命令再次运行时，按 sample_id 读取最近一次尝试，并按当前评分规则重算。ok、invalid、truncated 都跳过；只有 error 会重新请求模型。重试前后的调用都留在 attempts.jsonl，成绩按每题最近记录统计，不将重试次数重复计入题数。')
p('Ctrl+C 会保留已落盘的题目。正常结束会清除 .running.lock，并尝试卸载内存中的模型。强制终止可能留下锁文件或损坏的 JSONL 尾行，不能直接认定这些情况已经被自动恢复。')
h('12 三种常用入口')
code('python .\\evaluate_local.py --subject geography --check-only\npython .\\evaluate_local.py --subject history --model deepseek-r1:8b --limit 5\npython .\\evaluate_local.py --subject history --model deepseek-r1:8b --rescore-only')
p('依次用于只检查数据、前 5 题流程检查、离线重算已有结果。去掉 --limit 5 即选择全量；前 5 题按文件顺序选取，并非随机抽样。更换生成参数或思考模式时，使用 --output 指定新目录，不能混写已有实验。')

page('PPT 编排建议与交接核对')
h('13 建议使用六页讲清楚项目')
table(['页面','内容重点','建议呈现'],[
 ['1 目标与数据','三科题量、文本单选任务、地理筛选范围。','题量表及 1200 → 968 → 185 的地理筛选流程。'],
 ['2 脚本设计','输入答案隔离、每题独立调用、逐题保存。','读取 → 校验 → 调用 → 提取 → 评分 → 汇总流程图。'],
 ['3 状态与评分','ok 不等于答对；invalid、truncated、error 的区别。','状态对照表，加“B＋解析”可得分的例子。'],
 ['4 完整测评结果','仅比较 Qwen3 1.7B 与 DeepSeek-R1 8B 的三科完整结果。','每科两根柱，标注样本数和百分比。'],
 ['5 阶段进度与错误分析','Qwen3 8B 当前只测 378 题；展示不同状态数量。','阶段进度图和状态堆叠图，与完整排行榜分开。'],
 ['6 结论与后续','当前结果说明什么，哪些能力仍未覆盖。','列出答案复核、补齐 Qwen3 8B、统一协议后扩展 API。']],[1.12,3.22,2.72])
h('14 制作图表前需确认的事项')
p('使用同一版 scoring_protocol。当前为 politics-answer-extraction-v2，名称虽含 politics，但三科共享此解析规则。config.json 的 protocol 是生成实验标识，不要据其旧名称误认为仍采用“只能单字母得分”的旧规则。')
p('精确区分数值单位：准确率差值写“百分点”；wall_seconds_latest_attempts 是各题最近尝试的请求墙钟耗时之和，含加载或等待，未计入被替换的历史失败尝试；不是完整项目耗时，也不是纯生成耗时。输出 Token 统计包含思考。')
p('如要展示失败比例，可用 status_counts 中 invalid、truncated、error 分别除以 attempted_count；“答案可识别但答错”的数量为 ok 数量减 correct_count。当前汇总未实现题组宏平均、置信区间或显著性检验，不能写成已完成分析。')
p('部分题组共享材料，单题未必相互独立；公开题可能存在训练数据重叠。本次成绩只反映给定文本选择题与固定配置下的表现，不足以推断教学解释能力、通用学科能力或参数规模的因果效应。')
h('15 本文依据与更新方式')
p('主要依据：evaluate_local.py 的实际实现；data 中两类测评 JSONL 与历史、地理 manifest；原道德与法治清洗报告；results 各学科各模型目录的 config.json、summary.json 和 scored_results.jsonl。数值已核对逐题记录与汇总一致。')
p('更新 PPT 时先检查是否新增或续跑了结果，再更新样本数、正确数、状态数量与准确率。若评分规则变动，应对各模型原始输出统一重算，并保留评分版本；不能只更新一个模型的口径。')

d.core_properties.title='大模型学科测评项目交接说明'
d.core_properties.subject='脚本设计 状态说明 结果解读 PPT交接'
d.core_properties.author=''
for root in (d.element, d.styles.element):
    for border in root.xpath('.//w:pBdr'):
        border.getparent().remove(border)
d.save(OUT)
print(OUT)

