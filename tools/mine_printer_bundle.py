"""Mine the ELEGOO web bundle on a live printer for the API surface it uses.

The printer's own Angular single-page app is the authoritative description of
what the printer exposes. This walks it as bytes (no encoding guessing),
reports which network endpoints and SDCP commands it references, and can dump
the surrounding context for any pattern.

    python tools/mine_printer_bundle.py 192.168.128.143
    python tools/mine_printer_bundle.py 192.168.128.143 --context /websocket
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from pathlib import Path

ASSET_JS = re.compile(rb'src="([^"]+\.js)"')
ASSET_ANY = re.compile(rb'(?:src|href)="([^"]+)"')

INTERESTING = {
    "websocket urls": rb'wss?://[^"\'`\s]{0,100}',
    "root-relative paths": rb'["\'`](/[A-Za-z0-9_\-./]{1,70})["\'`]',
    "port literals": rb':\s*(?:3030|8080|8088|81|5000|7125|554|8554)\b',
    "sdsp topics": rb'sdcp/[a-z]+',
    "command names": rb'[A-Z][A-Z_]{6,40}(?:PRINT|FILE|VIDEO|STATUS|MATERIAL|HISTORY)[A-Z_]{0,20}',
    "video words": rb'(?i)(?:mjpeg|h264|rtsp|webrtc|jsmpeg|mediasource|sourcebuffer|videofeed)',
}


def fetch(url: str, timeout: int = 15) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        return response.read()


def bundle_bytes(host: str, cache: Path) -> bytes:
    cache.mkdir(parents=True, exist_ok=True)
    index = fetch(f"http://{host}/").decode("utf-8", "replace")
    scripts = re.findall(r'src="([^"]+\.js)"', index)
    if not scripts:
        raise SystemExit("no scripts found in the printer index page")
    blobs: list[bytes] = []
    for name in scripts:
        local = cache / Path(name).name
        if not local.exists():
            print(f"downloading {name}", file=sys.stderr)
            local.write_bytes(fetch(f"http://{host}/{name.lstrip('/')}"))
        blobs.append(local.read_bytes())
    return b"\n".join(blobs)


def unique_matches(data: bytes, pattern: bytes, limit: int = 60) -> list[bytes]:
    seen: list[bytes] = []
    for match in re.finditer(pattern, data):
        value = match.group(0)
        if value not in seen:
            seen.append(value)
        if len(seen) >= limit:
            break
    return seen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host")
    parser.add_argument("--cache", default=None, help="directory holding downloaded bundles")
    parser.add_argument("--context", default=None, help="dump context around this pattern")
    parser.add_argument("--window", type=int, default=600)
    args = parser.parse_args()

    cache = Path(args.cache) if args.cache else Path.home() / ".cache" / "ha-3dprinter-bundle"
    data = bundle_bytes(args.host, cache)
    print(f"bundle bytes: {len(data)}\n")

    if args.context:
        needle = args.context.encode("utf-8")
        position = 0
        found = 0
        while (position := data.find(needle, position)) >= 0 and found < 10:
            found += 1
            start = max(0, position - args.window)
            snippet = data[start : position + args.window].decode("utf-8", "replace")
            print(f"----- match {found} at byte {position} -----")
            print(snippet)
            print()
            position += len(needle)
        if not found:
            print(f"pattern {args.context!r} not present")
        return 0

    for label, pattern in INTERESTING.items():
        matches = unique_matches(data, pattern)
        print(f"===== {label} ({len(matches)}) =====")
        for value in matches:
            print(f"  {value.decode('utf-8', 'replace')}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
