"""Reverse proxy for a printer's own web UI.

A printer that exposes no control API still has an embedded page, and that page is
worth putting on a dashboard. It cannot be framed directly when the dashboard is
served over HTTPS and the printer only speaks HTTP, so Home Assistant fetches it
and re-serves it from its own origin.

What the server rewrites, and why each pass is needed:

* **Response headers.** A stale ``Content-Length`` after a body rewrite truncates
  the page, a forwarded ``ETag`` serves the unrewritten body to a conditional
  request, and a printer's own ``Content-Security-Policy`` carrying
  ``frame-ancestors`` would block the iframe with no visible reason.
* **``<base href>``.** A printer shipping ``<base href="/">`` pins every bare
  relative URL to the Home Assistant origin root, so the page's own scripts miss.
* **Root-absolute attributes.** ``/assets/...`` references resolve on the Home
  Assistant origin rather than the printer's.
* **CSS ``url()`` and ``@import``.** Fonts and sprite backgrounds 404 without it.

What the server deliberately does **not** rewrite: JavaScript bodies. A URL literal
cannot be told from prose by a regular expression, rewriting it invalidates the
printer's strong ``ETag`` so every reload refetches the whole bundle, and in the
bundle this was developed against the load-bearing URL is a template literal built
from ``window.location.hostname`` at runtime, which no server-side rewrite can
reach. A printer whose page needs that is reported as unproxied rather than
half-proxied, and the card renders the integration's own controls instead.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
from http import HTTPStatus
from types import MappingProxyType
from typing import Final

import aiohttp
from aiohttp import hdrs, web

from .protocols import PrinterConfig

_LOGGER = logging.getLogger(__name__)

STREAM_CHUNK: Final = 65536
MAX_REWRITE_BYTES: Final = 4 * 1024 * 1024

_PASSTHROUGH_HEADERS: Final = (
    hdrs.CONTENT_TYPE,
    hdrs.CONTENT_LENGTH,
    hdrs.CONTENT_RANGE,
    hdrs.ACCEPT_RANGES,
    hdrs.ETAG,
    hdrs.LAST_MODIFIED,
    hdrs.CACHE_CONTROL,
)

#: A rewritten body has a different length and a different hash, so neither header
#: is forwarded. ``Cache-Control`` is replaced with ``no-store`` for the same reason
#: the video proxy's playlist view does it: the body is generated per request.
_PASSTHROUGH_REWRITTEN: Final = (hdrs.CONTENT_TYPE,)

_DROP_REQUEST_HEADERS: Final = frozenset(
    {
        hdrs.HOST.lower(),
        hdrs.CONNECTION.lower(),
        hdrs.UPGRADE.lower(),
        hdrs.ACCEPT_ENCODING.lower(),
        hdrs.CONTENT_LENGTH.lower(),
        "te",
        "trailer",
        "transfer-encoding",
    }
)

#: A printer's own framing and transport policy. Core re-adds ``X-Frame-Options``
#: after every handler returns so the same-origin iframe is permitted regardless,
#: but core adds no CSP, so a printer that sends ``frame-ancestors 'none'`` would
#: silently block the page. Three of these names have no ``aiohttp.hdrs`` constant
#: in the aiohttp Home Assistant pins, so every name is written as its wire form.
_SCRUB_RESPONSE_HEADERS: Final = frozenset(
    {
        "x-frame-options",
        "content-security-policy",
        "content-security-policy-report-only",
        "cross-origin-opener-policy",
        "cross-origin-embedder-policy",
        "cross-origin-resource-policy",
        "strict-transport-security",
        "permissions-policy",
    }
)

_HTML: Final = "text/html"
_CSS: Final = "text/css"

_BASE_TAG: Final = re.compile(r"<base\b[^>]*?>", re.IGNORECASE)
_ABSOLUTE_ATTR: Final = re.compile(
    r"""(\b(?:href|src|action|poster|data-src)\s*=\s*)(["'])(/(?!/)[^"']*)\2""",
    re.IGNORECASE,
)
_CSS_URL: Final = re.compile(r"""url\(\s*(["']?)(/(?!/)[^"')]*)\1\s*\)""", re.IGNORECASE)
_CSS_IMPORT: Final = re.compile(r"""(@import\s+)(["'])(/(?!/)[^"']*)\2""", re.IGNORECASE)


class WebProxyError(Exception):
    """Raised when a proxied request cannot be served."""


class RouteKind(StrEnum):
    """What a proxied port carries."""

    HTTP = "http"
    BRIDGED = "bridged"


@dataclass(frozen=True, slots=True)
class Route:
    """One entry in the proxy's port table.

    ``rewrite`` is a property of the route rather than of the response, because a
    bridged port carries no document to rewrite and a port serving an API carries
    JSON that must be forwarded byte for byte.
    """

    port: int
    kind: RouteKind
    label: str
    rewrite: bool = False

    def __post_init__(self) -> None:
        """Make an incoherent route unrepresentable."""
        if self.kind is RouteKind.BRIDGED and self.rewrite:
            raise ValueError("a bridged route carries no HTML to rewrite")


def default_routes(config: PrinterConfig) -> dict[int, Route]:
    """Return the port table for one printer.

    The printer's own page is served on the port it was configured with. A camera
    port is handled by the camera views, which hold one shared upstream connection,
    so it is deliberately absent here.
    """
    port = config.port if config.port in (80, 443) else 80
    return {
        port: Route(port=port, kind=RouteKind.HTTP, label="printer web UI", rewrite=True)
    }


def normalize_content_type(value: str | None) -> str | None:
    """Return a content type without its parameters, lower-cased."""
    if not value:
        return None
    return value.split(";", 1)[0].strip().lower()


def scrub_response_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return the upstream response headers this proxy is willing to forward."""
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in _SCRUB_RESPONSE_HEADERS
    }


def filter_request_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return the client request headers worth forwarding upstream.

    ``Accept-Encoding: identity`` keeps byte counts equal to the upstream payload,
    so a forwarded ``Content-Length`` and a byte range stay valid.
    """
    forwarded = {
        key: value for key, value in headers.items() if key.lower() not in _DROP_REQUEST_HEADERS
    }
    forwarded[hdrs.ACCEPT_ENCODING] = "identity"
    return forwarded


def rewrite_document(body: str, content_type: str, prefix: str) -> str:
    """Apply every rewrite pass that suits ``content_type``.

    ``prefix`` must end in a slash, because it becomes the document base.
    """
    if content_type == _CSS:
        body = _CSS_URL.sub(_css_url_replacer(prefix), body)
        return _CSS_IMPORT.sub(_css_import_replacer(prefix), body)

    if content_type == _HTML:
        base = f'<base href="{prefix}">'
        if _BASE_TAG.search(body):
            body = _BASE_TAG.sub(base, body, count=1)
        else:
            body = _inject_base(body, base)
        body = _ABSOLUTE_ATTR.sub(_attr_replacer(prefix), body)
        body = _CSS_URL.sub(_css_url_replacer(prefix), body)
        return _CSS_IMPORT.sub(_css_import_replacer(prefix), body)

    return body


def _inject_base(body: str, base: str) -> str:
    head = re.search(r"<head\b[^>]*>", body, re.IGNORECASE)
    if head:
        return f"{body[: head.end()]}{base}{body[head.end() :]}"
    html = re.search(r"<html\b[^>]*>", body, re.IGNORECASE)
    if html:
        return f"{body[: html.end()]}{base}{body[html.end() :]}"
    return base + body


def _attr_replacer(prefix: str):
    def replace(match: re.Match[str]) -> str:
        target = match.group(3)
        if target.startswith(prefix):
            return match.group(0)
        return f"{match.group(1)}{match.group(2)}{prefix}{target.lstrip('/')}{match.group(2)}"

    return replace


def _css_url_replacer(prefix: str):
    def replace(match: re.Match[str]) -> str:
        target = match.group(2)
        if target.startswith(prefix):
            return match.group(0)
        return f"url({match.group(1)}{prefix}{target.lstrip('/')}{match.group(1)})"

    return replace


def _css_import_replacer(prefix: str):
    def replace(match: re.Match[str]) -> str:
        target = match.group(3)
        if target.startswith(prefix):
            return match.group(0)
        return f"{match.group(1)}{match.group(2)}{prefix}{target.lstrip('/')}{match.group(2)}"

    return replace


@dataclass(frozen=True, slots=True)
class ProxyStatus:
    """A serialisable view of the web proxy."""

    requests: int
    rewrites: int
    errors: int
    last_error: str | None
    routes: tuple[int, ...]

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe mapping."""
        return {
            "requests": self.requests,
            "rewrites": self.rewrites,
            "errors": self.errors,
            "last_error": self.last_error,
            "routes": list(self.routes),
        }


class WebProxyRuntime:
    """Own the port table and the upstream HTTP conversation for one printer."""

    def __init__(
        self, config: PrinterConfig, routes: Mapping[int, Route] | None = None
    ) -> None:
        """Create the proxy for one printer."""
        self._config = config
        self._routes: Mapping[int, Route] = MappingProxyType(
            dict(routes) if routes is not None else default_routes(config)
        )
        self._requests = 0
        self._rewrites = 0
        self._errors = 0
        self._last_error: str | None = None

    @property
    def routes(self) -> Mapping[int, Route]:
        """Return the port table."""
        return self._routes

    def allow_port(self, port: int) -> bool:
        """Return ``True`` when this proxy carries the given upstream port."""
        return port in self._routes

    def status(self) -> ProxyStatus:
        """Return a serialisable view of this proxy."""
        return ProxyStatus(
            requests=self._requests,
            rewrites=self._rewrites,
            errors=self._errors,
            last_error=self._last_error,
            routes=tuple(sorted(self._routes)),
        )

    def base_prefix(self, entry_id: str, token: str, port: int) -> str:
        """Return the prefix a document served from ``port`` must resolve against."""
        from .const import API_BASE, RESOURCE_WEB

        return f"{API_BASE}/{entry_id}/{RESOURCE_WEB}/{token}/p{port}/"

    def upstream_url(self, port: int, path: str, query: str = "") -> str:
        """Return the upstream URL for one request."""
        scheme = "https" if port == 443 else self._config.scheme
        authority = self._config.host if port in (80, 443) else f"{self._config.host}:{port}"
        url = f"{scheme}://{authority}/{path.lstrip('/')}"
        return f"{url}?{query}" if query else url

    async def async_proxy(
        self,
        request: web.Request,
        session: aiohttp.ClientSession,
        *,
        port: int,
        path: str,
        entry_id: str,
        token: str,
    ) -> web.StreamResponse:
        """Proxy one request, buffering only a document or stylesheet.

        Everything else streams, so a large asset never lands in memory.
        """
        route = self._routes.get(port)
        if route is None or route.kind is not RouteKind.HTTP:
            raise WebProxyError(f"port {port} is not carried by this proxy")

        url = self.upstream_url(port, path, request.query_string)
        self._requests += 1

        try:
            async with session.request(
                request.method,
                url,
                headers=filter_request_headers(request.headers),
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=8, sock_read=30),
            ) as upstream:
                return await self._async_respond(
                    request,
                    upstream,
                    prefix=self.base_prefix(entry_id, token, port),
                    rewrite=route.rewrite,
                )
        except aiohttp.ClientError as err:
            self._errors += 1
            self._last_error = str(err)
            raise WebProxyError(f"cannot reach the printer: {err}") from err
        except TimeoutError as err:
            self._errors += 1
            self._last_error = "the printer did not answer in time"
            raise WebProxyError("the printer did not answer in time") from err

    async def _async_respond(
        self,
        request: web.Request,
        upstream: aiohttp.ClientResponse,
        *,
        prefix: str,
        rewrite: bool,
    ) -> web.StreamResponse:
        """Build the response the browser receives."""
        content_type = normalize_content_type(upstream.headers.get(hdrs.CONTENT_TYPE))
        rewritable = (
            rewrite
            and upstream.status < HTTPStatus.BAD_REQUEST
            and content_type in (_HTML, _CSS)
        )

        if rewritable:
            body = await upstream.content.read(MAX_REWRITE_BYTES + 1)
            if len(body) <= MAX_REWRITE_BYTES:
                self._rewrites += 1
                response = web.Response(
                    body=rewrite_document(
                        body.decode("utf-8", "replace"), content_type or "", prefix
                    ).encode("utf-8"),
                    status=upstream.status,
                )
                for key, value in scrub_response_headers(upstream.headers).items():
                    if key.lower() in _PASSTHROUGH_REWRITTEN:
                        response.headers[key] = value
                response.headers[hdrs.CACHE_CONTROL] = "no-store"
                return response
            _LOGGER.debug(
                "%s: not rewriting a %d byte %s document",
                self._config.name,
                len(body),
                content_type,
            )

        return await self._async_stream(request, upstream)

    async def _async_stream(
        self, request: web.Request, upstream: aiohttp.ClientResponse
    ) -> web.StreamResponse:
        """Pipe an upstream body through without buffering it."""
        response = web.StreamResponse(status=upstream.status)
        for key, value in scrub_response_headers(upstream.headers).items():
            if key.lower() in _PASSTHROUGH_HEADERS:
                response.headers[key] = value
        await response.prepare(request)
        try:
            async for chunk in upstream.content.iter_chunked(STREAM_CHUNK):
                await response.write(chunk)
        except ConnectionResetError:
            # The browser closed the connection, which is not an error.
            pass
        finally:
            with suppress(ConnectionResetError, RuntimeError):
                await response.write_eof()
        return response
