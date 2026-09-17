"""Serve and load the Lovelace card that ships with this integration.

The card is a plain ES module served from the integration directory and registered
as an extra frontend module, so installing the integration is enough to make
``custom:generic-3dprinter-card`` available. No manual Lovelace resource is needed.
"""

from __future__ import annotations

import logging

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import CARD_URL_PATH, DATA_CARD_REGISTERED, DOMAIN, WWW_PATH

_LOGGER = logging.getLogger(__name__)

STATIC_URL_PATH = f"/{DOMAIN}"


async def async_register_card(hass: HomeAssistant) -> None:
    """Expose the card over HTTP and tell the frontend to load it."""
    if hass.data.get(DATA_CARD_REGISTERED):
        return

    integration = await async_get_integration(hass, DOMAIN)
    version = integration.version or "0"
    card_path = integration.file_path / WWW_PATH

    await hass.http.async_register_static_paths(
        [
            # cache_headers=False: the version query parameter is the cache key
            # rather than a 31 day unconditional cache.
            StaticPathConfig(STATIC_URL_PATH, str(card_path), False)
        ]
    )
    hass.data[DATA_CARD_REGISTERED] = True
    _LOGGER.debug("registered the %s static path from %s", STATIC_URL_PATH, card_path)

    if "frontend" not in hass.config.components:
        _LOGGER.debug("frontend is not set up, skipping Lovelace card registration")
        return

    add_extra_js_url(hass, f"{CARD_URL_PATH}?v={version}")
    _LOGGER.debug("Lovelace card registered as %s?v=%s", CARD_URL_PATH, version)
