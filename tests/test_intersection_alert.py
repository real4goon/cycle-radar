import ast
import copy
import json
import io
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import intersection_alert as alert

NOW = datetime(2026, 11, 3, 15, tzinfo=timezone.utc)


def fixture(fg=9, vix=28, rsi=30):
    return {"meta": {"updated_at_utc_iso": NOW.isoformat()}, "timing": {
        "fear_greed": {"score": fg, "available": True, "timestamp": NOW.isoformat()},
        "metrics": {"vix": vix, "spy_rsi14": rsi},
        "source_as_of": {"vix": NOW.isoformat(), "spy_rsi14": NOW.isoformat()}}}


class IntersectionTests(unittest.TestCase):
    def test_all_eight_combinations_and_inclusive_thresholds(self):
        for mask in range(8):
            d = fixture(9 if mask & 1 else 9.01, 28 if mask & 2 else 27.99, 30 if mask & 4 else 30.01)
            result = alert.evaluate(d, NOW)
            self.assertTrue(result["valid"])
            self.assertEqual(result["count"], mask.bit_count())
            self.assertEqual(result["active"], mask == 7)
            _, dispatch = alert.prepare(result, {"active": False}, "1", True)
            self.assertEqual(dispatch, mask == 7)

    def test_invalid_numbers_never_dispatch(self):
        for value in [None, "9", "", True, float('nan'), float('inf'), -1, 101]:
            for path in ["fg", "rsi"]:
                d = fixture()
                if path == "fg":
                    d["timing"]["fear_greed"]["score"] = value
                else:
                    d["timing"]["metrics"]["spy_rsi14"] = value
                self.assertFalse(alert.evaluate(d, NOW)["valid"], (value, path))
        for value in [None, True, 0, -1, float('inf'), 1001]:
            self.assertFalse(alert.evaluate(fixture(vix=value), NOW)["valid"])

    def test_each_missing_or_stale_timestamp_blocks(self):
        for group, key, hours in [("meta", "updated_at_utc_iso", 2), ("fear_greed", "timestamp", 36),
                                  ("source_as_of", "vix", 36), ("source_as_of", "spy_rsi14", 36)]:
            for value in [None, "bad", "2026-11-03", (NOW-timedelta(hours=hours, seconds=1)).isoformat(),
                          (NOW+timedelta(minutes=6)).isoformat()]:
                d = fixture()
                target = d["meta"] if group == "meta" else d["timing"][group]
                target[key] = value
                self.assertFalse(alert.evaluate(d, NOW)["valid"], (group, key, value))

    def test_timestamp_formats_and_boundaries(self):
        for value in [NOW.isoformat(), NOW.isoformat().replace('+00:00', 'Z'), NOW.timestamp(), NOW.timestamp()*1000]:
            self.assertTrue(alert.fresh(value, NOW, 36))
        self.assertTrue(alert.fresh((NOW-timedelta(hours=36)).isoformat(), NOW, 36))
        self.assertFalse(alert.fresh((NOW-timedelta(hours=36, seconds=1)).isoformat(), NOW, 36))

    def test_collector_source_time_uses_oldest_input(self):
        # Execute the actual helper without importing network collection dependencies.
        tree = ast.parse((ROOT / 'scripts/update_data.py').read_text(encoding='utf-8'))
        fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'oldest_source_time')
        namespace = {'timestamp': alert.timestamp}
        exec(compile(ast.Module(body=[fn], type_ignores=[]), '<collector helper>', 'exec'), namespace)
        helper = namespace['oldest_source_time']
        self.assertEqual(helper('2026-11-03T00:00:00-05:00', '2026-11-03T04:00:00+00:00'), '2026-11-03T04:00:00+00:00')
        self.assertIsNone(helper('invalid', NOW.isoformat()))
        self.assertEqual(helper(NOW.isoformat()), NOW.isoformat())

    def test_unavailable_fear_greed(self):
        d = fixture()
        d['timing']['fear_greed']['available'] = False
        self.assertFalse(alert.evaluate(d, NOW)['valid'])

    def test_persist_release_and_reentry(self):
        active = alert.evaluate(fixture(), NOW)
        state, dispatch = alert.prepare(active, {"version": 1, "active": False}, "run1", True)
        self.assertTrue(dispatch)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            alert.atomic_write(path, state)
            state = alert.load_state(path)
        state, dispatch = alert.prepare(active, state, "run2", True)
        self.assertFalse(dispatch)
        # Missing data cannot manufacture a release.
        invalid = alert.evaluate(fixture(fg=None), NOW)
        state2, dispatch = alert.prepare(invalid, state, "run3", True)
        self.assertEqual(state2, state)
        self.assertFalse(dispatch)
        release = alert.evaluate(fixture(fg=10), NOW)
        state, dispatch = alert.prepare(release, state, "run4", True)
        self.assertFalse(state['active'])
        state, dispatch = alert.prepare(active, state, "run5", True)
        self.assertTrue(dispatch)

    def test_missing_secrets_does_not_consume_entry(self):
        result = alert.evaluate(fixture(), NOW)
        state, dispatch = alert.prepare(result, {"active": False}, "run1", False)
        self.assertFalse(dispatch)
        _, dispatch = alert.prepare(result, state, "run2", True)
        self.assertTrue(dispatch)

    def test_mock_delivery_and_ownership(self):
        result = alert.evaluate(fixture(), NOW)
        state, _ = alert.prepare(result, {"active": False}, "run1", True)
        messages = []
        sent = alert.deliver(result, state, "run1", messages.append)
        self.assertEqual(len(messages), 1)
        self.assertIn('3/3', messages[0])
        self.assertEqual(sent['delivery'], 'sent')
        with self.assertRaises(RuntimeError):
            alert.deliver(result, sent, "run1", messages.append)
        with self.assertRaises(RuntimeError):
            alert.deliver(result, state, "run2", messages.append)
        with self.assertRaises(RuntimeError):
            alert.deliver(alert.evaluate(fixture(fg=10), NOW), state, "run1", messages.append)

    def test_delivery_failure_preserves_reservation_no_retry(self):
        result = alert.evaluate(fixture(), NOW)
        state, _ = alert.prepare(result, {"active": False}, "run1", True)
        def fail(_):
            raise RuntimeError('timeout')
        with self.assertRaises(RuntimeError):
            alert.deliver(result, state, 'run1', fail)
        _, dispatch = alert.prepare(result, state, 'rerun', True)
        self.assertFalse(dispatch)

    def test_token_is_not_in_error(self):
        with patch.dict('os.environ', {'TELEGRAM_BOT_TOKEN': 'SECRET_TOKEN', 'TELEGRAM_CHAT_ID': 'test'}):
            with patch.object(alert, 'urlopen', side_effect=Exception('https://api.telegram.org/botSECRET_TOKEN/')) as post:
                with self.assertRaises(RuntimeError) as error:
                    alert.telegram_send('test')
                self.assertNotIn('SECRET_TOKEN', str(error.exception))
                self.assertEqual(post.call_count, 1)

    def test_corrupt_state_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            for value in ['bad', '{}', '{"version":1,"active":"false"}']:
                path.write_text(value)
                with self.assertRaises((ValueError, json.JSONDecodeError)):
                    alert.load_state(path)

    def test_cli_prepare_commit_boundary_and_mock_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data').mkdir()
            state_path = root / 'data/telegram_state.json'
            output = root / 'github-output'
            current = datetime.now(timezone.utc).isoformat()
            dashboard = fixture()
            dashboard['meta']['updated_at_utc_iso'] = current
            dashboard['timing']['fear_greed']['timestamp'] = current
            dashboard['timing']['source_as_of'] = {'vix': current, 'spy_rsi14': current}
            alert.atomic_write(root / 'data/dashboard.json', dashboard)
            env = {'ALERT_ATTEMPT': 'first', 'GITHUB_OUTPUT': str(output),
                   'TELEGRAM_BOT_TOKEN': 'TEST_ONLY', 'TELEGRAM_CHAT_ID': 'test'}
            with patch.object(alert, 'ROOT', root), patch.object(alert, 'STATE', state_path), patch.dict('os.environ', env):
                with patch.object(sys, 'argv', ['alert', 'prepare']), patch.object(alert, 'urlopen') as network:
                    alert.main()
                    network.assert_not_called()
                self.assertIn('should_send=true', output.read_text())
                self.assertEqual(alert.load_state(state_path)['delivery'], 'reserved')
                # Represents the workflow boundary: reservation persisted before dispatch.
                with patch.object(sys, 'argv', ['alert', 'deliver']), patch.object(alert, 'urlopen', return_value=io.BytesIO(b'{"ok":true,"result":{"message_id":1}}')) as network:
                    alert.main()
                    self.assertEqual(network.call_count, 1)
                self.assertEqual(alert.load_state(state_path)['delivery'], 'sent')
                with patch.object(sys, 'argv', ['alert', 'prepare']), patch.dict('os.environ', {'ALERT_ATTEMPT': 'second'}):
                    alert.main()
                self.assertTrue(output.read_text().endswith('should_send=false\n'))

    def test_workflow_orders_reservation_before_send(self):
        workflow = (ROOT / '.github/workflows/update-data.yml').read_text(encoding='utf-8')
        self.assertLess(workflow.index('intersection_alert.py prepare'), workflow.index('git push origin HEAD:main'))
        self.assertLess(workflow.index('git push origin HEAD:main'), workflow.index('intersection_alert.py deliver'))
        self.assertIn('cancel-in-progress: false', workflow)
        self.assertIn('data/telegram_state.json', workflow)
        collector = (ROOT / 'scripts/update_data.py').read_text(encoding='utf-8')
        self.assertNotIn('telegram_send', collector)
        ast.parse(collector)
        for filename in ['index.html', 'app.js']:
            source = (ROOT / filename).read_text(encoding='utf-8')
            self.assertNotIn('TELEGRAM_BOT_TOKEN', source)
            self.assertNotIn('api.telegram.org', source)


if __name__ == '__main__':
    unittest.main()
