"""A fake SDCP printer for the test suite.

It speaks the frame shapes a real Centauri Carbon produces, including the two
details that matter most: a command is acknowledged in one frame and its payload
can arrive in another, and the camera lives on a port of its own. Collapsing either
one is how a fake passes while the real adapter hangs.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress

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
        self.camera_port: int = 0
        #: How many clients have opened the camera stream, and how many frames it
        #: has produced. Together they prove a live stream rather than a still.
        self.camera_connections = 0
        self.camera_frames_sent = 0
        self._server = None
        self._camera_server = None

    async def start(self) -> str:
        """Start the server and return its ``http://`` URL.

        The camera gets its own origin, exactly as the real hardware does: the
        control socket is on one port and the MJPEG stream on another, which is why
        the adapter is configured with a camera port rather than assuming one.
        """
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

        camera_app = web.Application()
        camera_app.router.add_get("/video", self._handle_camera)
        camera_runner = web.AppRunner(camera_app)
        await camera_runner.setup()
        camera_site = web.TCPSite(camera_runner, "127.0.0.1", 0)
        await camera_site.start()
        self._camera_server = camera_runner
        self.camera_port = camera_site._server.sockets[0].getsockname()[1]  # noqa: SLF001
        return self.url

    async def stop(self) -> None:
        """Stop both servers."""
        for attribute in ("_server", "_camera_server"):
            runner = getattr(self, attribute, None)
            if runner is not None:
                await runner.cleanup()
                setattr(self, attribute, None)

    async def _handle_camera(self, request):
        """Stream multipart JPEG until the client goes away.

        Frames are produced on a timer rather than on connect, so a test can prove
        the stream is live by watching more than one distinct frame arrive.
        """
        from aiohttp import web

        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "multipart/x-mixed-replace; boundary=--foo",
                "Cache-Control": "no-cache",
            },
        )
        await response.prepare(request)
        self.camera_connections += 1
        try:
            for index in range(120):
                self.camera_frames_sent += 1
                frame = self._jpeg(index)
                part = (
                    b"--foo\r\nContent-Type: image/jpeg\r\nContent-Length: "
                    + str(len(frame)).encode("ascii")
                    + b"\r\n\r\n"
                    + frame
                    + b"\r\n"
                )
                await response.write(part)
                await asyncio.sleep(0.05)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            with suppress(ConnectionResetError, RuntimeError):
                await response.write_eof()
        return response

    @staticmethod
    def _jpeg(index: int) -> bytes:
        """Return a tiny but structurally valid JPEG whose bytes vary per frame.

        The marker bytes are what the adapter scans for, and the payload byte makes
        two frames distinguishable so a test can tell a live stream from a still.
        """
        payload = b"fake-jpeg-%03d" % index
        return b"\xff\xd8" + payload + b"\xff\xd9"

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




