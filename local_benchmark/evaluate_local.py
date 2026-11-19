"""Ollama 单模型选择题测评。仅使用 Python 标准库，支持断点续跑。"""
import argparse
import collections
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
PROMPT = '请回答以下初中道德与法治单项选择题。最终回答仅输出一个大写字母A、B、C或D，不要附加解释。'
PROTOCOL = 'politics-local-v1-strict-letter'
# 保留原生成协议以兼容已完成作答；评分版本独立记录，可离线重算。
SCORING_PROTOCOL = 'politics-answer-extraction-v2'
SUBJECT_LABELS = {'politics': '道德与法治', 'history': '历史', 'geography': '地理'}


def subject_prompt(args):
    subject = getattr(args, 'subject', 'politics')
    if subject == 'politics':
        return PROMPT
    return f'请回答以下初中{SUBJECT_LABELS[subject]}单项选择题。最终回答仅输出一个大写字母A、B、C或D，不要附加解释。'


def extract_answer(content):
    """仅识别最终回答中的明确作答，不参考标准答案，不搜索思考内容。"""
    text = re.sub(r'<think>.*?</think>', '', content, flags=re.S | re.I).strip()
    if re.search(r'</?think\b', text, flags=re.I):
        return None, 'unfinished_thinking_block'
    text = text.translate(str.maketrans('ＡＢＣＤａｂｃｄ', 'ABCDabcd'))
    text = text.replace('**', '').replace('`', '').strip()
    if re.fullmatch(r'[A-Da-d]', text):
        return text.upper(), 'single_letter'
    candidates = []
    # 开头的选项标记：B. ①③、C 换行后附解析等。
    leading = re.match(r'^([A-Da-d])(?=$|[\s.．、:：。)）])', text)
    if leading:
        candidates.append(leading)
    # 只识别句首/分句首的明确结论，不把“选项 A 不正确”当成答案。
    conclusion = re.compile(
        r'(?:^|[\n。！？；;，,])\s*'
        r'(?:(?:因此|所以|故|综上所述|综上)[，,]?\s*)?'
        r'(?:最终答案|正确答案|答案|正确选项|最终选择|本题选|应选择|应选|选择|选)'
        r'\s*(?:为|是)?\s*[:：]?\s*(?:选项\s*)?'
        r'([A-Da-d])(?![A-Za-z0-9])'
    )
    candidates.extend(conclusion.finditer(text))
    # 多行单独给出不同字母属于冲突，也不能取第一个字母。
    candidates.extend(re.finditer(r'^\s*([A-Da-d])\s*$', text, flags=re.M))
    if not candidates:
        return None, 'no_clear_answer'
    for match in candidates:
        tail = text[match.end(1):]
        if re.match(r'\s*(?:[、,，/&]\s*(?:或(?:者)?\s*)?|或(?:者)?|还是|和|及|与)'
                    r'\s*(?:选项\s*)?[A-Da-d](?![A-Za-z0-9])', tail):
            return None, 'multiple_options'
        if re.match(r'\s*(?:选项)?\s*(?:不正确|不对|错误|不是|不确定|不一定|可能不)', tail):
            return None, 'negated_or_uncertain_answer'
    letters = {match.group(1).upper() for match in candidates}
    if len(letters) != 1:
        return None, 'conflicting_answers'
    return letters.pop(), 'explicit_answer'


def score_answer(content, expected, done_reason):
    prediction, method = extract_answer(content)
    status = ('truncated' if done_reason == 'length'
              else 'ok' if prediction else 'invalid')
    return {'prediction': prediction, 'correct': status == 'ok' and prediction == expected,
            'status': status, 'format_compliant': bool(re.fullmatch(r'[ABCD]', content.strip())),
            'parse_method': method, 'scoring_protocol': SCORING_PROTOCOL}


