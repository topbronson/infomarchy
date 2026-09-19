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
        stats = {'cpu': {'pct': 10, 'load': 1.5, 'temp': 45.0}, 'mem': {'used': 1, 'total': 2}, 'disks': [], 'gpus': [], 'uptime': 100.0, 'net': {'dev': 'eth0', 'addr': '10.0.0.5', 'wan': None, 'rx': 100, 'tx': 200, 'wireless': False, 'ssid': None, 'signal': None}, 'ping': {'ok': True, 'ms': 5.0}, 'hostname': 'test-host'}
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
        good = {'cpu': {'pct': 10, 'load': 1.5, 'temp': 45.0}, 'mem': {'used': 1, 'total': 2}, 'disks': [], 'gpus': [], 'uptime': 100.0, 'net': {'dev': 'eth0', 'addr': '10.0.0.5', 'wan': None, 'rx': 100, 'tx': 200, 'wireless': False, 'ssid': None, 'signal': None}, 'ping': {'ok': True, 'ms': 5.0}, 'hostname': 'test-host'}
        self.assertIsNotNone(h.validate_stats(good), 'valid full stats must pass')
        for bad in [{}, {'cpu': {'pct': 'bad'}, 'mem': {}, 'disks': [], 'gpus': []}, {'cpu': {}, 'mem': {}, 'disks': [], 'gpus': [], 'sessions': []},
                    {**good, 'net': {'dev': 'eth0'}}, {**good, 'ping': {'ok': 'yes'}}]:
            with self.assertRaises(ValueError):
                h.validate_stats(bad)

    def test_watch_local_refresh_does_not_wait_for_remote(self):
        h = self.load()
        self.assertTrue(hasattr(h, 'Monitor'), 'parallel monitor required')
        import concurrent.futures
        stats = {'cpu': {'pct': 10, 'load': 1.5, 'temp': 45.0}, 'mem': {'used': 1, 'total': 2}, 'disks': [], 'gpus': [], 'uptime': 100.0, 'net': {'dev': 'eth0', 'addr': '10.0.0.5', 'wan': None, 'rx': 100, 'tx': 200, 'wireless': False, 'ssid': None, 'signal': None}, 'ping': {'ok': True, 'ms': 5.0}, 'hostname': 'test-host'}
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


    def test_gpu_carry_forward_on_bad_nvtop_tick(self):
        import importlib.util
        import pathlib
        spec = importlib.util.spec_from_file_location('hh', pathlib.Path(__file__).resolve().parents[1] / 'hardware-hosts.py')
        h = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(h)
        def stats_with_gpu(gpu):
            return {'cpu': {'pct': 10, 'load': 1.5, 'temp': 45.0}, 'mem': {'used': 1, 'total': 2}, 'disks': [],
                    'gpus': [gpu], 'uptime': 100.0,
                    'net': {'dev': 'eth0', 'addr': '10.0.0.5', 'wan': None, 'rx': 100, 'tx': 200, 'wireless': False, 'ssid': None, 'signal': None},
                    'ping': {'ok': True, 'ms': 5.0}, 'hostname': 'test-host'}
        good = {'id': '0000:04:00.0', 'name': 'Intel Arc Pro B70', 'driver': 'xe', 'util': 32.0, 'temp': 57.0, 'memUsed': 2000000000, 'memTotal': 34000000000}
        bad = {'id': '0000:04:00.0', 'name': 'Intel Arc Pro B70', 'driver': 'xe', 'util': None, 'temp': 58.0, 'memUsed': None, 'memTotal': None}
        state = h.HostState({'id': 'local', 'label': 'Local hardware'})
        state.update(stats_with_gpu(dict(good)), '', 100.0)
        state.update(stats_with_gpu(dict(bad)), '', 105.0)   # nvtop stumbled
        g = state.stats['gpus'][0]
        # Last-known VRAM/util carried forward; fresh temp kept.
        self.assertEqual(g['util'], 32.0)
        self.assertEqual(g['memUsed'], 2000000000)
        self.assertEqual(g['memTotal'], 34000000000)
        self.assertEqual(g['temp'], 58.0)

    def test_paused_hosts_skip_collection(self):
        import importlib.util
        import pathlib
        import tempfile
        import json
        spec = importlib.util.spec_from_file_location('hh', pathlib.Path(__file__).resolve().parents[1] / 'hardware-hosts.py')
        h = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(h)
        stats = {'cpu': {'pct': 10, 'load': 1.5, 'temp': 45.0}, 'mem': {'used': 1, 'total': 2}, 'disks': [], 'gpus': [], 'uptime': 100.0, 'net': {'dev': 'eth0', 'addr': '10.0.0.5', 'wan': None, 'rx': 100, 'tx': 200, 'wireless': False, 'ssid': None, 'signal': None}, 'ping': {'ok': True, 'ms': 5.0}, 'hostname': 'test-host'}
        with tempfile.TemporaryDirectory() as directory:
            paused_path = pathlib.Path(directory) / 'hardware-paused.json'
            paused_path.write_text(json.dumps([]))
            jobs = []

            class Executor:
                def submit(self, fn, host):
                    jobs.append(host['id'])
                    import concurrent.futures
                    f = concurrent.futures.Future()
                    f.set_result(stats)
                    return f

            monitor = h.Monitor([{'id': 'spark', 'label': 'Spark'}], Executor(), paused_path=str(paused_path))
            monitor.tick(100.0)   # submit local + spark (in flight)
            monitor.tick(105.0)   # harvest: both online
            self.assertEqual(monitor.rows(105.0)[1]['status'], 'online')
            # Pause spark: only local is collected; spark keeps last-good stats.
            paused_path.write_text(json.dumps(['spark']))
            monitor.tick(110.0)
            self.assertEqual(jobs[-1], 'local', 'paused host must not be re-collected')
            row = monitor.rows(110.0)[1]
            self.assertEqual(row['status'], 'paused')
            self.assertTrue(row['paused'])
            self.assertIsNotNone(row['stats'], 'last-good data retained while paused')
            # Resume: spark is collected, then harvested back to online.
            paused_path.write_text(json.dumps([]))
            monitor.tick(115.0)
            self.assertEqual(jobs[-1], 'spark', 'resumed host is collected again')
            monitor.tick(120.0)
            row = monitor.rows(120.0)[1]
            self.assertEqual(row['status'], 'online')
            self.assertFalse(row['paused'])

if __name__ == '__main__':
    unittest.main()
