#!/usr/bin/env python3
"""Read-only Linux hardware probe. Standard library only; safe to stream to python3 -.

Reports CPU, memory, disks, and GPUs. Designed to run locally or piped over SSH
to a remote host (the monitor streams this file's bytes to `python3 -`).
"""
import csv
import json
import math
import os
import re
import shutil
import subprocess
import time
from pathlib import Path


def text(path):
    try:
        return path.read_text()[:65536].strip()
    except (OSError, UnicodeError):
        return ''


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) and n >= 0 else None
    except (ValueError, TypeError):
        return None


# Human names for common PCI vendor:device pairs. Anything not listed falls back
# to "<Vendor> <device-id>" so an unknown card is still identifiable, never blank.
DEVICE_NAMES = {
    ('0x8086', '0xe223'): 'Intel Arc Pro B70',
    ('0x8086', '0xe211'): 'Intel Arc B580',
    ('0x8086', '0x7d67'): 'Intel Arrow Lake-S iGPU',
    ('0x8086', '0x0a5e'): 'Intel Iris Xe',
    ('0x10de', '0x20b2'): 'NVIDIA RTX 3060',
    ('0x10de', '0x2204'): 'NVIDIA RTX 4060',
    ('0x10de', '0x26ba'): 'NVIDIA GB10',
}
VENDOR_NAMES = {'0x8086': 'Intel', '0x10de': 'NVIDIA', '0x1002': 'AMD'}


def gpu_name(vendor, device):
    key = (vendor.lower(), device.lower())
    if key in DEVICE_NAMES:
        return DEVICE_NAMES[key]
    return VENDOR_NAMES.get(vendor, vendor) + ' ' + device


def gpus(sys=Path('/sys'), nvidia_text=None):
    rows = {}
    for card in sorted((sys / 'class/drm').glob('card*')):
        if not re.fullmatch(r'card\d+', card.name):
            continue
        device = card / 'device'
        pci = device.resolve().name
        vendor = text(device / 'vendor')
        if not vendor:
            continue
        temps = [number(text(p)) for p in device.glob('hwmon/hwmon*/temp*_input')]
        temps = [n / 1000 for n in temps if n is not None]
        rows[pci] = dict(id=pci, name=gpu_name(vendor, text(device / 'device')),
                         driver=(device / 'driver').resolve().name if (device / 'driver').exists() else None,
                         util=number(text(device / 'gpu_busy_percent')),
                         memUsed=number(text(device / 'mem_info_vram_used')),
                         memTotal=number(text(device / 'mem_info_vram_total')),
                         temp=max(temps) if temps else None)
    if nvidia_text is None:
        try:
            result = subprocess.run(['nvidia-smi', '--query-gpu=pci.bus_id,name,utilization.gpu,memory.used,memory.total,temperature.gpu', '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=2)
            nvidia_text = result.stdout if result.returncode == 0 else ''
        except (OSError, subprocess.TimeoutExpired):
            nvidia_text = ''
    for values in csv.reader(nvidia_text.splitlines()):
        if len(values) != 6:
            continue
        pci, name, util, used, total, temp = [v.strip() for v in values]
        pci = pci.lower()
        if re.fullmatch(r'[0-9a-f]{8}:\d\d:[0-9a-f]{2}\.\d', pci):
            pci = pci[4:]
        if not re.fullmatch(r'[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.\d', pci):
            continue
        used, total = number(used), number(total)
        rows[pci] = dict(id=pci, name=name[:96], driver='nvidia', util=number(util),
                         memUsed=used * 1048576 if used is not None else None,
                         memTotal=total * 1048576 if total is not None else None,
                         temp=number(temp))
    return sorted(rows.values(), key=lambda row: row['id'])


def disks(mounts_text=None):
    """Real filesystems only: drop snap/loop/squashfs mounts and dedup by device
    name (btrfs subvolumes share one device but report a distinct st_dev each)."""
    if mounts_text is None:
        mounts_text = text(Path('/proc/self/mounts'))
    chosen = {}
    for line in mounts_text.splitlines():
        parts = line.split()
        if len(parts) < 3 or not parts[0].startswith('/dev/'):
            continue
        dev, mount, fstype = parts[0], parts[1], parts[2]
        mount = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), mount)
        if dev.startswith('/dev/loop') or fstype == 'squashfs' or mount.startswith('/snap/'):
            continue
        # Keep the highest (shortest) mount point per device; '/' wins.
        if dev in chosen:
            if len(mount) < len(chosen[dev]):
                chosen[dev] = mount
            continue
        chosen[dev] = mount
    rows = []
    for dev, mount in chosen.items():
        try:
            usage = shutil.disk_usage(mount)
        except OSError:
            continue
        if usage.total <= 0:
            continue
        rows.append(dict(mount=mount, used=usage.used, total=usage.total))
    if not rows:
        try:
            usage = shutil.disk_usage('/')
            rows.append(dict(mount='/', used=usage.used, total=usage.total))
        except OSError:
            pass
    return rows[:32]


def snapshot():
    def ticks():
        values = [int(n) for n in text(Path('/proc/stat')).splitlines()[0].split()[1:9]]
        return sum(values), values[3] + values[4]

    try:
        total0, idle0 = ticks()
        time.sleep(0.15)
        total1, idle1 = ticks()
        pct = max(0, min(100, 100 * (1 - (idle1 - idle0) / (total1 - total0)))) if total1 > total0 else None
    except (OSError, ValueError, IndexError):
        pct = None
    memory = {}
    for line in text(Path('/proc/meminfo')).splitlines():
        key, _, value = line.partition(':')
        parts = value.split()
        if parts and number(parts[0]) is not None:
            memory[key] = int(parts[0]) * 1024
    total = memory.get('MemTotal')
    available = memory.get('MemAvailable')
    return dict(cpu=dict(pct=pct),
                mem=dict(total=total, used=total - available if total is not None and available is not None else None),
                disks=disks(), gpus=gpus()[:16])


if __name__ == '__main__':
    print(json.dumps(snapshot(), allow_nan=False))