def rescore_record(record, expected):
    if record['status'] == 'error':
        return {**record, 'expected': expected, 'correct': False, 'prediction': None,
                'format_compliant': False, 'parse_method': 'api_error',
                'scoring_protocol': SCORING_PROTOCOL}
    return {**record, 'expected': expected,
            **score_answer(record['response'], expected, record.get('done_reason'))}


def read_jsonl(path):
    rows = []
    with path.open(encoding='utf-8-sig') as f:
        for number, line in enumerate(f, 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f'{path} 第 {number} 行不是有效 JSON；请检查文件。') from exc
    return rows


def load_dataset(inputs_path, answers_path):
    rows, answers = read_jsonl(inputs_path), read_jsonl(answers_path)
    keys = {x['sample_id']: x for x in answers}
    ids = [x['sample_id'] for x in rows]
    if not rows or len(set(ids)) != len(ids) or len(keys) != len(answers):
        raise ValueError('数据为空，或 sample_id 重复。')
    if set(ids) != set(keys):
        raise ValueError('题目和答案的 sample_id 不完全匹配。')
    for row in rows:
        if set(row) != {'sample_id', 'question', 'options'}:
            raise ValueError('输入文件只能包含 sample_id、question、options，禁止混入答案。')
        if not isinstance(row['question'], str) or not row['question'].strip():
            raise ValueError(f"题干为空：{row['sample_id']}")
        if set(row['options']) != set('ABCD') or any(
            not isinstance(v, str) or not v.strip() for v in row['options'].values()
        ):
            raise ValueError(f"选项不完整：{row['sample_id']}")
        if keys[row['sample_id']]['answer'] not in list('ABCD'):
            raise ValueError(f"答案不合法：{row['sample_id']}")
    return rows, keys


def make_payload(row, args):
    # 只将白名单字段组成提示词；答案表不会传入此函数。
    question = row['question'] + '\n' + '\n'.join(
        f'{letter}. {row["options"][letter]}' for letter in 'ABCD'
    )
    return {
        'model': args.model,
        'messages': [{'role': 'user', 'content': subject_prompt(args) + '\n' + question}],
        'stream': False,
        'think': args.think == 'on',
        'keep_alive': '10m',
        'options': {'temperature': args.temperature, 'seed': args.seed,
                    'num_ctx': args.num_ctx, 'num_predict': args.max_tokens},
    }


def api(base_url, route, payload=None, timeout=60):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
    request = Request(base_url.rstrip('/') + route, data=body,
                      headers={'Content-Type': 'application/json'})
    # 本地请求不经过系统代理。
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=timeout) as response:
            result = json.load(response)
    except HTTPError as exc:
        detail = exc.read().decode('utf-8', errors='replace')[:1000]
        raise RuntimeError(f'Ollama HTTP {exc.code}: {detail}') from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f'无法连接 Ollama 或请求超时：{exc}。请确认 Ollama 已启动。') from exc
    if result.get('error'):
        raise RuntimeError(str(result['error']))
    return result


def grade(response, expected):
    message = response.get('message')
    if response.get('done') is not True or not isinstance(message, dict):
        raise ValueError('Ollama 返回了不完整的响应。')
    content = message.get('content', '')
    if not isinstance(content, str):
        raise ValueError('Ollama 返回的最终回答不是文本。')
    return {**score_answer(content, expected, response.get('done_reason')),
            'response': content, 'thinking': message.get('thinking', ''),
            'done_reason': response.get('done_reason'),
            'prompt_tokens': response.get('prompt_eval_count'),
            'output_tokens': response.get('eval_count'),
            'total_duration_ns': response.get('total_duration'),
            'load_duration_ns': response.get('load_duration'),
            'eval_duration_ns': response.get('eval_duration')}


