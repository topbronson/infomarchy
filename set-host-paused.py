#!/usr/bin/env python3
"""Toggle a host's pause state in the hardware-paused sidecar file.

Usage: set-host-paused.py <host-id> <on|off>
The monitor re-reads the sidecar every second, so the change applies on the
next cycle without a worker restart.
"""
import json
import os
import re
import sys
from pathlib import Path


def main():
    if len(sys.argv) != 3 or sys.argv[2] not in ('on', 'off'):
        print('usage: set-host-paused.py <host-id> <on|off>', file=sys.stderr)
        return 2
    host_id, paused = sys.argv[1], sys.argv[2] == 'on'
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,31}', host_id):
        print('invalid host id', file=sys.stderr)
        return 2
    base = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'infomarchy'
    base.mkdir(parents=True, exist_ok=True)
    path = base / 'hardware-paused.json'
    ids = []
    if path.exists():
        try:
            data = json.loads(path.read_text())
            if isinstance(data, list):
                ids = [x for x in data if isinstance(x, str)]
        except (OSError, ValueError):
            ids = []
    if host_id not in ids:
        if paused:
            ids.append(host_id)
    else:
        ids.remove(host_id)
    path.write_text(json.dumps(sorted(ids)))
    print('paused=' + json.dumps(sorted(ids)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
