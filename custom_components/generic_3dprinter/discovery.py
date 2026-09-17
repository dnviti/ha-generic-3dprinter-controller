"""Fingerprint a printer on the local network.

Discovery exists to save the user from guessing a protocol, not to guess one for
them. A probe that answers identifies a candidate, the config flow shows what it
found, and the user confirms. Nothing here sends a command to a printer, because a
wrong guess on a machine that treats an unknown command as fatal is how hardware
gets bricked.

Two probes are used together:

* a UDP broadcast carrying the SDCP discovery literal, which Elegoo printers answer
  with their mainboard id and firmware version;
* a TCP connect plus a small HTTP fingerprint, which identifies Moonraker,
  OctoPrint and PrusaLink from their own headers and endpoints.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from dataclasses import dataclass, field
from typing import Any, Final

import aiohttp

from .const import ProtocolId

_LOGGER = logging.getLogger(__name__)

SDCP_DISCOVERY_PORT: Final = 3000
SDCP_DISCOVERY_PROBE: Final = b"M99999"
DISCOVERY_TIMEOUT: Final = 3.0
CONNECT_TIMEOUT: Final = 0.6
HTTP_TIMEOUT: Final = 3.0
MAX_RESPONSE_BYTES: Final = 65536

#: Ports worth a connect probe, with the protocol each one suggests. A port that
#: answers is a hint, never a conclusion.
CANDIDATE_PORTS: Final[tuple[tuple[int, ProtocolId], ...]] = (
    (7125, ProtocolId.MOONRAKER),
    (5000, ProtocolId.OCTOPRINT),
    (80, ProtocolId.WEB_ONLY),
    (3030, ProtocolId.SDCP_CC1),
)

#: HTTP fingerprints, checked against the first response body and headers.
HTTP_FINGERPRINTS: Final[tuple[tuple[str, ProtocolId], ...]] = (
    ("octoprint", ProtocolId.OCTOPRINT),
    ("prusalink", ProtocolId.PRUSALINK),
    ("prusa", ProtocolId.PRUSALINK),
    ("moonraker", ProtocolId.MOONRAKER),
    ("klipper", ProtocolId.MOONRAKER),
    ("duet", ProtocolId.DUET),
    ("reprap", ProtocolId.DUET),
    ("elegoo", ProtocolId.SDCP_CC1),
)


@dataclass(slots=True)
class DiscoveryResult:
    """What a probe learned about one host."""

    host: str
    protocol: ProtocolId
    #: Every protocol whose hint was seen, best guess first.
    candidates: list[ProtocolId] = field(default_factory=list)
    mainboard_id: str | None = None
    firmware: str | None = None
    model: str | None = None
    open_ports: list[int] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe summary for the config flow."""
        return {
            "host": self.host,
            "protocol": self.protocol.value,
            "candidates": [item.value for item in self.candidates],
            "mainboard_id": self.mainboard_id,
            "firmware": self.firmware,
            "model": self.model,
            "open_ports": self.open_ports,
            "evidence": self.evidence,
        }