def write_json(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def export_results(folder, selected, records, config):
    current = [records[r['sample_id']] for r in selected if r['sample_id'] in records]
    counts = collections.Counter(r['status'] for r in current)
    correct = sum(r['correct'] for r in current)
    attempted = len(current)
    complete = attempted == len(selected) and counts['error'] == 0
    format_count = sum(bool(r.get('format_compliant')) for r in current)
    summary = {
        'model': config['model'], 'think': config['think'],
        'subject': config.get('subject', '道德与法治'),
        'scoring_protocol': SCORING_PROTOCOL,
        'selected_count': len(selected), 'attempted_count': attempted,
        'pending_count': len(selected) - attempted, 'correct_count': correct,
        'status_counts': dict(counts),
        'accuracy': correct / attempted if attempted else None,
        'format_compliant_count': format_count,
        'format_compliance_rate': format_count / attempted if attempted else None,
        'accuracy_denominator': 'attempted_count; invalid/truncated/error count as incorrect',
        'full_dataset': len(selected) == config['dataset_count'],
        'complete_without_api_errors': complete,
        'wall_seconds_latest_attempts': sum(r['wall_seconds'] for r in current),
        'output_tokens_latest_attempts': sum(r.get('output_tokens') or 0 for r in current),
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'note': '允许附带解析的明确单选答案；内容准确率与格式合规率分开统计。候选答案未经专家逐题核验。',
    }
    write_json(folder / 'summary.json', summary)
    fields = ['sample_id', 'question', 'A', 'B', 'C', 'D', 'expected', 'prediction',
              'correct', 'status', 'format_compliant', 'parse_method', 'response', 'wall_seconds', 'prompt_tokens',
              'output_tokens', 'error']
    temp = folder / 'results.csv.tmp'
    with temp.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        for row in selected:
            if row['sample_id'] in records:
                writer.writerow({**records[row['sample_id']], 'question': row['question'],
                                 **row['options']})
    temp.replace(folder / 'results.csv')
    scored_temp = folder / 'scored_results.jsonl.tmp'
    scored_temp.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in current), encoding='utf-8')
    scored_temp.replace(folder / 'scored_results.jsonl')
    return summary


def result_folder(args):
    name = re.sub(r'[^a-zA-Z0-9_.-]', '_', args.model)
    subject = getattr(args, 'subject', 'politics')
    return args.output or ROOT / 'results' / SUBJECT_LABELS[subject] / name


def rescore_saved(args, rows, selected, keys):
    """离线重算，保留 attempts.jsonl 和生成配置，不需要 Ollama。"""
    folder = result_folder(args)
    config = json.loads((folder / 'config.json').read_text(encoding='utf-8'))
    checks = {'inputs_sha256': hashlib.sha256(args.inputs.read_bytes()).hexdigest(),
              'answers_sha256': hashlib.sha256(args.answers.read_bytes()).hexdigest(),
              'model': args.model, 'think': args.think, 'dataset_count': len(rows)}
    if any(config.get(k) != v for k, v in checks.items()):
        raise ValueError('历史配置与当前模型名、模式或数据不符，不能重算。')
    lock_path = folder / '.running.lock'
    try:
        lock = lock_path.open('x', encoding='utf-8')
    except FileExistsError as exc:
        raise ValueError('该结果目录正在使用，不能同时重算。') from exc
    try:
        with lock:
            lock.write(str(os.getpid()))
        records = {}
        for record in read_jsonl(folder / 'attempts.jsonl'):
            sid = record['sample_id']
            records[sid] = rescore_record(record, keys[sid]['answer'])
        summary = export_results(folder, selected, records, config)
        score = f"{summary['accuracy']:.2%}" if summary['accuracy'] is not None else '暂无'
        print(f"离线重算完成：已作答 {summary['attempted_count']}/{len(selected)}，"
              f"正确 {summary['correct_count']}，内容准确率 {score}；"
              f"仅字母格式合规 {summary['format_compliant_count']} 题。\n结果：{folder}")
        return 0
    finally:
        lock_path.unlink(missing_ok=True)


