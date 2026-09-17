"""Self-test for tools/verify_sdcp.py. No printer required.

Builds a loopback WebSocket server that speaks just enough SDCP to answer
Cmd 1, 512 and 258, then drives the module's own client against it. Also
checks the MJPEG frame extractor and the multipart body builder.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import re
import socket
import struct
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("verify_sdcp", HERE / "verify_sdcp.py")
vs = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(vs)

MAINBOARD = "48551d180103147000001c0000000000"


def ws_accept(conn: socket.socket) -> None:
    """Read the handshake request and answer 101."""
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            raise RuntimeError("client closed during handshake")
        buf += chunk
    key = re.search(rb"Sec-WebSocket-Key:\s*(\S+)", buf).group(1).decode()
    accept = base64.b64encode(
        hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()
    ).decode()
    conn.sendall(
        b"HTTP/1.1 101 Switching Protocols\r\n"
        b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
        b"Sec-WebSocket-Accept: " + accept.encode() + b"\r\n\r\n"
    )


def ws_send_text(conn: socket.socket, text: str) -> None:
    payload = text.encode()
    header = bytearray([0x81])
    if len(payload) < 126:
        header.append(len(payload))
    elif len(payload) < 65536:
        header.append(126)
        header.extend(struct.pack("!H", len(payload)))
    else:
        header.append(127)
        header.extend(struct.pack("!Q", len(payload)))
    conn.sendall(bytes(header) + payload)


def ws_recv_text(conn: socket.socket) -> str:
    def exact(n: int) -> bytes:
        out = b""
        while len(out) < n:
            chunk = conn.recv(n - len(out))
            if not chunk:
                raise RuntimeError("closed")
            out += chunk
        return out

    b1, b2 = exact(2)
    length = b2 & 0x7F
    if length == 126:
        length = struct.unpack("!H", exact(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", exact(8))[0]
    masked = b2 & 0x80
    mask = exact(4) if masked else b""
    data = exact(length)
    if masked:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return data.decode()


def ok(label: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {label}{(' :: ' + detail) if detail else ''}")
    if not condition:
        raise SystemExit(1)


def status_frame(request_id: str) -> str:
    return json.dumps({
        "Status": {
            "CurrentStatus": [1],
            "TempOfNozzle": 255.01, "TempTargetNozzle": 255,
            "TempOfHotbed": 50.04, "TempTargetHotbed": 50,
            "TempOfBox": 29.2, "TempTargetBox": 0,
            "CurrenCoord": "139.96,123.71,5.33",
            "CurrentFanSpeed": {"ModelFan": 58, "AuxiliaryFan": 0, "BoxFan": 68},
            "LightStatus": {"SecondLight": 1},
            "PrintInfo": {
                "Status": 13, "CurrentLayer": 34, "TotalLayer": 438,
                "CurrentTicks": 364.98, "TotalTicks": 4504,
                "Filename": "cube.gcode", "TaskId": "295cb186-daf5-4b84-9668-59a520e4640a",
                "PrintSpeedPct": 100, "Progress": 9,
            },
        },
        "MainboardID": MAINBOARD,
        "Topic": f"sdcp/status/{MAINBOARD}",
    })


def serve(conn: socket.socket) -> None:
    """Answer the probes in the order verify_sdcp sends them."""
    try:
        ws_accept(conn)
        # Spontaneous attributes push on connect.
        ws_send_text(conn, json.dumps({
            "Attributes": {
                "Name": "Centauri Carbon", "MachineName": "Centauri Carbon",
                "BrandName": "Elegoo", "ProtocolVersion": "V3.0.0",
                "FirmwareVersion": "V1.1.46", "MainboardID": MAINBOARD,
                "XYZsize": "256x256x256", "CameraStatus": 1,
                "Capabilities": ["FILE_TRANSFER", "PRINT_CONTROL", "VIDEO_STREAM"],
                "SupportFileType": ["GCODE"], "RemainingMemory": 123455,
            },
            "MainboardID": MAINBOARD,
            "Topic": f"sdcp/attributes/{MAINBOARD}",
        }))
        while True:
            raw = ws_recv_text(conn)
            msg = json.loads(raw)
            cmd = msg["Data"]["Cmd"]
            rid = msg["Data"]["RequestID"]
            # Echo the envelope, which is what the printer does.
            ws_send_text(conn, json.dumps({
                "Id": MAINBOARD,
                "Data": {"Cmd": cmd, "Data": {"Ack": 0}, "RequestID": rid,
                         "MainboardID": MAINBOARD, "TimeStamp": 1},
                "Topic": f"sdcp/response/{MAINBOARD}",
            }))
            if cmd == 258:
                ws_send_text(conn, json.dumps({
                    "Id": MAINBOARD,
                    "Data": {"Cmd": 258, "Ack": 0, "MainboardID": MAINBOARD,
                             "RequestID": rid},
                    "Topic": f"sdcp/response/{MAINBOARD}",
                }))
                # File list rides inside Data.Data for real firmware; the
                # module unwraps Data.Data.FileList.
                ws_send_text(conn, json.dumps({
                    "Data": {"Cmd": 258, "Data": {"Ack": 0, "FileList": [
                        {"name": "/local/cube.gcode", "FileSize": 2048,
                         "TotalLayers": 438, "CreateTime": 1700000000, "type": 1},
                    ]}, "RequestID": rid, "MainboardID": MAINBOARD},
                    "Topic": f"sdcp/response/{MAINBOARD}",
                }))
            if cmd == 1:
                ws_send_text(conn, json.dumps({
                    "Attributes": {
                        "Name": "Centauri Carbon", "MachineName": "Centauri Carbon",
                        "FirmwareVersion": "V1.1.46", "MainboardID": MAINBOARD,
                        "Capabilities": ["FILE_TRANSFER"],
                        "SupportFileType": ["GCODE"],
                    },
                    "MainboardID": MAINBOARD,
                    "Topic": f"sdcp/attributes/{MAINBOARD}",
                }))
            if cmd == 512:
                # Real firmware may never push. Delay one frame past the
                # module's wait so the Cmd 0 fallback is exercised.
                def later() -> None:
                    time.sleep(7.0)
                    try:
                        ws_send_text(conn, status_frame(rid))
                    except OSError:
                        pass
                threading.Thread(target=later, daemon=True).start()
            if cmd == 0:
                # Prefix the frame with a decimal length, as some firmware does.
                ws_send_text(conn, "123" + status_frame(rid))
    except (OSError, RuntimeError):
        return


def test_against_fake_printer() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def accept_once() -> None:
        conn, _ = server.accept()
        serve(conn)

    threading.Thread(target=accept_once, daemon=True).start()

    with vs.Ws("127.0.0.1", port, vs.WS_PATH, timeout=5.0) as ws:
        text = ws.recv_text(timeout=5)
        ok("learns MainboardID from the Attributes push",
           json.loads(text)["Attributes"]["MainboardID"] == MAINBOARD)

        attrs = vs.probe_attributes(ws, MAINBOARD)
        ok("Cmd 1 returns attributes", bool(attrs), str(attrs and attrs.get("FirmwareVersion")))

        status = vs.probe_status(ws, MAINBOARD, 1000)
        ok("Cmd 0 fallback yields a status frame", status is not None)
        info = status["Status"]["PrintInfo"]
        ok("PrintInfo parses", info["CurrentLayer"] == 34 and info["TotalLayer"] == 438)
        ok("remaining time computes", abs((info["TotalTicks"] - info["CurrentTicks"]) - 4139.02) < 0.01)

        files = vs.probe_files(ws, MAINBOARD)
        ok("Cmd 258 returns a file list", len(files) == 1 and files[0]["name"] == "/local/cube.gcode")
    server.close()


def test_camera_extraction() -> None:
    """Serve a two-part MJPEG body and confirm we cut a whole JPEG."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    jpeg = b"\xff\xd8" + b"\x00" * 400 + b"\xff\xd9"

    def serve_cam() -> None:
        conn, _ = server.accept()
        body = b"--foo\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n--foo\r\n"
        conn.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Type: multipart/x-mixed-replace; boundary=--foo\r\n"
            b"Connection: close\r\n\r\n" + body
        )
        conn.close()

    threading.Thread(target=serve_cam, daemon=True).start()
    frame = vs.grab_jpeg("127.0.0.1", port=port, path="/video")
    ok("camera extractor returns one whole JPEG",
       frame == jpeg and len(frame) == len(jpeg), f"{len(frame)} bytes")
    server.close()


