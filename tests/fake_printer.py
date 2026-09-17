"""A fake SDCP printer for the test suite.

It speaks the frame shapes a real Centauri Carbon produces, including the detail
that matters most for a parser: a command is acknowledged in one frame and its
payload can arrive in another. Collapsing the two is how a fake passes while the
real adapter hangs.
"""

from __future__ import annotations

import json

#: The mainboard id reported by the printer this fake was shaped from.
MAINBOARD = "5c441dd30105041800009c0000000000"


class FakePrinterServer:
    """A loopback printer that speaks enough SDCP for the adapter to work."""

    def __init__(self) -> None:
        """Create the server with a printing job in progress."""
        self.status = {
            "CurrentStatus": [1],
            "TempOfNozzle": 219.5,
            "TempTargetNozzle": 220,
            "TempOfHotbed": 55.0,
            "TempTargetHotbed": 55,
            "TempOfBox": 32.5,
            "TempTargetBox": 0,
            "CurrenCoord": "101.10,77.83,22.45",
            "CurrentFanSpeed": {"ModelFan": 100, "AuxiliaryFan": 69, "BoxFan": 68},
            "LightStatus": {"SecondLight": 1, "RgbLight": [0, 0, 0]},
            "PrintInfo": {
                "Status": 13,
                "CurrentLayer": 107,
                "TotalLayer": 627,
                "CurrentTicks": 2532.16,
                "TotalTicks": 18574,
                "Filename": "part.gcode",
                "TaskId": "3d315103",
                "PrintSpeedPct": 100,
                "Progress": 12,
            },
        }
        self.attributes = {
            "Name": "Fake Centauri",
            "MachineName": "Centauri Carbon",
            "BrandName": "ELEGOO",
            "FirmwareVersion": "V1.4.49",
            "MainboardID": MAINBOARD,
            "MainboardIP": "127.0.0.1",
            "CameraStatus": 1,
            "Capabilities": ["FILE_TRANSFER", "PRINT_CONTROL", "VIDEO_STREAM"],
        }
        self.received: list[dict] = []
        self.sent_commands: list[int] = []
        self.url: str = ""
        self._server = None

    async def start(self) -> str:
        """Start the server and return its ``ws://`` URL."""
        from aiohttp import web

        app = web.Application()
        app.router.add_get("/websocket", self._handle)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        self._server = runner
        port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
        self.url = f"http://127.0.0.1:{port}"
        return self.url

    async def stop(self) -> None:
        """Stop the server."""
        if self._server is not None:
            await self._server.cleanup()
            self._server = None

    async def _handle(self, request):
        from aiohttp import WSMsgType, web

        ws = web.WebSocketResponse(max_msg_size=8 * 1024 * 1024)
        await ws.prepare(request)
        await ws.send_str(
            json.dumps(
                {
                    "Data": {"Cmd": 1, "Data": {}, "MainboardID": MAINBOARD},
                    "Topic": f"sdcp/attributes/{MAINBOARD}",
                    "Attributes": self.attributes,
                    "MainboardID": MAINBOARD,
                }
            )
        )
        async for message in ws:
            if message.type is not WSMsgType.TEXT:
                break
            frame = json.loads(message.data)
            inner = frame.get("Data", {})
            cmd = inner.get("Cmd")
            request_id = inner.get("RequestID", "")
            self.received.append(inner)
            self.sent_commands.append(cmd)

            if cmd == 1:
                # The real printer answers the request with an Ack frame and pushes
                # the attributes in a frame of their own. Collapsing the two makes
                # the adapter wait forever for an acknowledgement it never sees.
                await ws.send_str(self._ack_frame(cmd, request_id))
                await ws.send_str(self._attributes_push())
            elif cmd == 0:
                await ws.send_str(self._ack_frame(cmd, request_id))
                await ws.send_str(self._status_frame())
            elif cmd == 258:
                await ws.send_str(
                    self._response_frame(
                        cmd,
                        request_id,
                        {
                            "Ack": 0,
                            "FileList": [
                                {"name": "/local/part.gcode", "type": 1, "FileSize": 1234}
                            ],
                        },
                    )
                )
            else:
                await ws.send_str(self._ack_frame(cmd, request_id))
        return ws

    def _ack_frame(self, cmd: int, request_id: str) -> str:
        return self._response_frame(cmd, request_id, {"Ack": 0})

    def _response_frame(self, cmd: int, request_id: str, body: dict) -> str:
        return json.dumps(
            {
                "Data": {
                    "Cmd": cmd,
                    "Data": body,
                    "RequestID": request_id,
                    "MainboardID": MAINBOARD,
                },
                "Topic": f"sdcp/response/{MAINBOARD}",
            }
        )

    def _attributes_push(self) -> str:
        return json.dumps(
            {
                "Data": {"Cmd": 1, "Data": {"Ack": 0}},
                "Topic": f"sdcp/attributes/{MAINBOARD}",
                "Attributes": self.attributes,
                "MainboardID": MAINBOARD,
            }
        )

    def _status_frame(self) -> str:
        return json.dumps(
            {
                "Status": self.status,
                "Topic": f"sdcp/status/{MAINBOARD}",
                "MainboardID": MAINBOARD,
            }
        )




