#!/usr/bin/env python3
"""Read-only Linux hardware probe. Standard library only; safe to stream to python3 -.

Reports CPU, memory, disks, and GPUs. Designed to run locally or piped over SSH
to a remote host (the monitor streams this file's bytes to `python3 -`).

GPU sources, in order of preference per card:
  - NVIDIA: nvidia-smi (util, VRAM, temp)
  - Intel/AMD discrete (xe/amdgpu): nvtop --snapshot (util, VRAM, temp)
  - Integrated (i915): nvtop --snapshot (util, temp only; VRAM is shared system
    memory, so it is not reported as a discrete VRAM figure)
  - Fallback: /sys (PCI identity + temp only)
"""
import csv
import os
import json
import math
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


def pct(value):
    """Parse '42%' / '42' / None -> 42.0 or None."""
    if value is None:
        return None
    s = str(value).strip()
    if s.endswith('%'):
        s = s[:-1]
    return number(s)


def celsius(value):
    """Parse '56C' / '56' / None -> 56.0 or None."""
    if value is None:
        return None
    s = str(value).strip()
    if s.endswith('C'):
        s = s[:-1]
    return number(s)


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


def nvtop_gpus():
    """Return a list of {util, memUsed, memTotal, temp} in nvtop enumeration
    order (which matches PCI order). Empty if nvtop is missing or errors."""
    try:
        result = subprocess.run(['nvtop', '--snapshot'], capture_output=True, text=True, timeout=3)
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    rows = []
    for d in data:
        if not isinstance(d, dict):
            continue
        rows.append(dict(
            util=pct(d.get('gpu_util')),
            memUsed=number(d.get('mem_used')),
            memTotal=number(d.get('mem_total')),
            temp=celsius(d.get('temp')),
        ))
    return rows


def gpus(sys=Path('/sys'), nvidia_text=None, nvtop_rows=None):
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
                         util=None,
                         memUsed=None,
                         memTotal=None,
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
    # Overlay nvtop data for non-NVIDIA cards, by index (nvtop enumerates in the
    # same PCI order as the /sys scan). Integrated GPUs (i915) get util+temp only;
    # their "VRAM" is shared system memory, not a discrete figure.
    if nvtop_rows is None:
        nvtop_rows = nvtop_gpus()
    idx = 0
    for pci in sorted(rows.keys()):
        row = rows[pci]
        if row['driver'] != 'nvidia' and idx < len(nvtop_rows):
            nv = nvtop_rows[idx]
            if row['util'] is None:
                row['util'] = nv['util']
            if row['temp'] is None:
                row['temp'] = nv['temp']
            if row['driver'] in ('xe', 'amdgpu'):
                if row['memUsed'] is None:
                    row['memUsed'] = nv['memUsed']
                if row['memTotal'] is None:
                    row['memTotal'] = nv['memTotal']
        idx += 1
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


def net_info():
    """Primary interface: dev, LAN addr, WAN (external) IP, cumulative rx/tx
    bytes, and wireless info. The monitor turns rx/tx into rates between ticks."""
    dev = None
    try:
        out = subprocess.run(["ip", "route", "get", "1.1.1.1"], capture_output=True, text=True, timeout=1).stdout
        m = re.search(r"dev (\S+)", out)
        if m:
            dev = m.group(1)
    except (OSError, subprocess.TimeoutExpired):
        pass
    if not dev:
        try:
            for name in sorted(os.listdir("/sys/class/net")):
                if name == "lo":
                    continue
                if number(text(Path("/sys/class/net") / name / "statistics" / "rx_bytes")) or number(text(Path("/sys/class/net") / name / "statistics" / "tx_bytes")):
                    dev = name
                    break
        except OSError:
            pass
    if not dev:
        return dict(dev=None, addr=None, wan=None, rx=None, tx=None, wireless=False, ssid=None, signal=None)
    stats = Path("/sys/class/net") / dev / "statistics"
    rx, tx = number(text(stats / "rx_bytes")), number(text(stats / "tx_bytes"))
    addr = None
    try:
        out = subprocess.run(["ip", "-4", "-j", "addr", "show", dev], capture_output=True, text=True, timeout=1).stdout
        for a in json.loads(out)[0].get("addr_info", []):
            if a.get("family") in ("inet", "inet4"):
                addr = a.get("local")
                break
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, IndexError):
        pass
    wireless = (Path("/sys/class/net") / dev / "wireless").exists()
    ssid = signal = None
    if wireless:
        link = text(Path("/sys/class/net") / dev / "wireless" / "link")
        m = re.search(r"SSID:\s*(.+)", link)
        ssid = m.group(1).strip() if m else None
        m = re.search(r"signal:\s*(-?\d+)", link)
        signal = int(m.group(1)) if m else None
    return dict(dev=dev, addr=addr, wan=external_ip(), rx=rx, tx=tx, wireless=wireless, ssid=ssid, signal=signal)


def ping():
    try:
        out = subprocess.run(["ping", "-c", "1", "-W", "1", "1.1.1.1"], capture_output=True, text=True, timeout=2).stdout
        m = re.search(r"time[=<]\s*([\d.]+)\s*ms", out)
        ok = "0% packet loss" in out
        return dict(ok=ok, ms=float(m.group(1)) if m else None)
    except (OSError, subprocess.TimeoutExpired):
        return dict(ok=False, ms=None)


def external_ip():
    try:
        import urllib.request
        req = urllib.request.Request("https://1.1.1.1/cdn-cgi/trace", headers={"User-Agent": "infomarchy"})
        with urllib.request.urlopen(req, timeout=1.2) as r:
            trace = r.read(4096).decode("utf-8", "replace")
        for line in trace.splitlines():
            if line.startswith("ip="):
                ip = line[3:].strip()
                return ip if re.fullmatch(r"[0-9a-fA-F.:]+", ip) else None
    except Exception:
        pass
    return None


def load_average():
    try:
        return round(float(os.getloadavg()[0]), 2)
    except (OSError, ValueError):
        return None


def cpu_temp():
    # Package CPU temp from the platform thermal zone (acpitz on x86, acpi_thermal
    # on ARM). Raw values are millidegrees, so divide by 1000. Returns Celsius or
    # None (never the bogus 5-digit millidegree figure).
    best = None
    for path in sorted(Path('/sys/class/thermal').glob('thermal_zone*')):
        try:
            ztype = text(path / 'type')
        except OSError:
            continue
        if ztype not in ('acpitz', 'acpi_thermal'):
            continue
        t = number(text(path / 'temp'))
        if t is not None:
            t = t / 1000
        if t is not None and (best is None or t > best):
            best = t
    return best


def hostname():
    try:
        import socket
        name = socket.gethostname().strip()
        if name:
            return name[:256]
    except OSError:
        pass
    try:
        return text(Path("/etc/hostname")).splitlines()[0].strip()[:256]
    except (OSError, IndexError):
        return None


def uptime():
    try:
        return float(text(Path("/proc/uptime")).split()[0])
    except (OSError, ValueError, IndexError):
        return None


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
    return dict(cpu=dict(pct=pct, load=load_average(), temp=cpu_temp()),
                mem=dict(total=total, used=total - available if total is not None and available is not None else None),
                disks=disks(), gpus=gpus()[:16],
                uptime=uptime(), net=net_info(), ping=ping(),
                hostname=hostname())


if __name__ == '__main__':
    print(json.dumps(snapshot(), allow_nan=False))
