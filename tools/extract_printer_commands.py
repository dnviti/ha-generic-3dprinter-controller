"""Extract the SDCP command sequences a live ELEGOO printer's own UI uses.

The printer's Angular app talks to the printer over ``ws://<host>:3030/websocket``
and its service class is the ground truth for the wire format. This dumps every
``getMsgBodyString(...)`` call site, every HTTP upload path, and the status
bitmask checks, so an integration can copy the real sequences instead of
guessing.

    python tools/extract_printer_commands.py 192.168.128.143
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from mine_printer_bundle import bundle_bytes  # noqa: E402

#: The UUID helper module: ``getMsgBodyString(id, cmd, mainboardId, data)``.
CALL_RE = re.compile(rb'getMsgBodyString\((.{0,400}?)\)(?=[,;.\)\]}])', re.DOTALL)
UPLOAD_RE = re.compile(rb'["\'`]([^"\'`]{0,60}(?:register|upload|fileUpload|UPLOAD)[^"\'`]{0,60})["\'`]')
XHR_RE = re.compile(rb'\.open\(\s*["\'`]([A-Z]+)["\'`]\s*,\s*([^,)]{0,120})', re.DOTALL)
STATUS_RE = re.compile(rb'CurrentStatus[^,;]{0,40}\.includes\((\d+)\)')
BITMASK_RE = re.compile(rb'CurrentStatus[^)]{0,30}?\b(8|1|2|3|4|5|6|7|9|10|11|12|13|14|15|16)\b')


def show(label: str, values: list[bytes], limit: int = 40) -> None:
    print(f"===== {label} ({len(values)}) =====")
    for value in values[:limit]:
        print(f"  {value.decode('utf-8', 'replace')}")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host")
    parser.add_argument("--cache", default=None)
    args = parser.parse_args()

    cache = Path(args.cache) if args.cache else Path.home() / ".cache" / "ha-3dprinter-bundle"
    data = bundle_bytes(args.host, cache)

    calls: list[bytes] = []
    for match in CALL_RE.finditer(data):
        text = b" ".join(match.group(1).split())
        if text not in calls:
            calls.append(text)
    show("getMsgBodyString call sites", calls)

    uploads = [m.group(1) for m in UPLOAD_RE.finditer(data)]
    show("upload-ish literals", list(dict.fromkeys(uploads)))

    xhr = [m.group(0) for m in XHR_RE.finditer(data)]
    show("XMLHttpRequest open()", list(dict.fromkeys(xhr))[:30])

    status = [m.group(1) for m in STATUS_RE.finditer(data)]
    show("CurrentStatus.includes(N)", list(dict.fromkeys(status)))

    print("===== status bit meanings found in the UI =====")
    for match in re.finditer(rb'.{160}CurrentStatus[^;]{0,120}', data):
        print(f"  {match.group(0).decode('utf-8', 'replace')[:320]}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