class _SdcpDiscoveryProtocol(asyncio.DatagramProtocol):
    """Collect the reply the printer broadcasts back to the discovery probe."""

    def __init__(self) -> None:
        """Create an empty reply holder."""
        self.reply: dict[str, Any] | None = None
        self.done: asyncio.Event = asyncio.Event()

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """Store the first decodable reply."""
        try:
            payload = json.loads(data.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            return
        if isinstance(payload, dict) and self.reply is None:
            self.reply = payload
            self.done.set()

    def error_received(self, exc: Exception) -> None:
        """Record a transport error as "no reply"."""
        _LOGGER.debug("SDCP discovery transport error: %s", exc)


async def async_discover_sdcp(timeout: float = DISCOVERY_TIMEOUT) -> DiscoveryResult | None:
    """Broadcast the Elegoo discovery probe and parse the first reply."""
    loop = asyncio.get_running_loop()
    transport = None
    try:
        transport, protocol = await loop.create_datagram_endpoint(
            _SdcpDiscoveryProtocol,
            local_addr=("0.0.0.0", 0),
            allow_broadcast=True,
            family=socket.AF_INET,
        )
        transport.sendto(SDCP_DISCOVERY_PROBE, ("255.255.255.255", SDCP_DISCOVERY_PORT))
    except OSError as err:
        _LOGGER.debug("SDCP discovery could not open a socket: %s", err)
        return None

    try:
        await asyncio.wait_for(protocol.done.wait(), timeout=timeout)
    except TimeoutError:
        return None
    finally:
        if transport is not None:
            transport.close()

    reply = protocol.reply
    if not reply:
        return None

    data = reply.get("Data") if isinstance(reply.get("Data"), dict) else reply
    if not isinstance(data, dict):
        return None

    mainboard = str(data.get("MainboardID") or "") or None
    return DiscoveryResult(
        host=str(data.get("MainboardIP") or "") or _reply_host(reply),
        protocol=ProtocolId.SDCP_CC1,
        candidates=[ProtocolId.SDCP_CC1],
        mainboard_id=mainboard,
        firmware=str(data.get("FirmwareVersion") or "") or None,
        model=str(data.get("MachineName") or data.get("Name") or "") or None,
        evidence=["answered the UDP discovery probe on port 3000"],
    )


def _reply_host(reply: dict[str, Any]) -> str:
    data = reply.get("Data")
    if isinstance(data, dict):
        return str(data.get("MainboardIP") or "")
    return ""


async def async_probe_ports(host: str) -> list[int]:
    """Return every candidate port that accepts a TCP connection."""

    async def probe(port: int) -> int | None:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=CONNECT_TIMEOUT
            )
        except (OSError, TimeoutError):
            return None
        writer.close()
        del reader
        try:
            await writer.wait_closed()
        except OSError:
            pass
        return port

    results = await asyncio.gather(*(probe(port) for port, _ in CANDIDATE_PORTS))
    return [port for port in results if port is not None]


async def async_fingerprint_http(host: str, port: int) -> tuple[ProtocolId | None, list[str]]:
    """Ask a port for its front page and match the body against known products."""
    url = f"http://{host}:{port}/"
    evidence: list[str] = []
    body = ""
    headers: dict[str, str] = {}
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
        ) as session:
            async with session.get(url, allow_redirects=True) as response:
                body = (await response.content.read(MAX_RESPONSE_BYTES)).decode(
                    "utf-8", "replace"
                )
                headers = {key.lower(): value for key, value in response.headers.items()}
    except (aiohttp.ClientError, TimeoutError) as err:
        _LOGGER.debug("HTTP fingerprint of %s failed: %s", url, err)
        return None, evidence

    haystack = f"{body}\n{' '.join(f'{k}: {v}' for k, v in headers.items())}".lower()
    for marker, protocol in HTTP_FINGERPRINTS:
        if marker in haystack:
            evidence.append(f"{url} mentions {marker!r}")
            return protocol, evidence
    if "server" in headers:
        evidence.append(f"{url} answered with Server: {headers['server']}")
    return None, evidence


async def async_discover_host(host: str) -> DiscoveryResult | None:
    """Probe one host and return the best candidate, or ``None`` when nothing answers."""
    ports = await async_probe_ports(host)
    if not ports:
        return None

    evidence = [f"open ports: {', '.join(str(port) for port in ports)}"]
    candidates: list[ProtocolId] = []
    for port, hint in CANDIDATE_PORTS:
        if port in ports and hint not in candidates:
            candidates.append(hint)

    # An HTTP fingerprint outranks a bare open port, because two products can share
    # a port while only one of them answers to its own name.
    for port in (7125, 5000, 80, 443):
        if port not in ports and not (port == 443):
            continue
        protocol, note = await async_fingerprint_http(host, port)
        evidence.extend(note)
        if protocol is not None:
            candidates = [protocol, *[item for item in candidates if item != protocol]]
            break

    if not candidates:
        return None

    return DiscoveryResult(
        host=host,
        protocol=candidates[0],
        candidates=candidates,
        open_ports=ports,
        evidence=evidence,
    )
