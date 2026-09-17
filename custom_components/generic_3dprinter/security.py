"""URL token manager.

The signing secret is generated once per Home Assistant instance and stored with
``helpers.storage.Store``, so tokens survive a restart without ever being derived
from user input.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    API_BASE,
    DATA_SIGNER,
    DATA_SIGNER_LOCK,
    DOMAIN,
    RESOURCE_CAMERA,
    RESOURCE_SNAPSHOT,
    RESOURCE_STATUS,
    RESOURCE_WEB,
    SIGNED_URL_TTL,
)
from .signer import (
    InvalidSignature,
    PayloadSigner,
    is_safe_relative_path,
    new_secret,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.tokens"
_SECRET_FIELD = "secret"

_SCOPE_TO_RESOURCE = {
    "camera": RESOURCE_CAMERA,
    "snapshot": RESOURCE_SNAPSHOT,
    "status": RESOURCE_STATUS,
    "web": RESOURCE_WEB,
}

_RESOURCE_TO_SCOPE = {value: key for key, value in _SCOPE_TO_RESOURCE.items()}


class InvalidToken(Exception):
    """Raised when a token is missing, malformed, expired or tampered with."""


@dataclass(frozen=True, slots=True)
class MediaToken:
    """A verified token."""

    entry_id: str
    resource: str
    path: str | None = None
    port: int | None = None
    expires: int = 0


class TokenManager:
    """Issue and verify the URLs handed to the browser."""

    def __init__(self, signer: PayloadSigner, *, ttl: int = SIGNED_URL_TTL) -> None:
        """Create a manager around an existing signer."""
        self._signer = signer
        self._ttl = ttl

    @classmethod
    async def async_create(
        cls, hass: HomeAssistant, *, ttl: int = SIGNED_URL_TTL
    ) -> TokenManager:
        """Return the instance-wide manager, creating the secret if needed."""
        if (manager := hass.data.get(DATA_SIGNER)) is not None:
            return manager
        lock: asyncio.Lock = hass.data.setdefault(DATA_SIGNER_LOCK, asyncio.Lock())
        async with lock:
            if (manager := hass.data.get(DATA_SIGNER)) is not None:
                return manager
            store: Store[dict[str, str]] = Store(hass, STORAGE_VERSION, STORAGE_KEY)
            stored = await store.async_load() or {}
            secret = stored.get(_SECRET_FIELD)
            if not secret:
                secret = new_secret()
                await store.async_save({_SECRET_FIELD: secret})
                _LOGGER.debug("generated a new URL signing secret")
            manager = cls(PayloadSigner(secret), ttl=ttl)
            hass.data[DATA_SIGNER] = manager
            return manager

    # ------------------------------------------------------------------ issuing

    def async_issue(
        self,
        *,
        entry_id: str,
        scope: str,
        path: str | None = None,
        port: int | None = None,
        ttl: int | None = None,
    ) -> str:
        """Return a signed token for one resource of one entry."""
        resource = _SCOPE_TO_RESOURCE.get(scope)
        if resource is None:
            raise InvalidToken(f"unknown resource scope: {scope}")
        if path is not None and not is_safe_relative_path(path):
            raise InvalidToken(f"refusing to sign an unsafe path: {path!r}")

        payload: dict[str, object] = {
            "e": entry_id,
            "r": resource,
            "x": int(time.time()) + (ttl if ttl is not None else self._ttl),
        }
        if path is not None:
            payload["p"] = path
        if port is not None:
            payload["o"] = int(port)
        return self._signer.sign(json.dumps(payload, separators=(",", ":"), sort_keys=True))

    def async_url(
        self,
        *,
        entry_id: str,
        scope: str,
        path: str | None = None,
        port: int | None = None,
        ttl: int | None = None,
    ) -> str:
        """Return a ready-to-use, relative URL for one resource."""
        token = self.async_issue(
            entry_id=entry_id, scope=scope, path=path, port=port, ttl=ttl
        )
        resource = _SCOPE_TO_RESOURCE[scope]
        if scope == "web":
            return f"{API_BASE}/{entry_id}/{resource}/{token}/p{port}/"
        return f"{API_BASE}/{entry_id}/{resource}/{token}"

    # --------------------------------------------------------------- verifying

    def async_verify(self, token: str | None, *, entry_id: str) -> MediaToken:
        """Verify ``token`` and return its payload, or raise :class:`InvalidToken`."""
        if not token:
            raise InvalidToken("missing token")
        try:
            payload = json.loads(self._signer.unsign(token))
        except (InvalidSignature, ValueError) as err:
            raise InvalidToken("invalid token") from err
        if not isinstance(payload, dict):
            raise InvalidToken("malformed token payload")

        try:
            token_entry = str(payload["e"])
            resource = str(payload["r"])
            expires = int(payload["x"])
        except (KeyError, TypeError, ValueError) as err:
            raise InvalidToken("incomplete token payload") from err

        if token_entry != entry_id:
            raise InvalidToken("token does not belong to this config entry")
        if expires and expires < time.time():
            raise InvalidToken("token expired")
        if resource not in _RESOURCE_TO_SCOPE:
            raise InvalidToken(f"unsupported resource: {resource}")

        path = payload.get("p")
        if path is not None and not is_safe_relative_path(str(path)):
            raise InvalidToken("token carries an unsafe path")

        port = payload.get("o")
        return MediaToken(
            entry_id=token_entry,
            resource=resource,
            path=str(path) if path is not None else None,
            port=int(port) if port is not None else None,
            expires=expires,
        )
