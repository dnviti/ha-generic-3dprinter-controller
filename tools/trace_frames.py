"""Trace the exact frames the SDCP adapter sends and receives.

Used to diagnose a correlation bug rather than guess at one. It applies the same
adapter the integration uses and prints every frame the reader routes.

    python tools/trace_frames.py 192.168.128.143 file
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components"))

if sys.platform == "win32":
    # aiodns refuses the Proactor loop that Windows defaults to.
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def run(host: str, action: str) -> int:
    import aiohttp

    from generic_3dprinter.adapters import sdcp
    from generic_3dprinter.const import ProtocolId
    from generic_3dprinter.protocols import parse_config
    from generic_3dprinter.registry import build_adapter

    config = parse_config(
        {
            "name": "trace",
            "protocol": ProtocolId.SDCP_CC1.value,
            "host": host,
            "port": 3030,
            "camera_port": 3031,
        }
    )

    original = sdcp.SdcpProtocol._handle_frame

    def traced(self, raw: str) -> None:  # noqa: ANN001
        payload = sdcp.load_frame(raw)
        if payload is None:
            print("  <- UNDECODABLE", raw[:200])
            return
        topic = str(payload.get("Topic") or "")
        data = payload.get("Data") or {}
        inner = data.get("Data") if isinstance(data, dict) else None
        print(f"  <- topic={topic}")
        if isinstance(inner, dict):
            keys = sorted(inner)
            print(f"     Data.Data keys={keys}")
            if "FileList" in inner:
                entries = inner["FileList"]
                print(f"     FileList length={len(entries) if isinstance(entries, list) else 'n/a'}")
                if isinstance(entries, list) and entries:
                    first = entries[0]
                    print(f"     first entry={json.dumps(first)[:400]}")
                    kinds = {item.get("type") for item in entries if isinstance(item, dict)}
                    print(f"     entry types={kinds}")
                    print(f"     parsed={len(sdcp.parse_file_list(entries))}")
        original(self, raw)

    sdcp.SdcpProtocol._handle_frame = traced  # type: ignore[method-assign]

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None)) as session:
        adapter = build_adapter(config, session)
        await adapter.async_setup()
        print(f"[trace] MainboardID resolved to {adapter._mainboard_id!r}")  # noqa: SLF001

        if action == "file":
            print("[trace] async_list_files")
            files = await adapter.async_list_files()
            print(f"[trace] returned {len(files)} entries")
            print(f"[trace] internal buffer has {len(adapter._file_list)} entries")  # noqa: SLF001

        await adapter.async_teardown()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host")
    parser.add_argument("action", nargs="?", default="file")
    args = parser.parse_args()
    return asyncio.run(run(args.host, args.action))


if __name__ == "__main__":
    raise SystemExit(main())