def run(args):
    rows, keys = load_dataset(args.inputs, args.answers)
    selected = rows[:args.limit] if args.limit else rows
    print(f'数据检查通过：共 {len(rows)} 题，本次选择 {len(selected)} 题。', flush=True)
    if args.check_only:
        print('仅检查数据，未连接 Ollama，未下载模型。')
        return 0
    if getattr(args, 'rescore_only', False):
        return rescore_saved(args, rows, selected, keys)
    versions = api(args.base_url, '/api/version')
    tags = api(args.base_url, '/api/tags').get('models', [])
    tag = next((m for m in tags if args.model in (m.get('name'), m.get('model'))), None)
    if not tag or not tag.get('digest'):
        raise ValueError(f'未找到本地模型 {args.model}，请先运行：ollama pull {args.model}')
    show = api(args.base_url, '/api/show', {'model': args.model})
    supported = show.get('thinking', {}).get('values')
    if supported is not None and (args.think == 'on') not in supported:
        raise ValueError(f'模型不支持 think={args.think}，支持的值为 {supported}。')
    config = {
        'protocol': PROTOCOL, 'model': args.model, 'digest': tag['digest'],
        'ollama_version': versions.get('version'), 'details': show.get('details'),
        'model_parameters': show.get('parameters'), 'model_template': show.get('template'),
        'think': args.think, 'temperature': args.temperature, 'seed': args.seed,
        'num_ctx': args.num_ctx, 'max_tokens': args.max_tokens, 'prompt': subject_prompt(args),
        'dataset_count': len(rows),
        'inputs_sha256': hashlib.sha256(args.inputs.read_bytes()).hexdigest(),
        'answers_sha256': hashlib.sha256(args.answers.read_bytes()).hexdigest(),
    }
    subject = getattr(args, 'subject', 'politics')
    if subject != 'politics':
        config.update(subject=SUBJECT_LABELS[subject], protocol=f'{subject}-local-v1')
    folder = result_folder(args)
    folder.mkdir(parents=True, exist_ok=True)
    # 避免两个终端同时写入同一份结果。正常退出和 Ctrl+C 都会清除锁。
    lock_path = folder / '.running.lock'
    try:
        lock = lock_path.open('x', encoding='utf-8')
    except FileExistsError as exc:
        raise ValueError(f'结果目录已锁定：{lock_path}。若上次进程异常退出，请确认没有测评运行后删除此锁文件。') from exc
    try:
        with lock:
            lock.write(str(os.getpid()))
        config_path = folder / 'config.json'
        if config_path.exists():
            if json.loads(config_path.read_text(encoding='utf-8')) != config:
                raise ValueError('模型版本、Ollama 版本、数据或参数已变化，请用 --output 指定新的结果目录。')
        else:
            if (folder / 'attempts.jsonl').exists():
                raise ValueError('结果目录有历史作答但缺少 config.json，请指定新的 --output 目录。')
            write_json(config_path, config)
        history = folder / 'attempts.jsonl'
        records = {}
        if history.exists():
            for record in read_jsonl(history):
                sid = record['sample_id']
                records[sid] = rescore_record(record, keys[sid]['answer'])
            # 完整 JSON 行末缺换行时，防止下一条记录被直接拼接。
            if history.stat().st_size:
                with history.open('rb') as f:
                    f.seek(-1, 2)
                    trailing_newline = f.read(1) == b'\n'
                if not trailing_newline:
                    with history.open('ab') as f:
                        f.write(b'\n')
        consecutive_errors = 0
        interrupted = False
        print(f'模型：{args.model} | 思考：{args.think} | 结果：{folder}', flush=True)
        try:
            with history.open('a', encoding='utf-8') as f:
                for index, row in enumerate(selected, 1):
                    sid = row['sample_id']
                    if sid in records and records[sid]['status'] != 'error':
                        continue
                    print(f'[{index}/{len(selected)}] {sid} 正在作答……', flush=True)
                    started = time.perf_counter()
                    record = {'sample_id': sid, 'expected': keys[sid]['answer'],
                              'timestamp': datetime.now(timezone.utc).isoformat()}
                    try:
                        result = api(args.base_url, '/api/chat', make_payload(row, args), args.timeout)
                        record.update(grade(result, keys[sid]['answer']))
                        consecutive_errors = 0
                    except (RuntimeError, ValueError) as exc:
                        record.update(status='error', correct=False, prediction=None,
                                      response='', error=str(exc), format_compliant=False,
                                      parse_method='api_error', scoring_protocol=SCORING_PROTOCOL)
                        consecutive_errors += 1
                    record['wall_seconds'] = round(time.perf_counter() - started, 3)
                    f.write(json.dumps(record, ensure_ascii=False) + '\n')
                    f.flush()
                    os.fsync(f.fileno())
                    records[sid] = record
                    summary = export_results(folder, selected, records, config)
                    print(f"  预测={record['prediction'] or '-'} 标准={record['expected']} "
                          f"状态={record['status']} 累计准确率={summary['accuracy']:.2%}", flush=True)
                    if record.get('error'):
                        print('  ' + record['error'], flush=True)
                    if consecutive_errors >= 3:
                        print('连续 3 题接口错误，已停止。修复服务后运行同一命令可重试。')
                        break
        except KeyboardInterrupt:
            interrupted = True
            print('\n已中断；已完成的题目均已保存。')
        finally:
            summary = export_results(folder, selected, records, config)
            try:
                api(args.base_url, '/api/generate', {'model': args.model, 'keep_alive': 0}, timeout=10)
            except (RuntimeError, ValueError):
                pass
        score = f"{summary['accuracy']:.2%}" if summary['accuracy'] is not None else '暂无'
        print(f"已作答 {summary['attempted_count']}/{summary['selected_count']}，"
              f"正确 {summary['correct_count']}，准确率 {score}。\n汇总：{folder / 'summary.json'}")
        return 130 if interrupted else 0 if summary['complete_without_api_errors'] else 2
    finally:
        lock_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', default='qwen3:1.7b', help='一次只评测一个已下载的 Ollama 模型')
    parser.add_argument('--subject', choices=list(SUBJECT_LABELS), default='politics',
                        help='politics=道德与法治（默认）；history=历史；geography=地理，各科结果独立')
    parser.add_argument('--inputs', type=Path, help='覆盖所选科目的默认题目文件')
    parser.add_argument('--answers', type=Path, help='覆盖所选科目的默认答案文件')
    parser.add_argument('--output', type=Path, help='结果目录；参数改变时请使用新目录')
    parser.add_argument('--base-url', default='http://127.0.0.1:11434')
    parser.add_argument('--limit', type=int, default=0, help='仅测前 N 题；默认 0 为全量')
    parser.add_argument('--think', choices=['on', 'off'], default='on')
    parser.add_argument('--max-tokens', type=int, default=4096, help='思考和最终回答的总输出上限')
    parser.add_argument('--num-ctx', type=int, default=8192)
    parser.add_argument('--temperature', type=float, default=0.0)
    parser.add_argument('--seed', type=int, default=20261109)
    parser.add_argument('--timeout', type=float, default=900, help='每题请求超时秒数')
    parser.add_argument('--check-only', action='store_true', help='只检查题目和答案，不连接模型')
    parser.add_argument('--rescore-only', action='store_true', help='按新规则离线重算已有输出，不调用模型')
    args = parser.parse_args()
    subject_data = DATA if args.subject == 'politics' else DATA / args.subject
    args.inputs = args.inputs or subject_data / 'evaluation_inputs.jsonl'
    args.answers = args.answers or subject_data / 'answer_key.jsonl'
    if args.limit < 0 or args.max_tokens < 1 or args.num_ctx <= args.max_tokens or args.timeout <= 0:
        parser.error('limit 必须非负；max-tokens 和 timeout 必须为正；num-ctx 必须大于 max-tokens。')
    try:
        return run(args)
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f'错误：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    sys.exit(main())