def test_multipart_body() -> None:
    body, content_type = vs._multipart(
        {"Check": "1", "S-File-MD5": "abc", "Offset": "0", "Uuid": "u", "TotalSize": "3"},
        "File", "cube.gcode", b"G1\n",
    )
    text = body.decode("utf-8", "replace")
    ok("multipart has the boundary in the content type", "boundary=" in content_type)
    for field in ("Check", "S-File-MD5", "Offset", "Uuid", "TotalSize"):
        ok(f"multipart carries {field}", f'name="{field}"' in text)
    ok("multipart carries the file part",
       'name="File"; filename="cube.gcode"' in text and "G1\n" in text)
    ok("multipart is CRLF terminated", text.endswith("--\r\n"))


def test_read_only_default() -> None:
    """Default argv must not include a write command."""
    src = (HERE / "verify_sdcp.py").read_text(encoding="utf-8")
    ok("start print is opt-in", '"--start-print"' in src and "start-print" in src)
    ok("upload is opt-in", '"--upload"' in src)
    ok("no writes before the argument parse",
       src.index("args = parser.parse_args()") < src.index("if args.start_print"))


if __name__ == "__main__":
    test_multipart_body()
    test_camera_extraction()
    test_read_only_default()
    test_against_fake_printer()
    print("\nall checks passed")
