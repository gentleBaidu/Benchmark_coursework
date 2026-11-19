import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import evaluate_local as ev


class EvaluationTests(unittest.TestCase):
    def response(self, content='B', reason='stop'):
        return {'done': True, 'done_reason': reason,
                'message': {'content': content, 'thinking': 'A C D are alternatives'},
                'eval_count': 12}

    def test_scoring_ignores_thinking_and_rejects_truncation(self):
        self.assertTrue(ev.grade(self.response(' B\n'), 'B')['correct'])
        for output in ('A或B', '', '<think>B</think>'):
            self.assertEqual(ev.grade(self.response(output), 'B')['status'], 'invalid')
        self.assertFalse(ev.grade(self.response('B', 'length'), 'B')['correct'])
        self.assertEqual(ev.grade(self.response('B', 'length'), 'B')['status'], 'truncated')
        with self.assertRaises(ValueError):
            ev.grade({'done': False, 'message': {'content': 'B'}}, 'B')

    def test_explained_answers(self):
        examples = {
            'B. ①③': 'B',
            'C\n\n解析：A、B不正确，正确答案为C。': 'C',
            '答案是 B': 'B',
            '正确答案：**D**。解析：符合题意。': 'D',
            '分析：选项A不正确，B正确。\n因此，正确答案为B。': 'B',
            '应选C，因为其符合题意。': 'C',
            'b': 'B',
            'Ｂ': 'B',
            '<think>答案是A</think>\nB': 'B',
        }
        for text, expected in examples.items():
            with self.subTest(text=text):
                result = ev.grade(self.response(text), expected)
                self.assertTrue(result['correct'])
                self.assertEqual(result['prediction'], expected)
                self.assertFalse(result['format_compliant'])
        self.assertFalse(ev.grade(self.response('答案：C'), 'B')['correct'])

    def test_ambiguous_or_negated_answers(self):
        for text in ('答案是B或C', '答案：B / C', '答案：B，或者C',
                     '答案是B还是C都可以', 'B\n最终答案：C',
                     '答案：B。正确答案：D。', 'A\nB', '答案：AB',
                     'B不正确', '答案是B不正确', '答案不是B',
                     '选项A不正确，选项B正确。', '<think>答案是B'):
            with self.subTest(text=text):
                result = ev.grade(self.response(text), 'B')
                self.assertIsNone(result['prediction'])
                self.assertEqual(result['status'], 'invalid')

    def fixture(self, root):
        rows = [{'sample_id': f'T{i}', 'question': '选择正确选项',
                 'options': dict(A='甲', B='乙', C='丙', D='丁')} for i in range(2)]
        keys = [{'sample_id': f'T{i}', 'answer': 'B', 'private_marker': 'NEVER_SEND'}
                for i in range(2)]
        for name, data in [('inputs.jsonl', rows), ('answers.jsonl', keys)]:
            (root / name).write_text(''.join(json.dumps(x) + '\n' for x in data), encoding='utf-8')
        return SimpleNamespace(inputs=root / 'inputs.jsonl', answers=root / 'answers.jsonl',
                               model='qwen3:1.7b', output=root / 'results', limit=1,
                               base_url='http://127.0.0.1:11434', check_only=False,
                               think='on', temperature=0.0, seed=1, num_ctx=8192,
                               max_tokens=4096, timeout=60)

    def fake_api(self, calls, fail=False, digest='fixed-digest'):
        def fake(base, route, payload=None, timeout=60):
            if route == '/api/version':
                return {'version': 'test'}
            if route == '/api/tags':
                return {'models': [{'name': 'qwen3:1.7b', 'digest': digest}]}
            if route == '/api/show':
                return {'details': {'quantization_level': 'Q4_K_M'}}
            if route == '/api/chat':
                calls.append(payload)
                self.assertNotIn('NEVER_SEND', json.dumps(payload))
                self.assertEqual(len(payload['messages']), 1)
                if fail:
                    raise RuntimeError('simulated connection failure')
                return self.response()
            return {}
        return fake

    def test_resume_smoke_to_full_and_version_guard(self):
        with tempfile.TemporaryDirectory(dir=ev.ROOT) as tmp, contextlib.redirect_stdout(io.StringIO()):
            args = self.fixture(Path(tmp))
            calls = []
            with patch.object(ev, 'api', self.fake_api(calls)):
                self.assertEqual(ev.run(args), 0)
                args.limit = 0
                self.assertEqual(ev.run(args), 0)
                self.assertEqual(ev.run(args), 0)
            self.assertEqual(len(calls), 2)
            summary = json.loads((args.output / 'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['accuracy'], 1.0)
            self.assertTrue(summary['full_dataset'])
            self.assertTrue(summary['complete_without_api_errors'])
            with patch.object(ev, 'api', self.fake_api(calls, digest='new-digest')):
                with self.assertRaisesRegex(ValueError, '版本'):
                    ev.run(args)
            self.assertFalse((args.output / '.running.lock').exists())

    def test_errors_can_resume_without_double_counting(self):
        with tempfile.TemporaryDirectory(dir=ev.ROOT) as tmp, contextlib.redirect_stdout(io.StringIO()):
            args = self.fixture(Path(tmp))
            with patch.object(ev, 'api', self.fake_api([], fail=True)):
                self.assertEqual(ev.run(args), 2)
            summary = json.loads((args.output / 'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['accuracy'], 0)
            with patch.object(ev, 'api', self.fake_api([])):
                self.assertEqual(ev.run(args), 0)
            self.assertEqual(len(ev.read_jsonl(args.output / 'attempts.jsonl')), 2)
            summary = json.loads((args.output / 'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['attempted_count'], 1)
            self.assertEqual(summary['correct_count'], 1)

    def test_offline_rescore_old_results_preserves_raw_history(self):
        with tempfile.TemporaryDirectory(dir=ev.ROOT) as tmp, contextlib.redirect_stdout(io.StringIO()):
            args = self.fixture(Path(tmp))
            with patch.object(ev, 'api', self.fake_api([])):
                ev.run(args)
            history = args.output / 'attempts.jsonl'
            record = ev.read_jsonl(history)[0]
            record.update(response='B. ①③', prediction=None, correct=False, status='invalid')
            history.write_text(json.dumps(record) + '\n', encoding='utf-8')
            original = history.read_bytes()
            args.rescore_only = True
            with patch.object(ev, 'api', side_effect=AssertionError('离线重算不得调用模型')):
                self.assertEqual(ev.run(args), 0)
            summary = json.loads((args.output / 'summary.json').read_text(encoding='utf-8'))
            self.assertEqual(summary['correct_count'], 1)
            self.assertEqual(summary['format_compliant_count'], 0)
            self.assertEqual(history.read_bytes(), original)
            self.assertTrue(ev.read_jsonl(args.output / 'scored_results.jsonl')[0]['correct'])
            args.rescore_only = False
            resumed_calls = []
            with patch.object(ev, 'api', self.fake_api(resumed_calls)):
                self.assertEqual(ev.run(args), 0)
                self.assertEqual(resumed_calls, [])

    def test_reject_answers_in_input(self):
        with tempfile.TemporaryDirectory(dir=ev.ROOT) as tmp:
            args = self.fixture(Path(tmp))
            rows = ev.read_jsonl(args.inputs)
            rows[0]['answer'] = 'B'
            args.inputs.write_text('\n'.join(json.dumps(x) for x in rows), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '禁止混入答案'):
                ev.load_dataset(args.inputs, args.answers)

    def test_dataset_id_mismatch(self):
        with tempfile.TemporaryDirectory(dir=ev.ROOT) as tmp:
            args = self.fixture(Path(tmp))
            args.answers.write_text('{"sample_id": "wrong", "answer": "B"}\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '不完全匹配'):
                ev.load_dataset(args.inputs, args.answers)

    def test_history_prompt_and_output_are_separate(self):
        with tempfile.TemporaryDirectory(dir=ev.ROOT) as tmp, contextlib.redirect_stdout(io.StringIO()):
            args = self.fixture(Path(tmp))
            row = ev.read_jsonl(args.inputs)[0]
            self.assertIn('道德与法治', ev.make_payload(row, args)['messages'][0]['content'])
            args.subject = 'history'
            self.assertIn('初中历史', ev.make_payload(row, args)['messages'][0]['content'])
            self.assertNotIn('道德与法治', ev.make_payload(row, args)['messages'][0]['content'])
            with patch.object(ev, 'api', self.fake_api([])):
                self.assertEqual(ev.run(args), 0)
            config = json.loads((args.output / 'config.json').read_text(encoding='utf-8'))
            self.assertEqual(config['subject'], '历史')
            self.assertEqual(config['protocol'], 'history-local-v1')
            args.output = None
            history_folder = ev.result_folder(args)
            args.subject = 'politics'
            self.assertNotEqual(history_folder, ev.result_folder(args))
            self.assertEqual(history_folder.parent.name, '历史')
            self.assertEqual(history_folder.name, 'qwen3_1.7b')
            self.assertEqual(ev.result_folder(args).parent.name, '道德与法治')

    def test_geography_prompt_output_and_saved_config(self):
        with tempfile.TemporaryDirectory(dir=ev.ROOT) as tmp, contextlib.redirect_stdout(io.StringIO()):
            args = self.fixture(Path(tmp))
            args.subject = 'geography'
            row = ev.read_jsonl(args.inputs)[0]
            payload = ev.make_payload(row, args)
            self.assertIn('初中地理', payload['messages'][0]['content'])
            self.assertNotIn('历史', payload['messages'][0]['content'])
            with patch.object(ev, 'api', self.fake_api([])):
                self.assertEqual(ev.run(args), 0)
            config = json.loads((args.output / 'config.json').read_text(encoding='utf-8'))
            self.assertEqual(config['subject'], '地理')
            self.assertEqual(config['protocol'], 'geography-local-v1')
            args.output = None
            geography_folder = ev.result_folder(args)
            self.assertEqual(geography_folder.parent.name, '地理')
            self.assertEqual(geography_folder.name, 'qwen3_1.7b')
            args.subject = 'history'
            self.assertNotEqual(geography_folder, ev.result_folder(args))

    def test_thinking_mode_change_cannot_mix_existing_results(self):
        with tempfile.TemporaryDirectory(dir=ev.ROOT) as tmp, contextlib.redirect_stdout(io.StringIO()):
            args = self.fixture(Path(tmp))
            with patch.object(ev, 'api', self.fake_api([])):
                ev.run(args)
                args.think = 'off'
                with self.assertRaisesRegex(ValueError, '参数已变化'):
                    ev.run(args)


if __name__ == '__main__':
    unittest.main()
