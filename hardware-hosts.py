#!/usr/bin/env python3
"""Independent, bounded hardware monitor; never imports the AI collector."""
import json
import os
from pathlib import Path
import re
import sys
import math
import selectors
import signal
import subprocess
import tempfile
import time


def run_bounded(argv, source, timeout=6):
    """Cap both pipes, deadline and process group; no shell interpolation."""
    with tempfile.TemporaryFile() as stdin:
        stdin.write(source)
        stdin.seek(0)
        proc = subprocess.Popen(argv, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        assert proc.stdout is not None and proc.stderr is not None
        selector = selectors.DefaultSelector()
        output, errors = bytearray(), bytearray()
        selector.register(proc.stdout, selectors.EVENT_READ, output)
        selector.register(proc.stderr, selectors.EVENT_READ, errors)
        deadline = time.monotonic() + timeout
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Hardware probe timed out')
                for key, _ in selector.select(min(remaining, 0.1)):
                    chunk = os.read(key.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        key.data.extend(chunk)
                        if len(output) + len(errors) > 65536:
                            raise ValueError('Hardware probe output exceeds 64 KiB')
            try:
                code = proc.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                raise TimeoutError('Hardware probe timed out') from None
            if code:
                raise ValueError(errors.decode('utf-8', errors='replace')[:256] or 'Hardware probe failed')
            return output.decode('utf-8')
        finally:
            selector.close()
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            proc.stdout.close()
            proc.stderr.close()


def validate_stats(stats):
    def metrics(obj, fields):
        if not isinstance(obj, dict) or set(obj) != set(fields):
            raise ValueError('Invalid hardware metrics')
        for value in obj.values():
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1e18):
                raise ValueError('Invalid hardware number')

    def label(value):
        if not isinstance(value, str) or len(value) > 256 or any(ord(c) < 32 for c in value):
            raise ValueError('Invalid hardware label')

    if not isinstance(stats, dict) or set(stats) != {'cpu', 'mem', 'disks', 'gpus', 'uptime', 'net', 'ping', 'hostname'}:
        raise ValueError('Not a hardware-only snapshot')
    metrics(stats['cpu'], ['pct', 'load', 'temp'])
    metrics(stats['mem'], ['used', 'total'])
    if stats['cpu']['pct'] is not None and stats['cpu']['pct'] > 100:
        raise ValueError('Invalid CPU percentage')
    for name, limit in [('disks', 32), ('gpus', 16)]:
        if not isinstance(stats[name], list) or len(stats[name]) > limit:
            raise ValueError('Too many hardware rows')
    for disk in stats['disks']:
        if not isinstance(disk, dict) or set(disk) != {'mount', 'used', 'total'}:
            raise ValueError('Invalid disk')
        label(disk['mount'])
        metrics({k: disk[k] for k in ['used', 'total']}, ['used', 'total'])
    for gpu in stats['gpus']:
        if not isinstance(gpu, dict) or set(gpu) != {'id', 'name', 'driver', 'util', 'temp', 'memUsed', 'memTotal'}:
            raise ValueError('Invalid GPU')
        label(gpu['id'])
        label(gpu['name'])
        if gpu['driver'] is not None:
            label(gpu['driver'])
        fields = ['util', 'temp', 'memUsed', 'memTotal']
        metrics({k: gpu[k] for k in fields}, fields)
        if gpu['util'] is not None and gpu['util'] > 100:
            raise ValueError('Invalid GPU percentage')
    if stats['uptime'] is not None and (type(stats['uptime']) not in (int, float) or not math.isfinite(stats['uptime']) or stats['uptime'] < 0):
        raise ValueError('Invalid uptime')
    if stats['hostname'] is not None:
        label(stats['hostname'])
    if stats['cpu']['load'] is not None and stats['cpu']['load'] > 1e6:
        raise ValueError('Invalid CPU load')
    if stats['cpu']['temp'] is not None and stats['cpu']['temp'] > 500:
        raise ValueError('Invalid CPU temperature')
    net = stats['net']
    if not isinstance(net, dict) or set(net) != {'dev', 'addr', 'wan', 'rx', 'tx', 'wireless', 'ssid', 'signal'}:
        raise ValueError('Invalid net')
    for key in ('dev', 'addr', 'wan', 'ssid'):
        if net[key] is not None:
            label(net[key])
    for key in ('rx', 'tx', 'signal'):
        if net[key] is not None and (type(net[key]) not in (int, float) or not math.isfinite(net[key]) or not 0 <= net[key] <= 1e18):
            raise ValueError('Invalid net number')
    if not isinstance(net['wireless'], bool):
        raise ValueError('Invalid net wireless')
    ping = stats['ping']
    if not isinstance(ping, dict) or set(ping) != {'ok', 'ms'}:
        raise ValueError('Invalid ping')
    if not isinstance(ping['ok'], bool):
        raise ValueError('Invalid ping ok')
    if ping['ms'] is not None and (type(ping['ms']) not in (int, float) or not math.isfinite(ping['ms']) or not 0 <= ping['ms'] <= 1e6):
        raise ValueError('Invalid ping ms')
    return stats


class HostState:
    def __init__(self, host, paused=False):
        self.host = host
        self.paused = paused
        self.stats = None
        self.last_success = None
        self.last_attempt = None
        self.error = ''
        self._net_prev = None
        self._net_rate = None

    def carry_forward_gpus(self, stats):
        # A fresh probe that lost its nvtop read (intermittent segfault) comes
        # back with null util/VRAM. Keep the last-known figures so the GPU
        # rows don't flap to '-' between good ticks; temp still comes from
        # /sys every time.
        prev = self.stats
        if not prev:
            return stats
        prev_gpus = {g['id']: g for g in (prev.get('gpus') or [])}
        for g in (stats.get('gpus') or []):
            pg = prev_gpus.get(g.get('id'))
            if not pg:
                continue
            for field in ('util', 'memUsed', 'memTotal'):
                if g.get(field) is None and pg.get(field) is not None:
                    g[field] = pg[field]
        return stats

    def update(self, stats, error, stamp):
        self.last_attempt = stamp
        self.error = error[:256].replace('\n', ' ').replace('\r', ' ')
        if stats is not None:
            self.stats = validate_stats(self.carry_forward_gpus(stats))
            self.last_success = stamp
            net = stats.get('net') or {}
            rx, tx = net.get('rx'), net.get('tx')
            if rx is not None and tx is not None:
                if self._net_prev is not None:
                    prx, ptx, pstamp = self._net_prev
                    dt = stamp - pstamp
                    self._net_rate = (max(0.0, (rx - prx) / dt), max(0.0, (tx - ptx) / dt)) if dt > 0 else None
                else:
                    self._net_rate = None
                self._net_prev = (rx, tx, stamp)

    def view(self, now):
        stale = self.last_success is None or now - self.last_success > 30 or bool(self.error)
        status = 'paused' if self.paused else 'offline' if self.error else 'collecting' if self.stats is None else 'stale' if stale else 'online'
        return dict(id=self.host['id'], label=self.host['label'], user=self.host.get('user'), stats=self.stats, status=status, stale=stale, error=self.error, paused=self.paused,
                    netRate=self._net_rate,
                    lastSuccess=self.last_success * 1000 if self.last_success is not None else None,
                    lastAttempt=self.last_attempt * 1000 if self.last_attempt is not None else None)


def parse_hosts(config):
    if not isinstance(config, dict) or set(config) != {'hosts'} or not isinstance(config['hosts'], list) or len(config['hosts']) > 4:
        raise ValueError('Expected {"hosts": [...]} with at most four hosts')
    hosts, seen = [], {'local'}
    patterns = {'id': r'[A-Za-z0-9][A-Za-z0-9_-]{0,31}', 'address': r'[A-Za-z0-9][A-Za-z0-9.:-]{0,252}', 'user': r'[A-Za-z_][A-Za-z0-9_-]{0,63}'}
    for host in config['hosts']:
        if not isinstance(host, dict) or set(host) != {'id', 'label', 'address', 'user'}:
            raise ValueError('Host requires id, label, address and user only')
        if any(not isinstance(host[k], str) or not re.fullmatch(pattern, host[k]) for k, pattern in patterns.items()):
            raise ValueError('Invalid host identifier, address or user')
        if host['id'] in seen or not isinstance(host['label'], str) or not 1 <= len(host['label']) <= 64 or any(ord(c) < 32 for c in host['label']):
            raise ValueError('Invalid or duplicate host label/id')
        seen.add(host['id'])
        hosts.append(dict(host))
    return hosts


def collect(host):
    source = Path(__file__).with_name('hardware-probe.py').read_bytes()
    argv = [sys.executable, '-'] if host['id'] == 'local' else ssh_command(host)
    return validate_stats(json.loads(run_bounded(argv, source)))


class Monitor:
    def __init__(self, hosts, executor, paused_path=None):
        self.states = [HostState({'id': 'local', 'label': 'Local hardware'})] + [HostState(host) for host in hosts]
        self.executor = executor
        self.pending = {}
        self.due = {}
        self.paused_path = paused_path
        self.paused = set()

    def tick(self, now):
        if self.paused_path is not None:
            try:
                self.paused = read_paused(self.paused_path)
            except (OSError, ValueError):
                self.paused = set()
        for state in self.states:
            key = state.host['id']
            state.paused = key in self.paused
            if state.paused:
                # Paused hosts are skipped: no probe, no SSH, keep the last good
                # data, and report status "paused" until re-enabled.
                if key in self.pending:
                    del self.pending[key]
                state.update(None, '', now)
                self.due[key] = 0
                continue
            future = self.pending.get(key)
            if future is not None and future.done():
                try:
                    state.update(future.result(), '', now)
                except Exception as error:
                    state.update(None, str(error), now)
                del self.pending[key]
            if key not in self.pending and now >= self.due.get(key, 0):
                self.pending[key] = self.executor.submit(collect, state.host)
                self.due[key] = now + 5

    def rows(self, now):
        return [state.view(now) for state in self.states]


def read_paused(path):
    import stat
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except FileNotFoundError:
        return set()
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Hardware paused file must be a regular file')
        raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError('Hardware paused file exceeds 4 KiB')
    data = json.loads(raw or b'[]')
    if not isinstance(data, list) or any(not isinstance(x, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,31}', x) for x in data):
        raise ValueError('Invalid hardware paused file')
    return set(data)


def read_config(path):
    import stat
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    except FileNotFoundError:
        return []
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Hardware config must be a regular file')
        raw = stream.read(16385)
        if len(raw) > 16384:
            raise ValueError('Hardware config exceeds 16 KiB')
    return parse_hosts(json.loads(raw))


def main():
    import argparse
    from concurrent.futures import ThreadPoolExecutor
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default=str(Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'infomarchy/hardware-hosts.json'))
    parser.add_argument('--once', action='store_true', help='Wait for one bounded round, then exit')
    args = parser.parse_args()
    try:
        hosts, error = read_config(args.config), ''
    except (OSError, ValueError) as exc:
        hosts, error = [], 'Hardware config: ' + str(exc)[:256]
    with ThreadPoolExecutor(max_workers=5) as executor:
        monitor = Monitor(hosts, executor,
                          paused_path=str(Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'infomarchy/hardware-paused.json'))
        while True:
            now = time.time()
            monitor.tick(now)
            if args.once:
                for future in list(monitor.pending.values()):
                    try:
                        future.result()
                    except Exception:
                        pass
                monitor.tick(time.time())
            print(json.dumps(dict(hosts=monitor.rows(time.time()), error=error), allow_nan=False), flush=True)
            if args.once:
                return
            time.sleep(1)


def ssh_command(host):
    return ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=3', '-o', 'ConnectionAttempts=1', '-o', 'ServerAliveInterval=2', '-o', 'ServerAliveCountMax=1', '-o', 'ClearAllForwardings=yes', '-o', 'PermitLocalCommand=no', '-o', 'ForwardAgent=no', '-o', 'ForwardX11=no', '-l', host['user'], '--', host['address'], 'python3', '-']


if __name__ == '__main__':
    try:
        main()
    except (BrokenPipeError, KeyboardInterrupt):
        pass
