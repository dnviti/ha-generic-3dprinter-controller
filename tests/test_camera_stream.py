"""The camera stream must be live, and must use one upstream connection.

Two properties are load-bearing here and neither is visible from the outside:

* **Live.** A still snapshot changes only when something polls it. A user watching a
  print wants the picture to move, so the endpoint the card loads has to be a
  continuous multipart stream.
* **One upstream connection.** The Centauri Carbon's camera server keeps a handful
  of connection slots and leaks them on disconnect, so two viewers must share one
  upstream connection rather than open one each. On that hardware this is a
  correctness requirement, not an optimisation.

The tests drive the real camera view over HTTP against a fake printer whose camera
produces a distinguishable frame every 50 ms, so "live" is measured rather than
asserted.

Every test here ends by unloading its entry, because an open camera stream holds an
upstream socket that Home Assistant's shutdown would otherwise wait on.
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import pytest
from aiohttp import web
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components"))

from custom_components.generic_3dprinter.const import DATA_RUNTIMES  # noqa: E402

JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"


def frames_in(buffer: bytes) -> list[bytes]:
    """Return every complete JPEG in ``buffer``."""
    frames: list[bytes] = []
    position = 0
    while True:
        start = buffer.find(JPEG_SOI, position)
        if start < 0:
            return frames
        end = buffer.find(JPEG_EOI, start + 2)
        if end < 0:
            return frames
        frames.append(buffer[start : end + 2])
        position = end + 2


@pytest.fixture(name="camera_app")
async def camera_app_fixture(hass: HomeAssistant, aiohttp_client):
    """Serve the integration's own camera view on a real socket.

    The view's ``get`` takes the URL's path parameters, so the route is registered
    with the view's own pattern and the matches are handed through. Calling
    ``handle`` directly would leave ``entry_id`` and ``token`` unbound.
    """
    from custom_components.generic_3dprinter.views import CameraStreamView

    view = CameraStreamView(hass)

    async def handler(request: web.Request):
        return await view.get(request, **request.match_info)

    app = web.Application()
    app.router.add_get(CameraStreamView.url, handler)
    return await aiohttp_client(app)


@asynccontextmanager
async def loaded(hass: HomeAssistant, entry: MockConfigEntry):
    """Load an entry and always unload it again before the test finishes."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    try:
        yield hass.data[DATA_RUNTIMES][entry.entry_id]
    finally:
        with suppress(Exception):
            await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_the_camera_endpoint_is_a_live_multipart_stream(
    hass: HomeAssistant, config_entry: MockConfigEntry, printer, camera_app
) -> None:
    async with loaded(hass, config_entry) as runtime:
        url = runtime.describe()["camera_url"]
        response = await camera_app.get(url)
        try:
            assert response.status == 200
            assert "multipart/x-mixed-replace" in response.headers["Content-Type"]

            buffer = bytearray()
            deadline = asyncio.get_running_loop().time() + 10.0
            while asyncio.get_running_loop().time() < deadline:
                chunk = await asyncio.wait_for(response.content.read(4096), timeout=5)
                if not chunk:
                    break
                buffer.extend(chunk)
                # Three distinct frames means the endpoint kept producing after the
                # first, which a still-image endpoint cannot do.
                if len({bytes(frame) for frame in frames_in(bytes(buffer))}) >= 3:
                    break
        finally:
            response.close()

        distinct = {bytes(frame) for frame in frames_in(bytes(buffer))}
        assert len(distinct) >= 3, (
            f"the endpoint produced {len(distinct)} distinct frames, so it is not a "
            f"live stream"
        )
        assert printer.camera_frames_sent >= 3


async def test_two_viewers_share_one_upstream_camera_connection(
    hass: HomeAssistant, config_entry: MockConfigEntry, printer, camera_app
) -> None:
    async with loaded(hass, config_entry) as runtime:
        url = runtime.describe()["camera_url"]

        first = await camera_app.get(url)
        second = await camera_app.get(url)
        try:
            await asyncio.wait_for(first.content.read(4096), timeout=5)
            await asyncio.wait_for(second.content.read(4096), timeout=5)

            assert runtime.camera.stats().viewers == 2, runtime.camera.stats().as_dict()
            # Both viewers read from the one upstream connection the hub owns.
            assert printer.camera_connections == 1, (
                f"the printer saw {printer.camera_connections} camera connections for "
                f"two viewers, so the shared hub is not doing its job"
            )
        finally:
            first.close()
            second.close()


async def test_the_snapshot_endpoint_still_serves_one_frame(
    hass: HomeAssistant, config_entry: MockConfigEntry, printer
) -> None:
    """A notification and an automation want one frame, not a stream."""
    async with loaded(hass, config_entry) as runtime:
        frame = await runtime.camera.async_refresh_frame()
        assert frame is not None
        assert frame.startswith(JPEG_SOI)
        assert frame.endswith(JPEG_EOI)


async def test_the_card_is_offered_the_live_stream_url(
    hass: HomeAssistant, config_entry: MockConfigEntry, printer
) -> None:
    """The card prefers ``camera_url``, so it has to be present and signed."""
    async with loaded(hass, config_entry) as runtime:
        description = runtime.describe()
        assert description["camera"] is True
        assert description["camera_url"], "the card has no live stream to load"
        assert "camera.mjpeg" in description["camera_url"]
        # The signed token is the last path segment, not a query parameter, because
        # a browser cannot attach an Authorization header to an <img>.
        assert len(description["camera_url"].rsplit("/", 1)[-1]) > 40
        assert "?" not in description["camera_url"]
        # A still is still offered, for the fallback and for notifications.
        assert "snapshot.jpg" in description["snapshot_url"]
