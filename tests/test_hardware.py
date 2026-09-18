import importlib.util
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

class HardwareTests(unittest.TestCase):
    def load(self):
        path = ROOT / 'hardware-probe.py'
        self.assertTrue(path.exists(), 'hardware probe must exist')
        spec = importlib.util.spec_from_file_location('probe', path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_multiple_intel_cards_and_missing_counters(self):
        probe = self.load()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            for card, pci in [('card0', '0000:04:00.0'), ('card2', '0000:09:00.0'), ('card1', '0000:00:02.0')]:
                device = root / 'devices' / pci
                device.mkdir(parents=True)
                (device / 'vendor').write_text('0x8086')
                (device / 'device').write_text('0xe211')
                entry = root / 'class/drm' / card
                entry.mkdir(parents=True)
                (entry / 'device').symlink_to(device)
            hwmon = root / 'devices/0000:04:00.0/hwmon/hwmon5'
            hwmon.mkdir(parents=True)
            (hwmon / 'temp2_input').write_text('56000')
            (hwmon / 'temp3_input').write_text('58000')
            # nvtop_rows=[] keeps this deterministic (no real subprocess).
            rows = probe.gpus(root, nvidia_text='', nvtop_rows=[])
            self.assertEqual(len(rows), 3)
            row = next(r for r in rows if r['id'] == '0000:04:00.0')
            self.assertEqual(row['temp'], 58)
            self.assertIsNone(row['util'])
            self.assertIsNone(row['memTotal'])

    def test_nvtop_overlay_maps_by_pci_order(self):
        probe = self.load()
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            # Real driver dirs so (device/'driver').resolve().name is meaningful.
            (root / 'drivers' / 'xe').mkdir(parents=True)
            (root / 'drivers' / 'i915').mkdir(parents=True)
            # Two xe cards + one i915, in PCI order card0<card1<card2.
            for card, pci, dev, drv in [('card0', '0000:04:00.0', '0xe223', 'xe'),
                                       ('card1', '0000:09:00.0', '0xe223', 'xe'),
                                       ('card2', '0000:00:02.0', '0x7d67', 'i915')]:
                device = root / 'devices' / pci
                device.mkdir(parents=True)
                (device / 'vendor').write_text('0x8086')
                (device / 'device').write_text(dev)
                (device / 'driver').symlink_to(root / 'drivers' / drv)
                entry = root / 'class/drm' / card
                entry.mkdir(parents=True)
                (entry / 'device').symlink_to(device)
            # nvtop enumerates in PCI sort order: 00:02.0 (iGPU) < 04:00.0 < 09:00.0.
            nv = [dict(util=2.0, memUsed=None, memTotal=None, temp=None),
                  dict(util=32.0, memUsed=2000000000, memTotal=34000000000, temp=57.0),
                  dict(util=0.0, memUsed=15000000000, memTotal=34000000000, temp=64.0)]
            rows = probe.gpus(root, nvidia_text='', nvtop_rows=nv)
            self.assertEqual(len(rows), 3)
            b70a = next(r for r in rows if r['id'] == '0000:04:00.0')
            b70b = next(r for r in rows if r['id'] == '0000:09:00.0')
            igpu = next(r for r in rows if r['id'] == '0000:00:02.0')
            # Discrete xe cards get util + VRAM + temp from nvtop.
            self.assertEqual(b70a['util'], 32.0)
            self.assertEqual(b70a['memUsed'], 2000000000)
            self.assertEqual(b70a['memTotal'], 34000000000)
            self.assertEqual(b70a['temp'], 57.0)
            self.assertEqual(b70b['util'], 0.0)
            self.assertEqual(b70b['memUsed'], 15000000000)
            # Integrated i915 gets util + temp but NOT VRAM (shared memory).
            self.assertEqual(igpu['util'], 2.0)
            self.assertIsNone(igpu['memUsed'])
            self.assertIsNone(igpu['memTotal'])

    def test_nvidia_multi_gpu_preserves_unavailable_memory(self):
        probe = self.load()
        rows = probe.gpus(pathlib.Path('/nonexistent'), '00000000:01:00.0, GB10, 4, [N/A], [N/A], 42\n00000000:02:00.0, RTX, 9, 10, 100, 51', nvtop_rows=[])
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[0]['memTotal'])
        self.assertEqual(rows[1]['memUsed'], 10 * 1048576)

    def test_probe_only_hardware_and_real_sample(self):
        probe = self.load()
        self.assertTrue(hasattr(probe, 'snapshot'), 'snapshot entry point required')
        sample = probe.snapshot()
        self.assertEqual(set(sample), {'cpu', 'mem', 'disks', 'gpus', 'uptime', 'net', 'ping', 'hostname'})
        self.assertGreater(sample['mem']['total'], 0)
        self.assertTrue(sample['disks'])
        self.assertGreaterEqual(sample['cpu']['pct'], 0)
        self.assertLessEqual(sample['cpu']['pct'], 100)

if __name__ == '__main__':
    unittest.main()
