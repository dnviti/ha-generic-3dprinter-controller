"""Dump a live SDCP status frame and the file list, field by field.

Read-only. Useful for confirming a firmware's exact field set before wiring a
sensor map to it:

    python tools/dump_status.py 192.168.128.143
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid

CMD_STATUS = 0
CMD_ATTRIBUTES = 1
CMD_FILE_LIST = 258


def envelope(mainboard_id: str, cmd: int, data: dict) -> str:
    return json.dumps(
        {
            "Id": "",
            "Data": {
                "Cmd": cmd,
                "Data": data,
                "RequestID": uuid.uuid4().hex,
                "MainboardID": mainboard_id,
                "TimeStamp": int(time.time() * 1000),
                "From": 1,
            },
        }
    )


async def run(host: str, port: int, seconds: float) -> int:
    import aiohttp

    mainboard_id = ""
    seen: dict[str, dict] = {}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None)) as session:
        async with session.ws_connect(
            f"ws://{host}:{port}/websocket", max_msg_size=8 * 1024 * 1024
        ) as ws:
            sent = 0

            async def pump() -> None:
                nonlocal sent, mainboard_id
                async for msg in ws:
                    if msg.type is not aiohttp.WSMsgType.TEXT:
                        continue
                    frame = json.loads(msg.data)
                    topic = frame.get("Topic", "")
                    if "status" in topic and "Status" in frame:
                        seen["status"] = frame["Status"]
                        mainboard_id = frame.get("MainboardID", mainboard_id)
                    elif "attributes" in topic:
                        seen["attributes"] = frame["Attributes"]
                    elif "response" in topic:
                        payload = frame.get("Data", {})
                        cmd = payload.get("Cmd")
                        seen[f"response:{cmd}"] = payload
                        if not mainboard_id:
                            mainboard_id = payload.get("MainboardID", "")
                    if sent < 3:
                        cmd = (CMD_STATUS, CMD_ATTRIBUTES, CMD_FILE_LIST)[sent]
                        data = {"Url": "/local"} if cmd == CMD_FILE_LIST else {}
                        await ws.send_str(envelope(mainboard_id, cmd, data))
                        sent += 1

            task = asyncio.create_task(pump())
            await ws.send_str(envelope("", CMD_STATUS, {}))
            sent = 1
            await asyncio.sleep(0.6)
            await ws.send_str(envelope(mainboard_id, CMD_ATTRIBUTES, {}))
            sent = 2
            await asyncio.sleep(0.6)
            await ws.send_str(envelope(mainboard_id, CMD_FILE_LIST, {"Url": "/local"}))
            sent = 3
            await asyncio.sleep(seconds)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    for key, value in seen.items():
        print(f"\n===== {key} =====")
        print(json.dumps(value, indent=2)[:4000])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=3030)
    parser.add_argument("--seconds", type=float, default=4.0)
    args = parser.parse_args()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return asyncio.run(run(args.host, args.port, args.seconds))


if __name__ == "__main__":
    raise SystemExit(main())
