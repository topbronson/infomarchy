import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]

class HostsTests(unittest.TestCase):
    def load(self):
        path = ROOT / 'hardware-hosts.py'
        self.assertTrue(path.exists(), 'independent hardware worker required')
        spec = importlib.util.spec_from_file_location('hosts', path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_explicit_hosts_reject_shell_and_option_injection(self):
        h = self.load()
        valid = {'id': 'spark', 'label': 'Spark', 'address': '100.110.189.112', 'user': 'top-bronson'}
        self.assertEqual(h.parse_hosts({'hosts': [valid]}), [valid])
        for key, value in [('address', '-oProxyCommand=evil'), ('address', 'host;id'), ('user', '$(id)'), ('id', '../escape')]:
            with self.assertRaises(ValueError):
                h.parse_hosts({'hosts': [{**valid, key: value}]})
        argv = h.ssh_command(valid)
        self.assertIn('StrictHostKeyChecking=yes', argv)
        self.assertIn('BatchMode=yes', argv)
        self.assertEqual(argv[-2:], ['python3', '-'])

    def test_last_good_survives_failure_and_ages(self):
        h = self.load()
        self.assertTrue(hasattr(h, 'HostState'), 'host state required')
        state = h.HostState({'id': 'local', 'label': 'Local'})
        self.assertEqual(state.view(100)['status'], 'collecting')
        stats = {'cpu': {'pct': 10}, 'mem': {'used': 1, 'total': 2}, 'disks': [], 'gpus': []}
        state.update(stats, '', 100)
        self.assertEqual(state.view(101)['status'], 'online')
        self.assertEqual(state.view(140)['status'], 'stale')
        state.update(None, 'SSH failed', 145)
        view = state.view(146)
        self.assertEqual(view['status'], 'offline')
        self.assertEqual(view['stats'], stats)
        self.assertEqual(view['lastSuccess'], 100000)
        self.assertEqual(view['lastAttempt'], 145000)
        self.assertTrue(view['stale'])
        state.update(stats, '', 150)
        self.assertEqual(state.view(151)['status'], 'online')

    def test_commands_are_bounded_and_bad_payload_rejected(self):
        h = self.load()
        self.assertTrue(hasattr(h, 'run_bounded'), 'bounded subprocess required')
        with self.assertRaises(TimeoutError):
            h.run_bounded([sys.executable, '-c', 'import time; time.sleep(5)'], b'', timeout=0.05)
        with self.assertRaises(ValueError):
            h.run_bounded([sys.executable, '-c', 'print("x" * 200000)'], b'')
        self.assertEqual(h.run_bounded([sys.executable, '-c', 'print(42)'], b''), '42\n')
        for bad in [{}, {'cpu': {'pct': 'bad'}, 'mem': {}, 'disks': [], 'gpus': []}, {'cpu': {}, 'mem': {}, 'disks': [], 'gpus': [], 'sessions': []}]:
            with self.assertRaises(ValueError):
                h.validate_stats(bad)

    def test_watch_local_refresh_does_not_wait_for_remote(self):
        h = self.load()
        self.assertTrue(hasattr(h, 'Monitor'), 'parallel monitor required')
        import concurrent.futures
        stats = {'cpu': {'pct': 10}, 'mem': {'used': 1, 'total': 2}, 'disks': [], 'gpus': []}
        class Executor:
            def __init__(self):
                self.jobs = []
            def submit(self, fn, host):
                future = concurrent.futures.Future()
                self.jobs.append((host, future))
                return future
        executor = Executor()
        monitor = h.Monitor([{'id': 'slow', 'label': 'Slow'}], executor)
        monitor.tick(100)
        self.assertEqual(len(executor.jobs), 2)
        executor.jobs[0][1].set_result(stats)
        monitor.tick(101)
        rows = monitor.rows(101)
        self.assertEqual(rows[0]['status'], 'online')
        self.assertEqual(rows[1]['status'], 'collecting')
        monitor.tick(105)
        self.assertEqual(len(executor.jobs), 3)
        self.assertEqual(executor.jobs[-1][0]['id'], 'local')
        monitor.tick(106)
        self.assertEqual(len(executor.jobs), 3, 'one in-flight job per host')

if __name__ == '__main__':
    unittest.main()
