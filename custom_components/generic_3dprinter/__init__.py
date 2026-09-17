"""Generic 3D Printer Controller.

One Home Assistant integration for a mixed fleet of 3D printers. Each config entry
is one printer, and each printer is driven through a protocol adapter that
translates it into one shared model, so this integration can talk to an Elegoo
SDCP printer, a Klipper machine over Moonraker, an OctoPrint host and a bare web
page without the entities or the dashboard card learning which is which.

Everything the browser touches - camera frames and a printer's own web UI - is
re-served from Home Assistant's own origin with a signed token in the URL, so a
dashboard behind HTTPS can show a printer that only speaks plain HTTP on the LAN.
"""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .bridge import async_register_websocket_bridge
from .const import (
    DATA_CARD_REGISTERED,
    DATA_COORDINATORS,
    DATA_RUNTIMES,
    DATA_VIEWS_REGISTERED,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import PrinterCoordinator
from .frontend import async_register_card
from .proxy import WebProxyRuntime
from .protocols import AuthError, ConfigError, UnreachableError, parse_config
from .registry import build_adapter, get_registration
from .runtime import PrinterRuntime, create_session, remove_runtime, set_runtime
from .security import TokenManager
from .views import async_register_views

_LOGGER = logging.getLogger(__name__)

type Generic3DPrinterConfigEntry = ConfigEntry[PrinterRuntime]


def _coordinator_map(hass: HomeAssistant) -> dict[str, PrinterCoordinator]:
    return hass.data.setdefault(DATA_COORDINATORS, {})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one printer."""
    if hass.http is None:
        raise ConfigEntryNotReady("the http integration is not set up")

    try:
        config = parse_config({**entry.data, **entry.options})
        get_registration(config.protocol)
    except ConfigError as err:
        _LOGGER.error("invalid configuration for %s: %s", entry.title, err)
        return False
    except KeyError as err:
        _LOGGER.error("protocol for %s is not available: %s", entry.title, err)
        return False

    session = create_session()
    adapter = build_adapter(config, session)
    tokens = await TokenManager.async_create(hass)
    web_proxy = WebProxyRuntime(config)

    runtime = PrinterRuntime(
        hass=hass,
        entry=entry,
        config=config,
        adapter=adapter,
        session=session,
        tokens=tokens,
        web_proxy=web_proxy,
    )

    try:
        await adapter.async_setup()
    except AuthError as err:
        await session.close()
        raise ConfigEntryNotReady(f"the printer refused the credential: {err}") from err
    except UnreachableError as err:
        await session.close()
        raise ConfigEntryNotReady(str(err)) from err

    set_runtime(hass, runtime)
    entry.runtime_data = runtime

    coordinator = PrinterCoordinator(hass, runtime, entry)
    _coordinator_map(hass)[entry.entry_id] = coordinator
    await coordinator.async_config_entry_first_refresh()

    await _async_register_once(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    _LOGGER.debug(
        "%s: %s ready on %s with %d capabilities",
        config.name,
        config.protocol.value,
        config.redacted_url,
        len(adapter.capabilities),
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload one printer."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    runtime: PrinterRuntime = entry.runtime_data
    await runtime.async_stop()
    remove_runtime(hass, entry.entry_id)
    _coordinator_map(hass).pop(entry.entry_id, None)
    return True


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Initialise the integration's shared state without any printer configured."""
    hass.data.setdefault(DATA_RUNTIMES, {})
    hass.data.setdefault(DATA_COORDINATORS, {})
    return True


async def _async_register_once(hass: HomeAssistant) -> None:
    """Register the HTTP views, the WebSocket API, the bridge and the card once.

    Several entries can load at the same time, so the work is guarded by a flag and
    re-checked under a lock rather than performed per entry.
    """
    if hass.data.get(DATA_VIEWS_REGISTERED):
        return
    lock: asyncio.Lock = hass.data.setdefault(f"{DOMAIN}_setup_lock", asyncio.Lock())
    async with lock:
        if hass.data.get(DATA_VIEWS_REGISTERED):
            return
        async_register_views(hass)
        from .websocket import async_register_websocket_api

        async_register_websocket_api(hass)
        async_register_websocket_bridge(hass)
        hass.data[DATA_VIEWS_REGISTERED] = True
        _LOGGER.debug("registered the %s views, WebSocket API and bridge", DOMAIN)

    if not hass.data.get(DATA_CARD_REGISTERED):
        await async_register_card(hass)


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change."""
    await hass.config_entries.async_reload(entry.entry_id)
