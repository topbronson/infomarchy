#!/usr/bin/env python3
"""Read-only Linux hardware probe. Standard library only; safe to stream to python3 -."""
import csv
import json
import math
from pathlib import Path
import re
import subprocess


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
        rows[pci] = dict(id=pci, name={'0x8086': 'Intel', '0x10de': 'NVIDIA', '0x1002': 'AMD'}.get(vendor, vendor) + ' ' + text(device / 'device'),
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
        rows[pci] = dict(id=pci, name=name[:96], driver='nvidia', util=number(util), memUsed=used * 1048576 if used is not None else None, memTotal=total * 1048576 if total is not None else None, temp=number(temp))
    return sorted(rows.values(), key=lambda row: row['id'])


def snapshot():
    import os
    import shutil
    import time

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
    disks = []
    seen = set()
    for line in text(Path('/proc/self/mounts')).splitlines():
        parts = line.split()
        if len(parts) < 3 or not parts[0].startswith('/dev/'):
            continue
        mount = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), parts[1])
        try:
            dev = os.stat(mount).st_dev
            if dev in seen:
                continue
            usage = shutil.disk_usage(mount)
            seen.add(dev)
            disks.append(dict(mount=mount, used=usage.used, total=usage.total))
        except OSError:
            continue
    if not disks:
        try:
            usage = shutil.disk_usage('/')
            disks.append(dict(mount='/', used=usage.used, total=usage.total))
        except OSError:
            pass
    return dict(cpu=dict(pct=pct), mem=dict(total=total, used=total - available if total is not None and available is not None else None), disks=disks[:32], gpus=gpus()[:16])


if __name__ == '__main__':
    print(json.dumps(snapshot(), allow_nan=False))

