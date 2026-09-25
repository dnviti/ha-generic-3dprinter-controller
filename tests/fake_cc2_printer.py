"""A fake Centauri Carbon 2 for the test suite: its broker, its camera and its upload.

The printer is the broker on the real hardware, so the fake is one too. It speaks
just enough MQTT 3.1.1 for the adapter and answers on the topics the real printer
uses, with the behaviours that matter most:

* in cloud mode it accepts the connection and the subscriptions and then answers
  nothing at all, which is what a live printer did;
* a resume is acknowledged only once the print has resumed, which on hardware
  takes minutes, so the fake never acknowledges it;
* status pushes are deltas numbered in sequence, merged into the full status.
"""

from __future__ import annotations

import asyncio
import copy
import json
import struct
from contextlib import suppress
from typing import Any

from custom_components.generic_3dprinter.mqtt_client import (
    CONNACK,
    CONNECT,
    DISCONNECT,
    PINGREQ,
    PINGRESP,
    PUBLISH,
    SUBACK,
    SUBSCRIBE,
    decode_publish,
    encode_publish,
    packet,
    read_packet,
)

SERIAL = "F01BXKSWL13QAZJ"
DEFAULT_CODE = "123456"

#: A full status in the shape a live printer sends for method 1002 on firmware
#: 02.01.00.00, with the job fields of a print in progress. The field names are the
#: ones measured on hardware: ``gcode_move`` and ``tool_head``, not the
#: ``gcode_move_inf`` and ``toolhead`` the community documentation gives.
FULL_STATUS: dict[str, Any] = {
    "machine_status": {
        "status": 2,
        "sub_status": 2075,
        "sub_status_reason_code": 0,
        "exception_status": [],
        "progress": 45,
    },
    "print_status": {
        "bed_mesh_detect": True,
        "enable": True,
        "filament_detect": True,
        "filename": "benchy.gcode",
        "uuid": "b52af24c-764e-4092-8a50-00e5f8f02b46",
        "current_layer": 225,
        "total_layer": 500,
        "print_duration": 3600,
        "total_duration": 8000,
        "remaining_time_sec": 4400,
        "progress": 45,
        "state": "printing",
    },
    "extruder": {"filament_detect_enable": 1, "filament_detected": 1, "target": 220, "temperature": 215},
    "heater_bed": {"target": 60, "temperature": 58},
    "ztemperature_sensor": {
        "measured_max_temperature": 0,
        "measured_min_temperature": 0,
        "temperature": 33,
    },
    "fans": {
        "fan": {"speed": 255.0},
        "aux_fan": {"speed": 178.0},
        "box_fan": {"speed": 25.0},
        "heater_fan": {"speed": 255.0},
        "controller_fan": {"speed": 255.0},
    },
    "led": {"status": 1},
    "gcode_move": {"extruder": 138.87, "speed": 1500, "speed_mode": 1, "x": 88.148, "y": 139.946, "z": 1.6},
    "tool_head": {"homed_axes": "xyz"},
    "external_device": {"camera": True, "type": "0303", "u_disk": False},
}

ATTRIBUTES: dict[str, Any] = {
    "hostname": "CC2 QAZJ",
    "machine_model": "Centauri Carbon 2",
    "sn": SERIAL,
    "software_version": {"ota_version": "02.01.00.00", "mcu_version": "00.00.00.00"},
    "camera_connected": True,
}


def _merge(base: dict[str, Any], delta: dict[str, Any]) -> None:
    for key, value in delta.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value


class _Session:
    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self.writer = writer
        self.client_id = ""
        self.topics: set[str] = set()

    async def send(self, data: bytes) -> None:
        with suppress(OSError, RuntimeError):
            self.writer.write(data)
            await self.writer.drain()


