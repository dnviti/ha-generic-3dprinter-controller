"""Signed URL payloads.

A browser cannot attach an ``Authorization`` header to an ``<img>``, ``<script>``
or ``<link>`` sub-resource, so every URL this integration hands to the browser
carries a self-contained token instead. The token is an HMAC-signed JSON payload
that binds the config entry, the resource scope, the upstream target, and an
expiry.

Two properties matter:

* **No open proxy.** The upstream target lives inside the signed payload, so a
  client cannot point the proxy at an arbitrary address.
* **Robust in a URL.** The token is URL-safe base64 with the padding stripped, so
  it contains no ``/``, no ``=``, and no ``<``, and it survives being placed in a
  path segment.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from typing import Any
from urllib.parse import urlsplit

_PADDING = "="


class InvalidSignature(Exception):
    """Raised when a signed payload was tampered with or is malformed."""


def new_secret() -> str:
    """Return a fresh signing secret."""
    return secrets.token_urlsafe(48)


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip(_PADDING)


def _b64decode(value: str) -> bytes:
    padding = _PADDING * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


class PayloadSigner:
    """Sign and verify compact JSON payloads."""

    def __init__(self, secret: str) -> None:
        """Create a signer around a shared secret."""
        self._key = secret.encode("utf-8")

    def sign(self, payload: str) -> str:
        """Return ``payload.signature`` as one URL-safe string."""
        body = payload.encode("utf-8")
        signature = hmac.new(self._key, body, hashlib.sha256).digest()
        return f"{_b64encode(body)}.{_b64encode(signature)}"

    def unsign(self, token: str) -> str:
        """Return the verified payload, or raise :class:`InvalidSignature`."""
        body_part, separator, signature_part = token.partition(".")
        if not separator:
            raise InvalidSignature("token has no signature")
        try:
            body = _b64decode(body_part)
            signature = _b64decode(signature_part)
        except (ValueError, TypeError) as err:
            raise InvalidSignature("token is not valid base64url") from err
        expected = hmac.new(self._key, body, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise InvalidSignature("signature does not match")
        try:
            return body.decode("utf-8")
        except UnicodeDecodeError as err:
            raise InvalidSignature("payload is not valid UTF-8") from err


#: Schemes a proxied upstream target may use. Anything else is refused before it
#: can be signed, so a token can never carry a ``file:`` or ``javascript:`` target.
ALLOWED_UPSTREAM_SCHEMES = frozenset({"http", "https", "ws", "wss"})


def is_safe_upstream_url(url: str) -> bool:
    """Return ``True`` when ``url`` is an absolute URL this proxy may fetch."""
    if not isinstance(url, str) or not url:
        return False
    parts = urlsplit(url)
    if parts.scheme.lower() not in ALLOWED_UPSTREAM_SCHEMES:
        return False
    if not parts.hostname:
        return False
    return parts.username is None and parts.password is None


def is_safe_relative_path(path: str) -> bool:
    """Return ``True`` when ``path`` is a relative path with no traversal.

    The target of a web-proxy request is composed from the entry's own origin plus
    this path, so the path is the only attacker-controlled part of the address.
    """
    if not isinstance(path, str):
        return False
    if path.startswith("//") or "://" in path:
        return False
    cleaned = path.split("?", 1)[0].split("#", 1)[0]
    if "\\" in cleaned:
        return False
    segments = [segment for segment in cleaned.split("/") if segment]
    return ".." not in segments


def payload_of(token: str) -> dict[str, Any]:
    """Decode a token's payload without verifying it. For diagnostics only."""
    body_part = token.partition(".")[0]
    try:
        return json.loads(_b64decode(body_part))
    except (ValueError, TypeError, json.JSONDecodeError):
        return {}
