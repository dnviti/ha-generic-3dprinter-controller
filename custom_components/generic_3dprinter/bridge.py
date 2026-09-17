"""WebSocket bridge for a proxied printer page.

A printer's own page is rarely just HTTP. The Centauri Carbon's control channel is
a WebSocket on port 3030, and a proxied page that cannot open it shows a UI that
looks alive and does nothing.

``HomeAssistantView`` cannot serve this. It dispatches only ``get``, ``post``,
``put``, ``delete``, ``patch``, ``head`` and ``options``, and although a ``get``
handler *may* return a ``WebSocketResponse``, the token in the path is what
authorises the request, so the route is registered directly on the aiohttp router
with its own token check. Home Assistant deliberately leaves that router unfrozen
(the app's ``start`` replaces ``_router.freeze`` with a no-op) precisely so
components discovered after boot can add routes.

One upstream socket is opened per viewer, which is correct here: the printer's own
page has one socket of its own, and sharing it would deliver one client's messages
to another. The camera is the shared resource, and it is shared in
:class:`~.runtime.CameraHub`.
"""

from __future__ import annotations

import logging
from contextlib import suppress

from aiohttp import WSMsgType, web
from homeassistant.core import HomeAssistant

from .const import DATA_ROUTES_REGISTERED, DOMAIN, RESOURCE_WEB
from .runtime import get_runtime
from .security import InvalidToken

_LOGGER = logging.getLogger(__name__)

WS_ROUTE: str = f"/api/{DOMAIN}/{{entry_id}}/ws/{{token}}"

#: Close codes used when the bridge refuses a connection.
CLOSE_UNAUTHORIZED = 4401
CLOSE_NOT_FOUND = 4404
CLOSE_UPSTREAM = 4502

#: Message ceiling in both directions. The SDCP status frames are small; the
#: printer's own page can send larger ones, and this bounds memory per socket.
MAX_MESSAGE_BYTES = 1024 * 1024


def async_register_websocket_bridge(hass: HomeAssistant) -> None:
    """Register the bridge route once per Home Assistant instance."""
    if hass.data.get(DATA_ROUTES_REGISTERED):
        return
    hass.http.app.router.add_get(WS_ROUTE, _make_handler(hass))
    hass.data[DATA_ROUTES_REGISTERED] = True
    _LOGGER.debug("registered the printer WebSocket bridge at %s", WS_ROUTE)


def _make_handler(hass: HomeAssistant):
    async def handler(request: web.Request) -> web.StreamResponse:
        return await _async_bridge(hass, request)

    return handler


async def _async_bridge(hass: HomeAssistant, request: web.Request) -> web.StreamResponse:
    """Authorise the upgrade and bridge both directions until either side closes."""
    entry_id = request.match_info["entry_id"]
    token = request.match_info["token"]
    runtime = get_runtime(hass, entry_id)
    if runtime is None:
        return web.Response(status=404, text="no such printer")

    try:
        media = runtime.tokens.async_verify(token, entry_id=entry_id)
    except InvalidToken as err:
        _LOGGER.debug("bridge: rejected a connection: %s", err)
        return web.Response(status=403, text="invalid token")

    if media.resource != RESOURCE_WEB:
        return web.Response(status=403, text="token cannot be used here")

    upstream_port = media.port or runtime.config.port
    if upstream_port is None or not runtime.web_proxy.allow_port(upstream_port):
        return web.Response(status=404, text="port is not proxied")

    scheme = "wss" if runtime.config.tls else "ws"
    upstream_url = f"{scheme}://{runtime.config.host}:{upstream_port}/websocket"

    client = web.WebSocketResponse(heartbeat=None, max_msg_size=MAX_MESSAGE_BYTES)
    await client.prepare(request)

    try:
        async with runtime.session.ws_connect(
            upstream_url, heartbeat=None, max_msg_size=MAX_MESSAGE_BYTES
        ) as upstream:
            _LOGGER.debug(
                "%s: bridged a WebSocket to %s", runtime.config.name, upstream_url
            )
            await _async_pump(client, upstream)
    except Exception as err:  # noqa: BLE001 - a failed bridge must not kill the view
        _LOGGER.debug(
            "%s: WebSocket bridge to %s failed: %s", runtime.config.name, upstream_url, err
        )
        with suppress(ConnectionResetError, RuntimeError):
            await client.close(code=CLOSE_UPSTREAM, message=b"the printer refused the bridge")

    with suppress(ConnectionResetError, RuntimeError):
        await client.close()
    return client


async def _async_pump(
    client: web.WebSocketResponse, upstream
) -> None:
    """Relay messages both ways until one side stops.

    Text frames stay text and binary stays binary, because SDCP is JSON and some
    printers send a binary camera or audio channel over the same socket. Close and
    error frames terminate the relay.
    """
    import asyncio

    async def client_to_upstream() -> None:
        async for message in client:
            if message.type is WSMsgType.TEXT:
                await upstream.send_str(message.data)
            elif message.type is WSMsgType.BINARY:
                await upstream.send_bytes(message.data)
            else:
                return

    async def upstream_to_client() -> None:
        async for message in upstream:
            if message.type is WSMsgType.TEXT:
                await client.send_str(message.data)
            elif message.type is WSMsgType.BINARY:
                await client.send_bytes(message.data)
            else:
                return

    tasks = [
        asyncio.create_task(client_to_upstream()),
        asyncio.create_task(upstream_to_client()),
    ]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done | pending:
            with suppress(asyncio.CancelledError, Exception):
                await task
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