class FakeCC2Printer:
    """A loopback Centauri Carbon 2."""

    def __init__(self, *, access_code: str = DEFAULT_CODE, max_clients: int = 4) -> None:
        """Create a printer in LAN-only mode with a print in progress."""
        self.access_code = access_code
        self.max_clients = max_clients
        #: ``False`` is cloud mode: the broker works and the printer never answers.
        self.answering = True
        self.status = copy.deepcopy(FULL_STATUS)
        self.attributes = copy.deepcopy(ATTRIBUTES)
        self.files: list[dict[str, Any]] = [
            {"filename": "benchy.gcode", "type": "file", "size": 1234567, "create_time": 1706900000},
            {"filename": "models", "type": "folder"},
            {"filename": "cube.gcode", "type": "file", "size": 42},
        ]
        #: Method to the error code it is refused with.
        self.refuse: dict[int, int] = {}
        #: Methods the printer never acknowledges. A resume is one.
        self.silent: set[int] = {1023}
        self.requests: list[dict[str, Any]] = []
        self.pings = 0
        self.registered: list[str] = []
        self.connects = 0
        self.uploads: list[dict[str, Any]] = []
        self.camera_connections = 0
        self.port = 0
        self.camera_port = 0
        self.upload_port = 0
        self._sequence = 0
        self._sessions: set[_Session] = set()
        self._server: asyncio.base_events.Server | None = None
        self._runners: list[Any] = []

    @property
    def methods(self) -> list[int]:
        """Return the method of every request received, in order."""
        return [int(item["method"]) for item in self.requests]

    def params_of(self, method: int) -> list[dict[str, Any]]:
        """Return the params of every request with ``method``."""
        return [item.get("params", {}) for item in self.requests if item["method"] == method]

    # ---------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        """Start the broker and the two HTTP servers, keeping earlier ports."""
        from aiohttp import web

        self._server = await asyncio.start_server(self._serve, "127.0.0.1", self.port or 0)
        self.port = self._server.sockets[0].getsockname()[1]

        camera = web.Application()
        camera.router.add_route("GET", "/{tail:.*}", self._camera)
        self.camera_port = await self._bind(camera, self.camera_port)

        upload = web.Application()
        upload.router.add_put("/upload", self._upload)
        self.upload_port = await self._bind(upload, self.upload_port)

    async def _bind(self, app: Any, port: int) -> int:
        from aiohttp import web

        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", port or 0)
        await site.start()
        self._runners.append(runner)
        return site._server.sockets[0].getsockname()[1]  # noqa: SLF001

    async def stop(self) -> None:
        """Power off: every client connection dies with the printer."""
        for session in list(self._sessions):
            session.writer.close()
        self._sessions.clear()
        if self._server is not None:
            self._server.close()
            with suppress(Exception):
                await asyncio.wait_for(self._server.wait_closed(), timeout=2)
            self._server = None
        for runner in self._runners:
            with suppress(Exception):
                await runner.cleanup()
        self._runners.clear()
        self.registered.clear()

    # ------------------------------------------------------------------ broker

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        session = _Session(writer)
        try:
            kind, _flags, body = await read_packet(reader)
            if kind != CONNECT:
                return
            session.client_id, password = self._parse_connect(body)
            self.connects += 1
            if password != self.access_code:
                await session.send(packet(CONNACK, 0, b"\x00\x05"))
                return
            await session.send(packet(CONNACK, 0, b"\x00\x00"))
            self._sessions.add(session)
            while True:
                kind, flags, body = await read_packet(reader)
                if kind == SUBSCRIBE:
                    await self._subscribe(session, body)
                elif kind == PUBLISH:
                    topic, payload, _qos, _id = decode_publish(flags, body)
                    await self._publish_in(session, topic, json.loads(payload))
                elif kind == PINGREQ:
                    await session.send(packet(PINGRESP, 0))
                elif kind == DISCONNECT:
                    return
        except (asyncio.IncompleteReadError, ConnectionError, OSError):
            return
        finally:
            self._sessions.discard(session)
            if session.client_id in self.registered:
                self.registered.remove(session.client_id)
            writer.close()

    @staticmethod
    def _parse_connect(body: bytes) -> tuple[str, str | None]:
        offset = 2 + struct.unpack("!H", body[:2])[0]
        flags = body[offset + 1]
        offset += 4

        def string() -> str:
            nonlocal offset
            (length,) = struct.unpack("!H", body[offset : offset + 2])
            value = body[offset + 2 : offset + 2 + length].decode()
            offset += 2 + length
            return value

        client_id = string()
        username = string() if flags & 0x80 else None
        password = string() if flags & 0x40 else None
        assert username in (None, "elegoo")
        return client_id, password

    async def _subscribe(self, session: _Session, body: bytes) -> None:
        packet_id = body[:2]
        offset, granted = 2, b""
        while offset < len(body):
            (length,) = struct.unpack("!H", body[offset : offset + 2])
            session.topics.add(body[offset + 2 : offset + 2 + length].decode())
            offset += 2 + length + 1
            granted += b"\x00"
        await session.send(packet(SUBACK, 0, packet_id + granted))

    async def deliver(self, topic: str, message: dict[str, Any]) -> None:
        """Publish ``message`` to every client subscribed to ``topic``."""
        data = encode_publish(topic, json.dumps(message).encode())
        for session in list(self._sessions):
            if topic in session.topics:
                await session.send(data)

    async def _publish_in(self, session: _Session, topic: str, message: dict[str, Any]) -> None:
        if not self.answering:
            return
        if topic == f"elegoo/{SERIAL}/api_register":
            client_id = message["client_id"]
            if client_id in self.registered or len(self.registered) < self.max_clients:
                self.registered.append(client_id)
                error = "ok"
            else:
                error = "too many clients"
            await self.deliver(
                f"elegoo/{SERIAL}/{message['request_id']}/register_response",
                {"client_id": client_id, "error": error},
            )
            return
        if not topic.endswith("/api_request"):
            return
        client_id = topic.split("/")[2]
        reply_topic = f"elegoo/{SERIAL}/{client_id}/api_response"
        if message.get("type") == "PING":
            self.pings += 1
            await self.deliver(reply_topic, {"type": "PONG"})
            return
        self.requests.append(message)
        method = int(message["method"])
        if method in self.silent:
            return
        await self.deliver(
            reply_topic, {"id": message["id"], "method": method, "result": self._result(method)}
        )

    def _result(self, method: int) -> dict[str, Any]:
        if method in self.refuse:
            return {"error_code": self.refuse[method]}
        if method == 1001:
            return {"error_code": 0, **self.attributes}
        if method == 1002:
            return {"error_code": 0, **copy.deepcopy(self.status)}
        if method == 1031 and self.status["machine_status"]["status"] != 2:
            # Measured: the speed mode is refused outside a print, "not printing".
            return {"error_code": 1010}
        if method == 1042:
            return {"error_code": 0, "url": f"http://127.0.0.1:{self.camera_port}/?action=stream"}
        if method == 1044:
            return {"error_code": 0, "file_list": self.files, "offset": 0, "total": len(self.files)}
        return {"error_code": 0}

    async def push_delta(self, delta: dict[str, Any], *, sequence: int | None = None) -> None:
        """Merge a delta into the status and push it, as the printer does on a change."""
        _merge(self.status, delta)
        self._sequence = self._sequence + 1 if sequence is None else sequence
        await self.deliver(
            f"elegoo/{SERIAL}/api_status",
            {"id": self._sequence, "method": 6000, "result": {"error_code": 0, **delta}},
        )

    # -------------------------------------------------------------------- HTTP

    async def _camera(self, request: Any) -> Any:
        from aiohttp import web

        self.camera_connections += 1
        response = web.StreamResponse(
            headers={"Content-Type": "multipart/x-mixed-replace; boundary=--frame_boundary"}
        )
        await response.prepare(request)
        with suppress(ConnectionError, RuntimeError, asyncio.CancelledError):
            for index in range(50):
                frame = b"\xff\xd8" + b"cc2-frame-%03d" % index + b"\xff\xd9"
                await response.write(
                    b"--frame_boundary\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                )
                await asyncio.sleep(0.02)
        return response

    async def _upload(self, request: Any) -> Any:
        from aiohttp import web

        body = await request.read()
        peer = request.transport.get_extra_info("peername") if request.transport else None
        self.uploads.append({"headers": dict(request.headers), "body": body, "peer": peer})
        end = int(request.headers["Content-Range"].split(" ")[1].split("/")[0].split("-")[1])
        return web.json_response({"error_code": 0, "offset": end})
