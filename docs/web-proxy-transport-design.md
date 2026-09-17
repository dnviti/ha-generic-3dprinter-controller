# Web proxy transport design

A decision document for the web proxy transport of the `generic_3dprinter` Home Assistant integration. No implementation.

## Evidence base

I read the reference implementation in `ha-generic-video-proxy`: `views.py`, `security.py`, `signer.py`, `upstream.py`, `runtime.py`, `hub.py`, `websocket.py`, `frontend.py`, `models.py`, `const.py`, `tests/test_views.py`, and `tests/upstream_server.py`. I read this repository's `docs/architecture.md`, `docs/protocol-adapter-layer-design.md`, `docs/protocol-elegoo-sdcp-verified.md`, and the `VideoUrl` discussion in `docs/research/elegoo-centauri-carbon-sdcp.md`.

I read Home Assistant 2025.1.4 from the installed tree, because three of the six decisions turn on what core actually does rather than what it documents. The load-bearing files are `homeassistant/helpers/http.py`, `homeassistant/components/http/auth.py`, `homeassistant/components/http/security_filter.py`, `homeassistant/components/http/headers.py`, and `homeassistant/components/http/__init__.py`.

I probed the live Centauri Carbon at `192.168.128.143` and downloaded its own bundles to disk. Every quoted bundle substring below was extracted from those files with a regex context scan. I also wrote four throwaway probes against aiohttp 3.11.11 to settle the questions that could not be answered by reading. Those probes are the reason several claims in this document are stated as proven rather than argued, and section `TEST STRATEGY` turns each one into a permanent test.

Two findings change what the parent repository should do next, and they are flagged where they land. `docs/architecture.md` lines 117 to 119 state that the WebSocket bridge must be a raw aiohttp route because `HomeAssistantView` cannot accept an upgrade. That is wrong, and section 3 gives the proof. `const.py` line 75 defines `QUERY_PATH: Final = "p"` for carrying the upstream path in a query parameter. Section 2 explains why that parameter must never exist.

## DECISION

| # | Point | Decision |
| --- | --- | --- |
| 1 | How the UI reaches the browser | Option (b), a shim-first proxy. The iframe points at an integration-served transparent proxy path, the printer's HTML passes through with four server-side edits and a scrubbed header set, and a JS shim installed as the first inline script patches `WebSocket`, `fetch`, and `XMLHttpRequest`, plus the WebSocket message payload that carries the camera URL. Option (a) cannot reach two runtime-derived URLs and option (c) means rewriting an Angular app. |
| 2 | URL rewriting | One URL grammar `/api/generic_3dprinter/{entry_id}/{token}/p{port}/{path:.*}` with the upstream port as a path segment. An ordered seven-pass table where only `<base>`, absolute attributes, CSS `url()`, and the header scrub run on the server, and only on `text/html` and `text/css`. JavaScript bodies are never rewritten except through an opt-in literal table that requires a verified bundle hash. |
| 3 | WebSocket bridging | A `HomeAssistantView` subclass with `async def get(...)` returning `web.WebSocketResponse`. This works; `HomeAssistantView` has no `websocket` handler name but `request_handler_factory` returns any `StreamResponse` and `WebSocketResponse` is one. One upstream printer socket per entry, fanned out to N viewers through a hub, with `max_msg_size` of 1 MiB, ping/pong terminated on each leg, and cleanup from HA's `handler_cancellation=True`. |
| 4 | Authentication | Path-segment HMAC token, extended from `{"e","r","x"}` to `{"e","s","x"}` with a new `web` scope, guarded on the view by a `resources` frozenset exactly as `ProxyView._token()` does today. No cookie for our auth, ever. The web scope never accepts a client-supplied URL; the target is always composed from the entry's fixed origin plus a validated relative path. The printer's own session cookie lives in a server-side per-entry jar and is never forwarded to the browser. |
| 5 | Failure isolation | A dedicated bounded `ClientSession` per entry, distinct timeouts per route kind, `iter_chunked` streaming that never buffers a whole asset, a 4 MiB cap that applies only to the two rewritten content types, a per-port circuit breaker copied from `StreamHub._async_handle_failure`, and a card that gates the iframe on a status endpoint instead of rendering it into a dead proxy. |
| 6 | Data shape | A `RouteTable` mapping port to a discriminated union of `HttpRoute`, `StreamRoute`, and `SocketRoute`, a `RewriteRule` table carrying its own justification and an optional bundle hash, a mutable `BridgedSocket` record with a frozen `SocketStats` projection, and a `WebProxyRuntime` that mirrors `ProxyRuntime` field for field so the two read the same. |

---

## 1. How the printer UI reaches the browser

### The three options against the actual printer

I downloaded `main.f1485af82d8237b5b3c1.js` (805227 bytes), `runtime.d8e385257819bb9e3f4a.js`, `25.a66e4a8918f0e5d04852.js`, `624.daeeadece3842f2bd4f0.js`, and `styles.948ae391de6d85346226.css` from the printer and searched them by regex. Two results decide this question.

The first is the socket URL. There is exactly one `new WebSocket` call in the main bundle, and the URL is not a constant:

```javascript
this.hostName=window.location.hostname
...
connect(){this.url=`ws://${this.hostName}:3030/websocket`,this.createWebSocket()}
```

The hostname comes from `window.location.hostname` at construction time. Once the page is served from the Home Assistant origin, that value is the Home Assistant hostname, so the page computes `ws://homeassistant.local:3030/websocket`. A server-side rewriter can substitute the literal `3030` and the literal `ws://`, but it cannot turn a second origin's port into a path under a prefix, because the template has no slot where a path could be inserted. To fix it by rewriting you would have to replace the template expression itself with a new expression, which means pattern-matching the source of a hash-versioned bundle whose hash changes on every firmware revision.

The second is the camera URL, in chunk 25:

```javascript
u.Q6J("src","http://"+(null==t.printerDetail||null==t.printerDetail.Data?null:t.printerDetail.Data.VideoUrl),u.LSH)
```

The `src` is assembled at runtime from a data field named `VideoUrl`. This repository's own research already establishes where that field comes from. `docs/research/elegoo-centauri-carbon-sdcp.md` lines 1195 to 1197 say that if port 3031 returns no bytes you send SDCP command 386 with `{"Enable": 1}`, and the response carries `Data.Data.VideoUrl`. I confirmed the 3031 stream works without 386, so 386 is a no-op on this printer and the field was absent from my own Cmd 386 capture, which returned `{"Cmd":386,"Data":{"Ack":3}}`. Either way the value arrives as protocol data, so it is unreachable by any static rewrite of any asset.

Option (a) fails on both. Option (a) with a server-side rewriter is the design that looks correct on paper and breaks on first load of the real bundle. Option (c) fails on cost, not on principle. Reproducing this UI means reproducing an Angular application plus SDCP print control plus a chunked upload that assembles `Offset` and `S-File-MD5` multipart fields, and it would need re-deriving per firmware and per vendor.

Option (b) survives both, because a shim patches the constructor rather than the string, and because the same shim can wrap the socket's message handler and rewrite the `VideoUrl` field before the page's own handler parses it. That second move is what makes (b) strictly better than a DOM observer over `img[src]`, and it is only available because we already own the socket.

### What (b) means precisely

The iframe points at a proxy path on the Home Assistant origin. The response is the printer's own document, with four server-side edits and a header scrub. The first edit installs a shim as the first inline `<script>` in `<head>`, before the printer's own `<script src="runtime...">` tags. The remaining edits are passes 1 to 3 of section 2. Everything dynamic is the shim's job.

The shim patches four things:

| Target | Why a patch and not a rewrite |
| --- | --- |
| `WebSocket` | the URL is built from `window.location.hostname` plus a hardcoded port, so it is runtime-derived |
| `WebSocket` message payloads | `VideoUrl` arrives as protocol data and becomes an `img.src` |
| `fetch` | absent from this bundle, present in Moonraker and OctoPrint frontends |
| `XMLHttpRequest.prototype.open` | Angular's `HttpXhrBackend` routes through XHR; this bundle has no `fetch` at all |

### Framing headers

The printer sends neither `X-Frame-Options` nor `Content-Security-Policy` today. I confirmed that directly: `GET http://192.168.128.143/` returns exactly three headers, `Content-Length`, `Content-Type`, and `ETag`. So for this printer the neutralisation is defensive. It still belongs in the design, for three reasons.

Home Assistant's own `headers_middleware` in `components/http/headers.py` runs `response.headers.update(added_headers)` on every response with `{"Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff", "Server": "", "X-Frame-Options": "SAMEORIGIN"}`. Because the iframe is same-origin, `SAMEORIGIN` is satisfied, and core's own header helps rather than hurts. Core does not touch `Content-Security-Policy`, so a printer that sends `frame-ancestors 'none'` would be blocked and the proxy must strip it. `Referrer-Policy: no-referrer` from core is also what keeps the path-segment token out of the `Referer` header of every outbound sub-resource request, so that one line of core is doing real work for section 4.

The proxy must also strip `Strict-Transport-Security` from any proxied response. Forwarding a printer's HSTS header on the Home Assistant origin would force the browser to upgrade every future request to the Home Assistant hostname to HTTPS, which breaks an HTTP-only Home Assistant install. That is a self-inflicted outage caused by copying a header, and it is worth calling out because it is invisible until the user reloads.

### Why not the alternatives in detail

| Option | Survives the runtime socket URL | Survives the runtime camera URL | Cost |
| --- | --- | --- | --- |
| (a) server rewrite | No, the port would have to become a path inside a template expression | No, the value never appears in an asset | Low per printer, fails on the real bundle |
| (b) with shim and pre-pass | Yes, the constructor is patched | Yes, the message payload is rewritten | Medium once, per dialect profile afterwards |
| (c) reimplement in the card | Not applicable | Not applicable | Very high, and per firmware |

I would ship (b). The evidence that would change my mind is a printer whose UI ships as a native ES module graph with static, root-absolute specifiers only, no runtime-derived socket URL, and no data-driven asset URLs. For that printer (a) is simpler and has no shim to break, so (a) should be expressible as a dialect profile with the shim disabled rather than being deleted. Model it as data, not as two code paths.

One honest limitation of (b). The shim cannot patch anything inside a closed shadow root, cannot patch a `<img>` whose `src` was assigned before the shim ran, and cannot intercept `location.href` assignment. The shim's correctness is therefore not self-evident, and section `TEST STRATEGY` requires a live browser check against the real printer rather than a unit test over the shim source.

---

## 2. URL rewriting rules

### The URL grammar comes first

One proxy prefix cannot express two upstream ports. The port therefore becomes a path segment, and the port is the only part of the authority that varies. The host does not vary and does not appear in the URL at all.

```
/api/generic_3dprinter/{entry_id}/{token}/p{port}/{path:.*}
```

I proved this grammar routes by standing up an aiohttp app on a real socket and requesting it. `{path:.*}` crosses slashes, `p{port}` works as a prefix placeholder inside one literal segment, and both `p80` and `p3030` resolve:

```
200  /api/generic_3dprinter/e1/TOK/p80/
        {"port": "80", "path": ""}
200  /api/generic_3dprinter/e1/TOK/p80/deep/nested/a/b/c.js
        {"port": "80", "path": "deep/nested/a/b/c.js"}
200  /api/generic_3dprinter/e1/TOK/p3030/websocket
        {"port": "3030", "path": "websocket"}
```

A realistic web token is 110 characters for the payload shape in section 4, so a deep asset URL stays near 198 characters. That is short enough for a `<base>` element.

The grammar has one hazard worth recording. A catch-all registered under the same base will shadow a sibling route whose shape it also matches. In my probe it did not, because the sibling `/{entry_id}/list/{token}` has three trailing segments and the catch-all needs four, so the shapes do not overlap. Any future sibling route added under the same base must be checked for that overlap, and the check belongs in a routing test rather than in a comment.

### Why the upstream path must not be a query parameter

`const.py` line 75 defines `QUERY_PATH: Final = "p"` for a query parameter carrying the upstream path. That design does not work here, for two reasons.

A query parameter cannot be expressed in `<base>`. The relative chunk URLs in this bundle are bare, and `<base>` is the only mechanism that turns them into prefixed URLs without touching every one of them. If the prefix portion that varies lives in a query string, `<base>` cannot carry it.

Home Assistant's `security_filter_middleware` inspects `request.path + "?" + request.query_string` against a regex that includes `[a-zA-Z0-9_]=/([a-z0-9_.]//?)+`, `(\.\.//?)+`, and a `<script` pattern, after a recursive unquote. A query parameter holding a real upstream path is exactly the input that pattern is looking for. The path segment form has no `=` and no `/` inside the variable part, which is why it is safe. I verified the token alphabet is exactly `[A-Za-z0-9_-]` plus one `.`, because `signer.py` line 41 strips base64 padding with `rstrip(b"=")`. So the token contains no `/`, no `=`, no `<`, no tab, and at most one `.`, and it cannot contain `..`. The same probe confirmed a realistic proxy path does not match the traversal pattern:

```
  False  /api/generic_3dprinter/e1/TOK/p80/assets/iconfont/iconfont.css
  True   /api/generic_3dprinter/e1/TOK/p80/../secret
```

Home Assistant 400s the second one before the view runs, which is the correct outcome, and the proxy never generates it.

### The ordered pass

Pass 0 runs on every proxied response. Passes 1 to 3 run on the server and only when the body is rewritable. Passes 4 to 7 are the shim's or the server's depending on the target.

| Order | Target | Where | Required | What breaks if skipped |
| --- | --- | --- | --- | --- |
| 0 | Response headers | server | yes for CSP, no for XFO | Forwarding a stale `Content-Length` after a body rewrite truncates or hangs the response. Forwarding `ETag` or `Last-Modified` serves a stale rewritten body to a conditional request. Forwarding `Content-Encoding` after decoding and rewriting double-decodes. Forwarding `Strict-Transport-Security` can force HTTPS on the Home Assistant host. A printer's `Content-Security-Policy` with `frame-ancestors` blocks the iframe and core never touches CSP. |
| 1 | `<base href>` | server | yes | The printer ships `<base href="/">`. That element overrides `document.baseURI`, so every bare relative URL in this bundle resolves against the Home Assistant origin root regardless of where the document was served. `runtime.<hash>.js`, both lazy chunks, the mini CSS, and the i18n JSON all miss. The replacement must end in a trailing slash. |
| 2 | Absolute `href`/`src`/`srcset`/`poster`/`action`/`data-src` | server | yes | 54 origin-absolute `/assets/images/network/...` bindings in chunk 25, plus `/assets/iconfont/iconfont.css`, resolve on the Home Assistant origin. Fatal on OctoPrint, whose entire app is root-absolute. |
| 3 | CSS `url()` and `@import` in `text/css` and inline `<style>` | server | yes | Fonts and sprite backgrounds 404. `styles.948ae391de6d85346226.css` is 510 KB of real styling, and `iconfont.css` references its font files relative to itself once pass 2 puts it under the prefix. |
| 4 | `<script>` string literals containing paths | shim, not server | yes, at runtime | The socket URL on 3030, and the second WebSocket on 8883 that WebRTC uses on printers advertising `VIDEO_WEBRTC`. |
| 5 | `fetch()` and `XMLHttpRequest` targets | shim | yes | The `/cc/...do` API calls go through Angular `HttpClient`, whose backend is `xhrFactory.build()` returning `new XMLHttpRequest`. There are zero `fetch(` calls in any of the five bundles, so `XMLHttpRequest.prototype.open` is the load-bearing patch and the `fetch` patch is dead code kept for other dialects. |
| 6 | Absolute URLs carrying the page's own hostname plus a printer port | shim | yes, and this is the print path | Print upload is `` `http://${this.webSocketService.hostName}:80/uploadFile/upload` `` in three places in chunk 25, and three download paths synthesize `o.href = "http://${hostName}:80" + path` then `o.click()`. Inside the iframe `hostName` is the Home Assistant hostname, so these become `http://homeassistant.local:80/...`. The result is a UI that can view but cannot upload or download. |
| 7 | SPA deep-link fallback | server, gated | yes, gated | Ungated, the fallback answers a missing `624.<hash>.js` with `index.html` at 200, which webpack's script-element loader reports as a parse failure rather than a clean 404, and answers a missing `i18n/network-<lang>.json` with HTML that `HttpClient` fails to parse as JSON. Gate on `Sec-Fetch-Mode: navigate` or an `Accept` containing `text/html`. |
| 8 | `location` and `location.href` assignment | neither, by design | no | Cannot be done, because `[LegacyUnforgeable]` on `Location` members makes assignment unstoppable in Chrome and Safari. It also does not need doing here. None of the five bundles contains a `window.location` or `location.href` write, the router is path-based, and Angular's `Location.go` runs through `prepareExternalUrl` which prepends the base. |
| 9 | ES module `import()` specifiers | not applicable here | no | There is no dynamic `import(` in any bundle and no native ESM. `runtime.js` uses the webpack `i.l` script-element loader. |

### Pass 0 detail

Scrub list on every response:

```
X-Frame-Options
Content-Security-Policy
Content-Security-Policy-Report-Only
Cross-Origin-Opener-Policy
Cross-Origin-Embedder-Policy
Cross-Origin-Resource-Policy
Strict-Transport-Security
Permissions-Policy
```

Drop list, additionally, whenever the body was rewritten:

```
Content-Encoding
Content-Length
ETag
Last-Modified
Content-Range
Accept-Ranges
```

Then set `Cache-Control: no-store` on rewritten documents, matching what `PlaylistView.handle` already does in the reference for the same reason.

The `X-Frame-Options` entry in the scrub list is inert and should be kept only for documentation value. Core's `headers_middleware` runs `response.headers.update(added_headers)` on every response after the handler returns, and `added_headers["X-Frame-Options"] = "SAMEORIGIN"` is applied whenever `use_x_frame_options` is set, which the config schema defaults to `True`. So a proxy cannot remove that header even if it tries, and the same-origin iframe is permitted anyway. It is helpful rather than harmful. The `Content-Security-Policy` entry is the one that does work, because `added_headers` contains no CSP and a printer that sends `frame-ancestors 'none'` would otherwise block the iframe with no way to see why.

Do not use a deny list for response headers. An allow list is what `views.py` already does with `_PASSTHROUGH_HEADERS`, and it is the right shape:

```python
_PASSTHROUGH_HEADERS: Final = (
    hdrs.CONTENT_TYPE, hdrs.CONTENT_LENGTH, hdrs.CONTENT_RANGE,
    hdrs.ACCEPT_RANGES, hdrs.ETAG, hdrs.LAST_MODIFIED, hdrs.CACHE_CONTROL,
)
```

The web proxy's copy of that tuple must be shorter than the media one, because `Content-Length` and `ETag` are only safe on the unrewritten path. That means the tuple is chosen per route kind, which is the first place the data shape in section 6 earns its keep.

### Pass 1 detail

The printer ships `<base href="/">`, and that element is what breaks bare relative resolution. With no `<base>` element at all, `document.baseURI` would be the document's own URL, and a bare `624.<hash>.js` served from `.../p80/` would resolve correctly on its own. The printer's explicit `<base href="/">` overrides that and pins resolution to the origin root. So pass 1 has two legal forms, and rewriting is much better than deleting. Deleting the element fixes webpack's bare chunk names, and rewriting it also fixes the 54 origin-absolute `/assets/...` bindings that no amount of base can help, because those are already absolute.

The replacement must end in a trailing slash, and the proxy must canonicalize its own document URL to end in one too. Angular's `getBaseHrefFromDOM()` takes the pathname verbatim and only prefixes a slash when one is missing, so `.../p80` with no trailing slash yields a base of `.../{entry_id}/` and every bare chunk resolves one directory too high. Request the document as `.../p{port}/` and emit `<base href="/api/generic_3dprinter/{entry_id}/{token}/p{port}/">`.

### Pass 2 detail

Rewrite a value when it starts with exactly one `/`. Three cases must not be rewritten. A `//host/path` value is protocol-relative and belongs here only when the host matches the printer's, which for the server pass means the configured host. A `data:`, `blob:`, or `mailto:` value is left alone. A fragment-only `#x` is left alone.

Relative values with no leading slash must be left alone even though they look like the easy case. `<base>` resolves them correctly, and rewriting them server-side would put the resolver in two places. That is a correctness argument as much as a simplicity one, and it is the same idempotence rule the shim follows in pass 5.

### Pass 5 detail

The shim normalizes through one function, `resolveUpstreamUrl(url)`, and leaves anything it does not recognize untouched:

| Input | Action |
| --- | --- |
| already starts with the proxy prefix | unchanged, so the patch is idempotent |
| `data:`, `blob:`, `about:`, `mailto:` | unchanged |
| `ws:` or `wss:` | handed to the WebSocket patch, not to `fetch` |
| absolute, host equals `window.location.hostname`, port in the route table | remap to prefix plus that port plus path |
| absolute, host equals the configured printer host, port in the route table | same |
| `//host/...` | same, under either host match |
| `/...` | prefix plus the default port plus path |
| bare relative | unchanged, `<base>` handles it |

The third row from the bottom is the one that is easy to get wrong and expensive to miss. The obvious rule is to match the printer's own host, and that rule fails for the print path. In chunk 25 the upload target is `` `http://${this.webSocketService.hostName}:80/uploadFile/upload` ``, and `hostName` is `window.location.hostname`, which inside the iframe is the Home Assistant hostname. Three download paths do the same thing with `o.href = "http://${hostName}:80" + path` followed by `o.click()`. All four produce `http://homeassistant.local:80/...`, which is the Home Assistant origin on port 80. Nothing on the server can rewrite those, because they are built from a hostname the browser supplies at runtime, and nothing in the route table can make them work, because port 80 of the Home Assistant host is the Home Assistant frontend.

So the shim keys on the port table plus the current hostname, not on the printer's hostname. That single change is the difference between a printer UI that can view and one that can also upload and download. It also generalizes to the WebRTC socket in chunk 25, `` new WebSocket(`ws://${this.webSocketService.hostName}:8883`) ``, which is gated on the printer advertising `VIDEO_WEBRTC`. The Centauri Carbon's capabilities are `["FILE_TRANSFER","PRINT_CONTROL","VIDEO_STREAM"]`, so that path is inactive here, and the rule covers it without a second code path. Port 8883 belongs in the route table for printers that advertise it.

An unknown port is left untouched and reported. Failing open keeps one unrecognized port from breaking the whole UI, and it is safe because the host is fixed by the entry and the port is not a security boundary. The port table is the guard, and it lives on the server.

The WebSocket patch must wrap `onmessage` on `WebSocket.prototype` with an accessor pair, not on the instance. The page assigns `this.printerService.webSocket.onmessage = ...` only after `new WebSocket(...)` has returned, so an instance-level override at construction time is replaced before it ever runs. A prototype getter and setter that stores and wraps the assigned callback is what actually intercepts. That matters because the `VideoUrl` rewrite depends on it, and the evidence says the wrap is sufficient here. Every handler in all five bundles is an `onmessage` property assignment, there is no `addEventListener("message", ...)` anywhere, and there is no RxJS `WebSocketSubject`. The `VideoUrl` field is set in exactly one place, from `EDIT_PRINTER_VIDEO_STREAMING` (command 386), and command 386 arrives on the 3030 socket and is routed by `e.Topic.indexOf("sdcp/response")` through `socketResponseSubject`. No HTTP response carries it.

### Pass 6 and pass 7 detail

Pass 6 is where the view-only failure lives and pass 7 is the mitigation for everything else, so they read together.

JavaScript cannot intercept `window.location` assignment, and the reason is stronger than "the property is not configurable". The HTML specification marks `Location`'s members `[LegacyUnforgeable]`, so neither Chrome nor Safari lets a page redefine or shadow them. There is nothing to patch.

That turns out not to matter for this printer, and the evidence is specific rather than hopeful. None of the five bundles contains a `window.location`, `location.href`, `top.location`, `parent.location`, or `location.reload()` write. The router is path-based rather than hash-based, and its route table uses `loadChildren` for the lazy chunks. Angular's `Location.go` runs through `prepareExternalUrl`, which prepends the base href, and the base href comes from the DOM:

```javascript
getBaseHrefFromDOM(){return s().getBaseHref(this._doc)}
```

So in-app navigation produces prefixed URLs on its own, and a reloaded deep link lands on a path the printer does not serve. Pass 7 answers that with the printer's `index.html`, exactly as an SPA host does with `try_files ... /index.html`.

Pass 7 must be gated, and the reason is that an ungated fallback is worse than no fallback. Webpack's `i.l` loader creates a script element and only settles on load or error, so answering a missing `624.<hash>.js` with `index.html` at status 200 produces a JavaScript parse failure instead of a clean 404. Angular's i18n loader requests `i18n/network-<lang>.json` through `HttpClient`, so an HTML fallback there produces a JSON parse failure. Gate the fallback on `Sec-Fetch-Mode: navigate` or an `Accept` header containing `text/html`, and return a real 404 for everything else.

The residual risk is narrow and worth stating. The router's prefix comes from `<base>` through a chain of three assumptions, and any future firmware that adds a `location.href` write cannot be intercepted at all. That is why the live browser test in `TEST STRATEGY` is a release gate rather than a nice-to-have.

### Sandbox

Omit the `sandbox` attribute entirely. `allow-same-origin allow-scripts` re-grants the real origin and script execution, which is exactly the unsandboxed state, so the attribute would document an isolation that does not exist. Dropping either token breaks the page, because without `allow-same-origin` the origin becomes opaque and origin-relative asset resolution changes. Core's `X-Frame-Options: SAMEORIGIN` already permits the frame, so there is nothing for the attribute to add.

### Why JavaScript bodies are never rewritten

Rewriting JS bodies is the tempting shortcut and it should be refused. You cannot distinguish a URL literal from prose with a regex. Rewriting the body invalidates the strong `ETag` the printer sends, so every reload refetches 805 KB. It breaks source maps and any subresource integrity hash. And in this bundle the load-bearing string is a template expression, not a literal, so the rewrite would fail on the exact case it was added for.

The one exception is a data-driven literal table for a dialect you have verified against a specific bundle:

```python
@dataclass(frozen=True, slots=True)
class LiteralPatch:
    """A JS-body substitution that is only legal against a verified bundle."""
    old: str
    new: str
    applies_to_sha256: str

    def __post_init__(self) -> None:
        if len(self.applies_to_sha256) != 64:
            raise ValueError("a literal patch requires the full bundle sha256 it was verified against")
```

The hash being non-optional is the point. It makes "a literal patch applied to an unverified bundle" unrepresentable, which is the failure mode that turns a proxy into a source of silent corruption. Refuse to apply the patch when the hash differs, log it, and fall back to the shim.

---

## 3. WebSocket bridging

### The architecture document's claim is wrong

`docs/architecture.md` lines 117 to 119 say:

> the WebSocket bridge is registered as a raw aiohttp route, since `HomeAssistantView` only dispatches `get/post/put/delete/patch/head/options` and therefore cannot accept an upgrade.

The premise is right and the conclusion is wrong. `helpers/http.py` line 172 does iterate exactly those seven methods and there is no `websocket` name:

```python
for method in ("get", "post", "delete", "put", "patch", "head", "options"):
    if not (handler := getattr(self, method, None)):
        continue
    handler = request_handler_factory(hass, self, handler)
    routes.extend(router.add_route(method, url, handler) for url in urls)
```

But a WebSocket upgrade is an HTTP `GET`, and `request_handler_factory` returns any `StreamResponse` unchanged:

```python
if isinstance(result, web.StreamResponse):
    # The method handler returned a ready-made Response, how nice of it
    return result
```

`web.WebSocketResponse` is a `StreamResponse` subclass. I proved that and proved the upgrade completes through a route registered this way, with the same outer middleware that mutates headers after the handler returns:

```
isinstance(WebSocketResponse, StreamResponse): True
handler names registered: ['get']
UPGRADE OK; negotiated subprotocol: None
REPLY: echo:hello:proto=None
bad token -> WSServerHandshakeError 403
plain GET bad token -> 403
```

So `HomeAssistantView` with `async def get(...)` returning a `WebSocketResponse` is the correct choice, and the raw route is not needed. That matters beyond style. Middlewares in aiohttp are app-level, not route-level, so a raw route sees the identical auth, security-filter, and headers stack. The raw route buys nothing and costs the `name`, `extra_urls`, and `requires_auth` conventions that every other view in this integration uses.

The method must not be named `websocket`. Doing so registers nothing and fails silently, which is the trap the current architecture note was reaching for.

### The auth consequence

`requires_auth` must be `False`, for the reason `views.py` already documents in its own module docstring:

> the views deliberately declare `requires_auth = False`: the Home Assistant authentication middleware would otherwise reject a plain `<img src>` request before the token could be checked.

The probe shows the same holds for the upgrade. `raise web.HTTPForbidden` before `prepare()` surfaces to a WebSocket client as a 403 handshake failure, which is a distinguishable error, so the card can tell "not authorised" from "printer unreachable". If `requires_auth` were left at its inherited `True`, `request_handler_factory` would raise `HTTPUnauthorized` before the token was read and the card would see a 401 handshake with no way to explain it.

The handler must also reject a non-upgrade request. A plain `GET` on the socket path is a normal request that arrives at the same handler and finds no `Upgrade` header. Check `Upgrade` and `Connection` and answer 426 or 400, because a browser that somehow navigates to that path should get an explanation rather than a hang.

### Subprotocol negotiation

The printer negotiates nothing. Connecting with no protocols yields `ws.protocol is None`, and when I offered `("sdcp", "moonraker")` aiohttp logged that the client protocols do not overlap the server's known set and returned no `Sec-WebSocket-Protocol` header.

That produces a real browser failure if the bridge is careless. A browser that offers subprotocols and receives none back kills the socket. So the rule is mirror, never invent:

- read the browser's `Sec-WebSocket-Protocol` request header,
- pass that same list as `protocols=` to the downstream `WebSocketResponse` so the handshake echoes it,
- pass only the subprotocol actually selected downstream to the upstream `ws_connect`.

For this printer the correct end state is simpler. The shim constructs `new WebSocket(url)` with no protocols argument at all, and the view declares `protocols=()`. A dialect that genuinely requires a subprotocol, such as Creality's `wsslicer` on port 9999, declares it in its `SocketRoute` and the bridge passes it through.

### Byte-for-byte relay, and the one deliberate exception

Relay by frame type, never by decoding text and re-encoding it. Switch on `msg.type` and call `send_str` or `send_bytes` accordingly. SDCP is JSON, but the printer also accepts binary-capable frames and any firmware revision can change that.

The exception is the camera URL, and it belongs in the shim rather than the bridge. The bridge stays byte-for-byte, and the shim rewrites the `VideoUrl` field in the browser's `onmessage` payload before the page's own handler parses it. Rewriting in the shim rather than in the bridge keeps the server-side relay honest and puts the dialect knowledge in the one place that is already dialect-specific.

The shim's ability to do that depends on how the page attaches its handler. `main.js` assigns `this.printerService.webSocket.onmessage = t => ...`, so an accessor on the patched class that wraps the assigned callback is sufficient here. A page that used RxJS's `WebSocketSubject` or `addEventListener("message", ...)` would need the wrapper placed differently, which is why the shim's interception surface is declared per dialect in the same literal table as section 2.

### Ping/pong

The browser WebSocket API exposes no ping or pong at all, so genuine passthrough is impossible. Terminate keepalive on each leg. Set `autoping=True` on both the `WebSocketResponse` and the `ws_connect`, so aiohttp answers the printer's pings locally and answers the browser's locally. Do not forward them.

This costs nothing for this printer, because SDCP already carries an application-level heartbeat as a JSON TEXT message. `main.js` runs `heartCheckStart()` with `period=3e4`. That is an ordinary TEXT frame and it passes through untouched.

Set `heartbeat=None` on both legs, and do not rely on the measured 3 second push cadence for liveness. That cadence was measured while the printer was actively printing. `docs/protocol-elegoo-sdcp-verified.md` line 70 describes `sdcp/status` as pushed on change and in reply to command 0, which means an idle printer may push nothing at all. A 30 second receive timeout would then kill a healthy idle connection, and an SDCP socket dying on an idle printer is exactly the failure a user would report as "the card goes stale overnight".

Protocol-level `heartbeat` is the wrong tool here anyway, for a reason that is easy to miss. aiohttp resets the heartbeat timer on every received message, so with data flowing the PING is never sent and the parameter is inert. It fires only once the printer goes quiet, and then `_pong_not_received` raises `ServerTimeoutError` and tears the connection down. Meanwhile the printer's server is hand-rolled enough that it emits a literal `%d` in an `Expires` header, and the vendor's own page chose an application-level JSON heartbeat rather than relying on protocol PING. Both are evidence the server may not answer PING at all.

Drive liveness with an application-level command 0 every 30 seconds instead, matching the heartbeat the printer's own page uses. That is a frame the printer demonstrably answers, it doubles as a status refresh when push is quiet, and it needs no protocol feature the printer may not implement.

Two aiohttp details to get right while wiring this. `ws_connect(receive_timeout=...)` is deprecated in 3.11.11, so pass `timeout=aiohttp.ClientWSTimeout(ws_receive=None, ws_close=2.0)` rather than the deprecated parameter. The default `ws_close` is 10 seconds, which is longer than the shutdown budget described under cleanup below.

### Max message size, and the knob that actually bounds memory

`max_msg_size` is not the write-side bound, and setting only it leaves the real buffer unbounded. Two facts from aiohttp 3.11.11 change the parameters.

First, `max_msg_size` also sizes the **reader** queue at twice its value, so a 4 MiB cap allocates an 8 MiB read buffer per socket. Second, the write side is bounded by `writer_limit`, which defaults to 64 KiB for `WebSocketResponse` and which `ws_connect` does not expose as a parameter at all. Setting `max_msg_size` alone therefore bounds the wrong direction.

The sizes follow from the measurement rather than from a round number. The file list was 14086 bytes for 78 entries, so an entry costs about 180 bytes. A 1 MiB cap would truncate at roughly 5800 files, which a USB disk full of G-code can reach. So:

| Leg | `max_msg_size` | `writer_limit` |
| --- | --- | --- |
| printer to HA, upstream | 4 MiB | not tunable, accept the 64 KiB default |
| HA to browser, downstream | 4 MiB | 64 KiB, set explicitly rather than inherited |

Treat an oversized frame as drop-frame-keep-socket, not as a fatal error. Closing both legs on `WSMessageTooBigError` means the user's file list never loads and the printer UI dies, which is a worse outcome than one missing frame. Log it, increment a counter on the bridge record, and keep the socket.

### Backpressure

The first version of this document claimed there is no queue and that `send_str` gives immediate backpressure. That was wrong in a way worth stating precisely, because it changes the design.

`send_str` awaits a drain only when the accumulated output exceeds `writer_limit` and the transport is paused, and aiohttp resets its output counter before awaiting. So the slack per leg is 64 KiB of writer buffer plus the transport high-water mark. At the measured 700 byte status frame that is roughly 180 frames, which at the printer's 3 second cadence is about nine minutes before a stalled browser is even noticed. The buffer is bounded, which is what matters for memory, but it is not small and the backpressure is not immediate.

That has one design consequence. A `StreamRoute` fan-out needs a per-viewer writer task with a bounded queue and a stall timer, because the reader must not block on a slow viewer and because the reference's `StreamHub` has no stall timer at all. Without one, a viewer that stops reading never leaves `_subscribers`, so `_async_idle_stop` never fires and the printer socket is pinned open forever. A dashboard tab that the user has navigated away from is exactly that case, and it is more likely than a stalled video player. Drop the viewer after a bounded stall and log the eviction.

On a `SocketRoute` each viewer owns its own upstream socket, so a stalled viewer stalls only itself and the queue question disappears.

The drop policy for a fan-out is per topic rather than per route. Drop oldest for `sdcp/status` and `sdcp/attributes`, because the newest reading supersedes the previous one. Never drop for `sdcp/response` or `sdcp/error`, because those carry command acknowledgements and file lists. Evict the viewer instead, with a diagnostic that names the reason.

`handler_cancellation=True` is set by Home Assistant at `components/http/__init__.py` line 628:

```python
self.runner = web.AppRunner(self.app, handler_cancellation=True, shutdown_timeout=10)
```

I proved what that buys, for both an abrupt TCP abort with no close frame and a graceful close:

```
  handler-start:abrupt
  handler-cancelled:abrupt
  upstream-cancelled:abrupt
  upstream-finally:abrupt
  handler-finally:abrupt:ws_closed=True
  handler-start:clean
  handler-cancelled:clean
  upstream-finally:clean
  handler-finally:clean:ws_closed=True
leaked tasks after cleanup: 0
```

So the cancellation does happen and closing the browser tab genuinely tears down the upstream leg. But the obvious `finally` is not safe, and the reason is the shutdown budget in that same call.

Home Assistant sets `shutdown_timeout=10`. aiohttp cancels the handler task and then awaits it under `asyncio.shield`, so a `finally` block that itself awaits something slow does not leak nothing. It leaks an orphan task. Two awaits are slow enough to matter. Awaiting the peer task while it is blocked inside `send_str` on a paused transport, and awaiting `ws.close()` on the client leg, which waits up to `ws_close` seconds for a close frame that a hand-rolled printer may never send.

So the `finally` must be written to a budget rather than to completion:

- cancel the peer task, then `await asyncio.wait_for(peer, 1.0)` inside a bare `except`, so a peer that ignores cancellation does not block the unwind,
- close the browser leg with `WebSocketResponse.close(drain=False)`, never a plain `close()`, because draining is what makes it slow,
- close the upstream leg through its own response object rather than an awaited graceful close, and set `ws_close` to 2.0 seconds rather than the 10 second default, as noted under ping and pong,
- in the `StreamRoute` case, close the viewer's writer task the same way.

Also count the tasks honestly. A bridge is not two tasks when a heartbeat is configured, because aiohttp spawns its own ping task. It is not two tasks on the hub path either, because each fan-out viewer has a writer task. Any test that asserts a task count must derive it from the configured shape rather than assuming two.

One thing that is safe, and worth recording so nobody adds a defensive wrapper for it. Cancelling the handler while it is awaiting `ws_connect` does not leak a connection. aiohttp closes the response on `BaseException` and releases the connector's placeholder, and there is no await between the two operations where a cancellation could land. Use `async with` for readability, not for leak protection.

### Sharing policy belongs to the route kind

My first draft said one upstream socket per entry, fanned out to N viewers, justified by the printer's `MaximumVideoStreamAllowed: 4`. That was wrong twice over, and the correction is the most important change in this document.

The evidence was misapplied. `MaximumVideoStreamAllowed: 4` and `NumberOfVideoStreamConnected: 1` are fields of the **video** stream, reported in the same attributes object as `CameraStatus` and `Capabilities`. They say nothing about how many SDCP control sockets the printer tolerates. I read them live and then used them to justify a decision about a different port.

The second reason is the one I got wrong, and the correction is worth recording because the first version of this document repeated an unverified claim. A reviewer asserted that the file list frame carries no `RequestID` and that a fan-out hub therefore destroys correlation outright. I tested that against the printer by sending command 258 with a known `RequestID` and dumping every frame:

```
RequestID sent: da102d47-2774-4a80-a16f-9ecbe498d628
len=  14086  topic=sdcp/response/5c441dd301  Cmd=258  RequestID=YES da102d47-277
    FileList entries=78
```

So the premise is false. The response envelope does carry `RequestID`, and it echoes the value that was sent. Correlation is technically possible through a fan-out, because a browser can discard frames whose `RequestID` it never issued.

The recommendation survives the correction, on a narrower and more honest argument. A fan-out would make the proxy depend on the printer's own minified page correctly filtering responses by `RequestID`, which is not a property anyone has verified and which would be invisible when it failed. It also copies one viewer's 14086 byte file list to every other browser on that entry, including browsers that never asked. Per-viewer sockets preserve correlation by construction and make no assumption about the page's filtering. I still ship per-viewer, and I would change my mind if a two-socket probe showed the printer tolerates only one control socket, in which case a hub with explicit ownership handoff becomes the only option.

The measured frame also settles the size question in section 5. Seventy-eight entries cost 14086 bytes, so an entry is about 180 bytes and the original 1 MiB cap would have truncated a file list at roughly 5800 files.

So the sharing policy is a property of the route kind, not a per-entry decision:

| Route kind | Sharing | Why |
| --- | --- | --- |
| `StreamRoute` | one upstream connection, fanned out to N viewers | a broadcast medium, where the latest frame supersedes the previous one, and the printer's own limit of 4 video streams is what constrains us |
| `SocketRoute` | one upstream connection PER viewer | a request and response channel, where a fan-out forces the proxy to rely on the printer's own page filtering frames by correlation identifier |

This is the discriminated union in section 6 doing real work. The policy is declared once on the type and the request path reads it, rather than a hub being right for one port and wrong for another with a conditional in between.

The `StreamRoute` case mirrors `StreamHub` exactly, and that is the reference repository's stated thesis in `hub.py`, that several browsers watch one camera through a single upstream connection. Keep the lazy connect, the exponential backoff, the idle stop, and the drop-oldest policy. Add one thing the reference does not have. `StreamHub` has no stall timer, so a browser that stops reading keeps its queue in `_subscribers` forever and `_async_idle_stop` never fires, which pins the printer socket open indefinitely. A web UI in a dashboard tab that the user navigated away from is exactly that case, and it is more likely than a stalled video player.

The `SocketRoute` case needs a per-viewer upstream socket plus one `asyncio.Lock` per entry. The lock is not for the socket, since each viewer owns its own. It is for mutating commands. Two views of the same printer mounted on one dashboard must not both send command 130 concurrently, and serializing at the entry is the only place that can be enforced, because the printer has no notion that the two viewers are related.

One honest unknown. Whether the printer accepts more than one concurrent SDCP control socket is **UNVERIFIED**. The probe that established the socket works used a single connection. If the printer turns out to accept only one, the per-viewer design degrades to a hub with an explicit ownership handoff, and the `Sharing` field is what makes that a one-line change rather than a rewrite. The test strategy requires a two-socket probe against the real printer before either shape is locked in.

---

## 4. Authentication and open-proxy safety

### The scheme

Reuse `PayloadSigner` unchanged. It is HMAC-SHA256 over base64url with the padding stripped, it uses `hmac.compare_digest`, and it already rejects tampering. Nothing about the printer changes any of that.

Extend the signed payload by exactly one field:

```python
{"e": entry_id, "r": resource, "x": expiry}      # today, in security.py
{"e": entry_id, "s": scope,    "x": expiry}      # web scope, proposed
```

The token stays a path segment. The reference already gives the reason, and it applies here unchanged:

> The token is a path segment, so players that append query parameters of their own cannot break authentication.

The view gates the token kind with a `resources` frozenset, reusing `ProxyView._token()` verbatim:

```python
media = runtime.tokens.async_verify(token, entry_id=entry_id)
if media.resource not in self.resources:
    raise InvalidToken(f"token for '{media.resource}' cannot be used on '{self.name}'")
```

Adding `s` is what makes that guard complete. Without a scope, a long-lived `RESOURCE_STATUS` token used for polling would also be a valid web token, because `async_verify` only checks `e` and `x`. With `s` present and the web views declaring `resources = frozenset({RESOURCE_WEB})`, a media token cannot be replayed on a web route and a web token cannot be replayed on a media route.

### Why no cookie

There is a real case for a cookie here, and it should be stated before it is rejected. A path-segment token lives in the iframe URL. A full page navigation triggered by the printer's own JS would need to carry that token forward, and a cookie scoped to the proxy prefix would survive such a navigation automatically. That is the strongest argument for a cookie and it is still not enough.

Home Assistant's `auth_middleware` in `components/http/auth.py` accepts exactly two things. It accepts a Bearer `Authorization` header, and it accepts an `authSig` query parameter validated against path, params, and a refresh token. It never consults a cookie. So a cookie cannot authenticate against core, and since the view already declares `requires_auth = False` and checks its own token, a cookie adds no authentication power at all.

What it adds is ambient authority. A cookie scoped to `/` is attached by the browser to every same-origin request, including `/api/`, `/auth/token`, and the Home Assistant frontend. The iframe's contents are third-party code we do not control and did not write, so a cookie would let the printer's own JavaScript reach Home Assistant endpoints with credentials it should never hold. Path-scoping the cookie to the proxy prefix narrows this but introduces `Path` and `SameSite` behaviour across an HTTPS Home Assistant behind nginx or Nabu Casa, which is exactly the class of thing that fails silently for one user and works for everyone else.

The navigation case is handled by pass 6 instead. `<base>` plus the SPA fallback keeps the prefix in the URL, and that is a routing problem rather than an authentication problem.

### The cookie that does exist

OctoPrint, Duet, and others authenticate their own session with a cookie. That cookie belongs to the printer, not to us, and it must not reach the browser. The proxy holds a server-side jar per entry. On an upstream `Set-Cookie`, strip `Domain`, rewrite `Path` to the proxy prefix, and store it on the runtime. On a downstream request under that prefix, replay the stored value upward and never emit `Set-Cookie` to the browser.

This splits the two authentications cleanly. The browser authenticates to the proxy with the path token. The proxy authenticates to the printer with the printer's session. The browser's cookie jar stays untouched, so the printer's JS cannot read or modify anything belonging to Home Assistant.

The cost is that concurrent viewers share one printer session. For a single-household installation that is the right trade, and it is what a reverse proxy with its own auth does everywhere.

### Open-proxy safety

The web scope never accepts a client-supplied URL. The reference's signed `u` field exists for HLS, where the playlist genuinely names arbitrary segment URLs, and `is_safe_upstream_url` is what keeps it honest by rejecting anything that is not absolute `http` or `https`. The web scope needs no such field, because a browser resolving relative references produces relative paths, not arbitrary hosts. Removing the field is strictly safer than validating it.

One pure function composes the target, and it is the only place in the integration where a request path becomes a URL:

```python
def resolve_route_target(origin: str, port: int, raw_path: str) -> str:
    """Compose an upstream URL from a fixed origin and a request path.

    Never uses urljoin. Composes from components and re-verifies the result.
    Raises RouteRejected for anything that could leave the origin.
    """
```

**Never call `urljoin` here.** This is the single most dangerous line in the design and it fails silently. `urljoin("http://192.168.128.143", "//evil.com/x")` returns `"http://evil.com/x"`, because a leading `//` makes the second argument a network-path reference and replaces the authority. Home Assistant's `security_filter_middleware` does not catch it. Its regex is `(\.\.//?)+` plus a `<script` pattern plus `[a-zA-Z0-9_]=/([a-z0-9_.]//?)+`, and `//evil.com` matches none of them, so an aiohttp app with core's real middleware returns 200 for `/api/generic_3dprinter/E/TOK/p80//evil.com/x`.

The reference implementation never had this hole, and it is worth naming why. There, the upstream URL was itself inside the signed payload, and `security.py` re-validated it on verification with `is_safe_upstream_url`, which returns `False` for `//evil.com/x` because it has no scheme. Dropping the signed `u` field, which is the right call for the web scope, also drops that guard. It must be replaced rather than merely omitted.

So the function composes, then re-verifies, then asserts the authority:

```python
composed = f"{scheme}://{host}:{port}{normalized_path}"
if not is_safe_upstream_url(composed):          # reuse signer.py, unchanged
    raise RouteRejected("composed URL is not a safe upstream")
parts = urlsplit(composed)
if parts.hostname != expected_host or parts.port != expected_port:
    raise RouteRejected("composed URL escaped the entry origin")
```

Reusing `is_safe_upstream_url` verbatim is deliberate. It is the reference's own guard, it already exists, and it is already tested in `tests/test_security.py`.

It must reject, after unquoting in a loop until the value stops changing, in this order:

| Input | Why it is refused |
| --- | --- |
| a path starting with `//` | a network-path reference, and the open proxy above |
| a path containing `://` | an absolute URL smuggled into the path position |
| a path containing `?` or `#` | `path = "?q=1"` composes to `http://host:3030?q=1`, which is the printer's root with attacker parameters, and `#frag` truncates the request |
| a path containing `..` as a segment after unquote | aiohttp's client resolves `..` during URL parsing, so the path this function validated and the path the printer receives can differ |
| a path containing a backslash | some parsers treat `\` as `/` |
| a path containing `%00` | truncation in a downstream C parser |
| a path containing a `%` that does not decode to a complete escape | malformed input that different layers treat differently |
| a path longer than a fixed ceiling | an unbounded input to a regex-based guard |
| a port not in the route table | the table is the authority for which ports exist |

Two subtleties about where the input comes from. First, aiohttp hands `match_info` values already percent-decoded, so `request.path` for `/p80/%2f%2fevil.com/x` is `///evil.com/x` while `raw_path` stays encoded. Decode with this function's own loop rather than trusting either, and reject on the decoded form. Second, Home Assistant's filter does recursively unquote, so `%252e%252e%252f` and `%2e%2e%2f` are both 400'd before the view runs. That is defence in depth, not a substitute, because the filter is a regex over the whole path and this function is a decision about one specific request.

The route table is what makes an unknown port impossible rather than merely rejected. A registry maps port to route, and a miss is a 404 before any network work happens.

One more consequence of the filter runs the other way. The pattern `[a-zA-Z0-9_]=/([a-z0-9_.]//?)+` matches an ordinary query parameter whose value starts with a slash, so a printer call like `?file=/models/x` becomes a hard 400 with no useful message. The proxy cannot fix that, because the filter runs before the view. It is a reason to keep the proxy's own URL free of query parameters, and a reason to expect that some printer API calls will need the shim to move a path out of the query string and into the path.

### Replay and leak

Replay across entries is already impossible and stays impossible. `async_verify` raises `InvalidToken("token does not belong to this entry")` when `token_entry != entry_id`, and the view passes the entry id from the matched path. Binding `s` closes the remaining cross-scope hole.

The token is still a bearer credential sitting in an iframe URL, so it reaches the Home Assistant access log and the browser history. Three mitigations, and the first is free because core already does it. Home Assistant sets `Referrer-Policy: no-referrer` on every response in `headers.py`, so the token never appears in an outbound `Referer`. Second, the TTL should be shorter than the media `SIGNED_URL_TTL` of six hours, because a printer UI is loaded interactively and can re-mint on a 403. Fifteen to thirty minutes is the right range. Third, nothing may log the token, and the redaction helper should follow `redact_url` in `upstream.py` rather than inventing a second shape.

---

## 5. Failure isolation

### Timeouts, per route kind

| Route kind | Total | Connect | Read | Rationale |
| --- | --- | --- | --- | --- |
| document and asset | bounded, from config | `sock_connect` | `sock_read` | a whole small response, so a total bound is correct |
| rewritten body | `upstream_timeout` | `sock_connect` | n/a | must complete to be rewritten, so it cannot be unbounded |
| MJPEG stream | `None` | `sock_connect` | `stall_timeout` | endless by nature, matching `MediaProxyView.handle` |
| socket | `None` | `sock_connect` | `receive_timeout` | 30 seconds, being ten times the measured 3 second push cadence |

The reference already encodes the first and third rows as `runtime.client.timeout(total=None, sock_read=runtime.config.stall_timeout)` for streams and `self.timeout(total=float(self._config.upstream_timeout))` for finite reads. Reuse those helpers rather than adding a second timeout vocabulary.

### Streaming, never buffering

Every upstream body is consumed with `iter_chunked` at the reference's `STREAM_CHUNK = 65536`:

```python
async for chunk in upstream_response.content.iter_chunked(STREAM_CHUNK):
    await response.write(chunk)
```

Never `await response.read()` except in the one bounded place described next. The 450 KB/s MJPEG stream and the 805 KB JavaScript bundle both stream through. The MJPEG case must not be buffered at all, because a camera that runs for hours would grow memory without bound.

### Size caps, applied narrowly

Only `text/html` and `text/css` are buffered for rewriting. Everything else is streamed. That narrowness is the design's main isolation property, and it is what keeps the 805 KB `main.js` and the 510 KB `styles.css` off the rewrite path entirely. The largest buffered body on this printer is the 2166 byte document.

The cap uses the reference's exact pattern from `read_bytes`, which reads one byte past the limit so an oversized body is detected rather than truncated:

```python
body = await response.content.read(limit + 1)
if len(body) > limit:
    raise UpstreamError(f"the upstream {what} is larger than {limit} bytes")
```

The reference's `MAX_TEXT_BYTES` is `1024 * 1024`. For rewritten documents a 4 MiB cap is generous, and the right behaviour on exceeding it is to pass the body through unrewritten with a diagnostic rather than to fail, because a large HTML document that renders without a rewritten `<base>` is better than a 502.

The CPU cost is bounded by the cap. A 4 MiB regex pass on the event loop is tens of milliseconds, which is acceptable, and if a dialect ever needs a larger cap the rewrite moves to `hass.async_add_executor_job`. State the cap as the thing that makes the CPU cost bounded.

### The HTTP client

A dedicated session per entry, as `create_session` in `upstream.py` already does, with the docstring's reason unchanged: long-lived connections stay out of Home Assistant's shared pool, and each entry decides its own TLS verification.

Two things must change, and the second is a real bug rather than a tuning choice.

First the pool size. `create_session` uses `TCPConnector(limit=0, limit_per_host=0)`, which is unbounded. That is right for media, where a proxy holds one or two long-lived connections. It is wrong for a web UI, where an SPA opens a burst of parallel asset requests, and unbounded means a slow printer accumulates sockets on every reload.

Second, the limit alone is not enough, because there is no timeout behind it. `create_session` sets `timeout=aiohttp.ClientTimeout(total=None)` on the session, and aiohttp applies no timeout to the wait for a free connection slot when the delay is None. So the ninth concurrent request to one host does not fail and does not queue visibly. It waits forever, and a two-tab load hangs with no error anywhere to explain it. Note also that aiohttp's connection key includes the port, so ports 3030 and 3031 are separate buckets and do not consume the per-host budget.

So the web routes need an explicit timeout on every buffered fetch, of the shape `read_bytes` already uses in the reference, which reaches `stream=False` and therefore a finite total:

```python
timeout = runtime.client.timeout(total=float(self._config.upstream_timeout))
```

and a pool large enough for two tabs:

```
limit=32, limit_per_host=16
```

Sixteen covers a document, `runtime`, `polyfills`, `main`, `styles`, `iconfont.css`, two lazy chunks, and up to three fonts, twice over. Keep `total=None` for the streaming route only, where an endless body is the point.

### Circuit breaker

Copy `StreamHub._async_handle_failure` exactly. Doubling backoff with a ceiling, a state and an error message exposed for diagnostics:

```python
delay = min(max(self._config.reconnect_interval, self._backoff), self._config.max_backoff)
self._backoff = min(delay * 2, self._config.max_backoff)
```

The breaker is per port, because a dead web UI on port 80 says nothing about the camera on 3031. Without it, a dead printer costs one connect attempt per asset request, which for an SPA is dozens per reload and hundreds if the user retries.

### The camera route

The camera must be proxied, and it must not go through a Home Assistant camera entity.

Proxying is required rather than optional. The printer page is served from port 80 over plain HTTP, so an `<img src="http://192.168.128.143:3031/video">` inside the iframe is mixed content on an HTTPS dashboard and the browser blocks it. That is the same reason `views.py` gives in its own module docstring for proxying media through Home Assistant.

Serving it as an HA camera entity is the wrong mechanism, for a reason specific to this design. The `src` lives inside the printer's own Angular document as an `<img>`, so the only thing that can satisfy it is a URL that returns `multipart/x-mixed-replace`. A camera entity exposes `/api/camera_proxy_stream/...` with its own rotating token, which the printer's page knows nothing about, and its lifecycle is owned by the camera platform rather than by the page. Serve the camera on its own token-in-path route, shaped like the reference's `StreamView`, and let the shim rewrite the 3031 URL to point at it.

The cost is bandwidth at the Home Assistant origin rather than CPU. The measured rate is about 450 KB/s per viewer at 10 fps, so four viewers is about 1.8 MB/s. The reference's re-emit does a memcpy per part and `MultipartFrameParser` is a `bytearray.find`, so the CPU is negligible. The `StreamRoute` fan-out from section 3 is what keeps this to one upstream connection regardless of viewer count.

### Connection and task leaks

Every upstream open uses `async with self._client.open(...)`, so an exception mid-body cannot leak a connection.

The reload path is the one that leaks, and the reference does not model it because it has no socket bridge. `remove_runtime` currently only pops the entry from `hass.data`. `ProxyRuntime.async_stop` closes the shared session, which kills the upstream WebSocket transport while leaving the downstream browser socket open with no upstream and no reconnect. That is a half-dead bridge, and it is what a config entry reload produces.

So unload has an order and it is not the order the reference uses. Close every live downstream socket first, with code 1011 so the page can tell deliberate teardown from a crash. Await the handler tasks under a short timeout, using the same cancellation-safe pattern as the `finally` above. Then stop the hub and close the session. Then call `remove_runtime`. Closing the session first, which is what happens today, is the specific mistake to avoid.

### What the card shows when the proxy is down

The card must not render an iframe into a dead proxy. A browser's own error page inside a dashboard tile is ugly and unactionable, and it gives the user no way to tell a dead printer from a bad token.

Gate the iframe on a status response. `PrinterStatus` in `models.py` already carries `online`, `last_error`, and `web_proxy_url`, and the reference already exposes exactly this pattern through `StatusView` returning `runtime.status()` and `describe()` handing the card a `status_url`. The card polls the status endpoint, renders its own chrome from the normalised snapshot, and shows an explicit unavailable state with the last error and a retry action. The iframe is created only once status reports healthy, and its `src` is removed when the card is detached.

Do not rely on the iframe's `onerror`. It does not fire for every failure mode, including a 403 from a stale token. The status endpoint is the signal.

---

## 6. The data shape

A registry over branching conditionals. The domain here is a per-entry table of upstream ports, each with a different serving strategy, so the type is a table from port to route and the strategy is a discriminated union. That removes every `if port == 80` and every `if kind == "ws"` from the request path.

### Route kinds

```python
# proxy/routes.py
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar, Final, Literal


class RouteKind(StrEnum):
    """How one upstream port is served."""

    DOCUMENT = "document"   # text/html, rewritten, no-store
    ASSET = "asset"         # any other body, rewritten only when text/css
    STREAM = "stream"       # multipart MJPEG, never buffered, never rewritten
    SOCKET = "socket"       # a WebSocket upgrade


class Timeouts:
    """Named timeouts so a route never carries a bare float."""

    total: float | None
    sock_read: float | None
    receive: float | None


@dataclass(frozen=True, slots=True)
class HttpRoute:
    """A port served over request and response."""

    port: int
    rewrite: RewriteProfile
    timeouts: Timeouts
    max_rewrite_bytes: int
    dialect: str

    kind: ClassVar[Literal[RouteKind.DOCUMENT, RouteKind.ASSET]] = RouteKind.DOCUMENT


class Sharing(StrEnum):
    """How one upstream connection relates to browser viewers.

    Declared on the route kind rather than decided per request, because the
    answer follows from whether the protocol is a broadcast medium or a
    request and response channel.
    """

    FANOUT = "fanout"          # one upstream, N viewers, drop-oldest queues
    PER_VIEWER = "per_viewer"  # one upstream per viewer, correlation preserved


@dataclass(frozen=True, slots=True)
class StreamRoute:
    """A port served as a long-lived multipart body."""

    port: int
    path: str
    stall_timeout: int
    queue_size: int
    dialect: str

    kind: ClassVar[RouteKind] = RouteKind.STREAM
    sharing: ClassVar[Sharing] = Sharing.FANOUT


@dataclass(frozen=True, slots=True)
class SocketRoute:
    """A port served as a bridged WebSocket."""

    port: int
    path: str
    subprotocols: tuple[str, ...]
    max_msg_size: int
    writer_limit: int
    keepalive_command: int | None
    keepalive_interval: int
    dialect: str

    kind: ClassVar[RouteKind] = RouteKind.SOCKET
    sharing: ClassVar[Sharing] = Sharing.PER_VIEWER


#: The union. Narrowed with isinstance, never with a string comparison.
ProxyRoute = HttpRoute | StreamRoute | SocketRoute
```

`kind` and `sharing` are both `ClassVar`, so neither is a dataclass field and neither can be set to a wrong value by a caller. A caller cannot construct a `StreamRoute` whose kind says `SOCKET`. `max_rewrite_bytes` lives only on `HttpRoute`, so "a socket route with a rewrite cap" is not a representable value. `queue_size` lives only on `StreamRoute` and `writer_limit` only on `SocketRoute`, because the two kinds have different backpressure shapes and a shared field would invite applying one to the other.

`keepalive_command` on `SocketRoute` is what carries the application-level liveness from section 3 as data. For SDCP it is `0`, which the printer demonstrably answers. A dialect that has no safe read-only command sets it to `None` and gets no keepalive rather than a guessed one.

### The route table

```python
@dataclass(frozen=True, slots=True)
class RouteTable:
    """The only authority for which ports exist and how each is served.

    Built once at the configuration boundary from validated config, then trusted.
    A miss is a 404 before any network work happens, which is what makes an
    unconfigured port impossible rather than merely rejected.
    """

    routes: Mapping[int, ProxyRoute]

    def require(self, port: int) -> ProxyRoute: ...

    @property
    def ports(self) -> frozenset[int]: ...

    @property
    def default_port(self) -> int:
        """The port a bare root-absolute path resolves to."""
```

For the Centauri Carbon the table has three entries and is declared as data:

```python
CENTAURI_CARBON_ROUTES: Final[RouteTable] = RouteTable(
    routes={
        80: HttpRoute(
            port=80,
            rewrite=DEFAULT_HTML_REWRITE,
            timeouts=Timeouts(total=10.0, sock_read=30.0, receive=None),
            max_rewrite_bytes=4 * 1024 * 1024,
            dialect="elegoo",
        ),
        3030: SocketRoute(
            port=3030, path="/websocket", subprotocols=(),
            max_msg_size=1024 * 1024, idle_timeout=60, dialect="sdcp",
        ),
        3031: StreamRoute(
            port=3031, path="/video", stall_timeout=30, dialect="elegoo",
        ),
    }
)
```

### Rewrite rules as data

The ordered pass in section 2 becomes a tuple, so the ordering is testable and the shim and the server share one declaration.

```python
class RewriteSite(StrEnum):
    BASE_HREF = "base_href"
    ABSOLUTE_ATTR = "absolute_attr"
    PROTOCOL_RELATIVE_ATTR = "protocol_relative_attr"
    CSS_URL = "css_url"
    CSS_IMPORT = "css_import"
    JS_LITERAL = "js_literal"
    SPA_FALLBACK = "spa_fallback"


class RewriteWhere(StrEnum):
    SERVER = "server"
    SHIM = "shim"
    BOTH = "both"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True, slots=True)
class RewriteRule:
    """One pass of the rewrite, carrying its own justification.

    `reason` is required because every rule is a claim about what breaks if it is
    skipped, and a rule whose reason nobody can state is a rule to delete.
    """

    order: int
    site: RewriteSite
    where: RewriteWhere
    required: bool
    reason: str
    literal_patch: LiteralPatch | None = None

    def __post_init__(self) -> None:
        if self.site is RewriteSite.JS_LITERAL and self.where is RewriteWhere.SERVER:
            if self.literal_patch is None:
                raise ValueError("a server-side JS literal rewrite requires a verified LiteralPatch")
```

That `__post_init__` is the type discipline from `LiteralPatch` stated once more at the level where the mistake is actually made. Rewriting JavaScript on the server without a verified bundle hash is the failure mode that corrupts the page silently, and it is now constructible only by first constructing the object that records the hash it was verified against.

```python
@dataclass(frozen=True, slots=True)
class RewriteProfile:
    """The ordered passes for one dialect."""

    rules: tuple[RewriteRule, ...]
    strip_headers: frozenset[str]
    passthrough_headers: tuple[str, ...]
    inject_shim: str | None

    def __post_init__(self) -> None:
        orders = [rule.order for rule in self.rules]
        if orders != sorted(orders) or len(set(orders)) != len(orders):
            raise ValueError("rewrite rules must have unique, ascending order")
```

### Live bridges

A mutable record with a frozen projection, exactly the split `StreamHub` and `HubStats` already use in the reference.

```python
class CloseReason(StrEnum):
    BROWSER_CLOSED = "browser_closed"
    UPSTREAM_CLOSED = "upstream_closed"
    UPSTREAM_ERROR = "upstream_error"
    MESSAGE_TOO_BIG = "message_too_big"
    PROTOCOL_ERROR = "protocol_error"
    IDLE = "idle"
    CANCELLED = "cancelled"
    STOPPING = "stopping"


@dataclass(slots=True)
class BridgedSocket:
    """One live bridge. Mutable, owned by the hub's event loop."""

    bridge_id: str
    entry_id: str
    port: int
    path: str
    subprotocol: str | None
    opened_at: float
    downstream: web.WebSocketResponse
    upstream: aiohttp.ClientWebSocketResponse | None = None
    frames_to_browser: int = 0
    frames_to_printer: int = 0
    bytes_to_browser: int = 0
    bytes_to_printer: int = 0
    close_reason: CloseReason | None = None


@dataclass(frozen=True, slots=True)
class SocketStats:
    """The serialisable projection, for status and diagnostics."""

    bridges: int
    bridges_by_reason: Mapping[str, int]
    frames_to_browser: int
    frames_to_printer: int
    bytes_to_browser: int
    bytes_to_printer: int
```

`close_reason` is an enum rather than a string, so the card and diagnostics cannot invent a reason. The mutable record never leaves the runtime. Diagnostics get `SocketStats`, which mirrors how `ProxyRuntime.status()` reports `HubStats` rather than the hub.

### The runtime

```python
@dataclass(slots=True)
class WebProxyRuntime:
    """Everything the web proxy views, the card and diagnostics need.

    Mirrors ProxyRuntime in the reference so the two read the same and the
    coordinator does not need a second vocabulary.
    """

    hass: HomeAssistant
    entry: ConfigEntry
    config: PrinterConfig
    origin: str                      # validated once, never re-derived
    routes: RouteTable
    tokens: TokenManager
    client: UpstreamClient           # reused from the reference unchanged
    session: aiohttp.ClientSession   # dedicated, bounded connector
    sockets: SocketHub               # one upstream socket, N viewers
    bridges: dict[str, BridgedSocket]
    breakers: dict[int, Breaker]
    cookies: CookieJar               # the printer's own session, server-side only

    @property
    def entry_id(self) -> str: ...

    def status(self) -> dict[str, object]:
        """Return a serialisable status document, redacting the token."""

    async def async_stop(self) -> None:
        """Close bridges, stop the hub, close the session. Idempotent."""
```

`origin` is a stored validated string rather than a property derived from config on every request. That is deliberate. It is the one value the open-proxy guard depends on, and deriving it per request gives a second place where it could differ from what was validated.

`cookies` and `sockets` are genuinely shared across concurrent viewers of one entry. The sharing is real and required, since one printer has one session and one SDCP socket. Resolve it by construction rather than by locking. The hub owns the only upstream writer, so two viewers sending commands both go through `hub.async_send()` and serialize there. The cookie jar is touched only from the event loop.

---

## RISKS

**The shim is unverifiable by unit test.** Its correctness depends on how a specific minified bundle happens to construct URLs and attach handlers. A unit test over the shim source proves almost nothing. The only honest verification is loading the real printer through a running Home Assistant in a real browser and exercising the print-status pane, the camera pane, and a settings navigation. Treat that as a release gate, not a nice-to-have.

**Pass 6 rests on a three-link chain.** `<base>` is rewritten, Angular reads the base href from the DOM, and the printer's router therefore keeps the prefix in the URL on a full navigation. All three are individually verified and the composition is not. The specific user action that breaks if the chain fails is a browser refresh, or a bookmark, from a deep-linked printer route. Mitigate with the SPA fallback and a test that reloads a deep link.

**Terminating ping/pong is a departure from the letter of the requirement.** It is the only correct option because the browser API has no ping surface, but if a printer ever made liveness depend on a ping it could observe, this would hide a dead link. The countermeasure is the application-level heartbeat and the measured push cadence, both of which exist on this printer and neither of which is guaranteed on others.

**One upstream socket means one failure domain per entry.** A wedged printer socket takes down the card's controls for every viewer, where per-viewer sockets would isolate them. The printer's own `MaximumVideoStreamAllowed: 4` and the print-control race make the shared socket the right choice anyway, but the failure is more visible, so the status endpoint must distinguish "socket down" from "printer down".

**The server-side cookie jar builds a session nobody can see.** If the printer rotates its session cookie on a timer, the proxy must re-authenticate silently, and a user has no way to observe or force it. Add an explicit re-authenticate action rather than making the only recovery a config entry reload.

**The token is in the URL and the browser history is outside our control.** A short TTL limits the blast radius. It does not eliminate the exposure for anyone who has access to the browser's history on a shared machine. Worth stating in the integration's own docs rather than leaving implicit.

**`docs/architecture.md` currently encodes a wrong rationale.** Lines 117 to 119 will keep producing the raw-route design in any future implementation unless they are corrected, and the correction is one paragraph.

**`const.py` line 75 declares `QUERY_PATH` for a design that must not exist.** Leaving it invites the query-parameter design back, and that design trips Home Assistant's security filter. It should be deleted rather than left unused.

**Rewrite CPU runs on the event loop.** Bounded by the cap, but the cap is per-response and a printer serving many large HTML documents to many viewers multiplies it. If a dialect ever needs a cap above a few MiB, move the rewrite to an executor.

---

## TEST STRATEGY

Every test below stands up a real loopback aiohttp upstream in the style of `tests/upstream_server.py`, which serves real sockets, real framing, and real byte ranges rather than mocks. The four throwaway probes I wrote are folded in as permanent tests, because their results are what several decisions rest on.

### Extending the loopback upstream

`tests/upstream_server.py` gains one route group so a printer can be simulated faithfully. The fixture must reproduce the four properties that the real printer has and that a naive fake would miss.

| Fake route | Must reproduce |
| --- | --- |
| `/` | `<base href="/">`, relative script tags, one root-absolute stylesheet, and a shim-visible `<head>` |
| `/runtime.<hash>.js` | a body containing a bare-relative chunk loader, so `<base>` rewriting is observable |
| `/624.<hash>.js` | a lazy chunk, proving `{path:.*}` crosses slashes and the prefix applies |
| `/api/detail.do` | JSON carrying a camera URL field, to prove the shim's payload rewrite |
| `/websocket` on a second port | a real upgrade with no subprotocol, pushing frames on a timer |
| `/video` on a third port | `multipart/x-mixed-replace` with real JPEG frames, endless |
| `/framing` | a response carrying `X-Frame-Options: DENY`, `Content-Security-Policy: frame-ancestors 'none'`, and `Strict-Transport-Security`, to prove pass 0 |
| `/slow` | headers then silence, to prove the stall timeout |
| `/huge` | a body over `max_rewrite_bytes`, to prove the pass-through fallback |
| `/flaky` | fails N times then succeeds, to prove the breaker |

Three ports is the important part. A single-port fake cannot test the decision this whole design exists to make.

### Per-decision tests

**Decision 1, shim over rewrite.** Serve the fake printer's HTML through the proxy, then assert on the returned document that `<base href>` points at the proxy prefix, that the root-absolute stylesheet href was rewritten, that the bare-relative script src was NOT rewritten, and that the shim script tag precedes every printer script tag. Then, in a headless browser against the fake printer, assert that a page whose script reads `window.location.hostname` and opens `ws://${host}:3030/websocket` ends up connected to the proxy's socket route. That last assertion is the one that proves the decision, and it is the reason the fake must reproduce the template-literal construction rather than a static URL.

**Decision 1, header scrub.** Request `/framing` through the proxy and assert the response carries no `X-Frame-Options`, no `Content-Security-Policy`, and no `Strict-Transport-Security`, while still carrying core's own `SAMEORIGIN` that the middleware adds afterward. Assert the printer's `Set-Cookie` on a document response does not reach the browser.

**Decision 2, grammar.** A routing test asserts that `p80`, `p3030`, `p3031`, a single-segment path, a five-segment path, and an empty path all resolve, and that a sibling literal route under the same base is not shadowed. This is the test that pins the catch-all overlap hazard.

**Decision 2, ordered pass.** A table-driven test over the fake document asserts each pass in isolation, and a second test asserts the ordering by checking that a `url()` inside an inline `<style>` is rewritten once and not twice. Idempotence gets its own test, because applying the pass twice must produce the same bytes.

**Decision 2, open-proxy guard.** A table-driven unit test over `resolve_route_target` with the full hostile input list from section 4, plus the legitimate cases. Both the rejection set and the acceptance set are asserted, because a guard that rejects everything passes a rejection-only test. Add an integration test that a request for `/api/generic_3dprinter/{entry}/{token}/p80//evil.example/x` does not open a connection to `evil.example`, checked with a connector that records every host it is asked to reach.

**Decision 3, upgrade through a `get` handler.** A test that opens a real WebSocket against the proxy's socket route and completes a round trip, proving the view shape works end to end rather than in a probe. Assert that a bad token fails the handshake with 403 and not 401, matching the distinction `_rejected` already draws in the reference.

**Decision 3, subprotocol mirroring.** Connect with no protocols and with a wrong protocol, and assert the printer side received exactly the subprotocol that was negotiated downstream and never the client's whole list. The fake upstream should record the `Sec-WebSocket-Protocol` header it was sent, because that is the assertion that catches inventing.

**Decision 3, byte-for-byte.** Push TEXT frames, BINARY frames, and a binary frame containing invalid UTF-8 through the bridge and assert the bytes received equal the bytes sent. The invalid-UTF-8 case is the one that catches a `send_str` on a binary frame.

**Decision 3, cleanup.** Reuse the shape of my cancellation probe as a test: open a bridge, abort the client TCP socket with no close frame, and assert that the upstream leg's `finally` ran, that the hub's subscriber count returned to zero, and that `asyncio.all_tasks()` has no leftover task. Repeat with a graceful close. The reference already has `test_viewer_disconnects_release_the_hub` for the MJPEG analogue, so this test has a house pattern to match.

**Decision 3, one upstream.** Open three viewer sockets and assert the fake printer recorded exactly one upstream connection, and that a command sent by viewer two reaches the printer on that one connection. Then assert the idle timer does not fire while a `RequestID` is outstanding, by having the fake delay its reply past `idle_timeout` once.

**Decision 4, token scoping.** Mint a `RESOURCE_CAMERA` token and present it on the web route, asserting 403. Mint a web token for entry A and present it with entry B's path segment, asserting 403. Both mirror the reference's existing `test_stream_uses_the_signed_token`.

**Decision 4, cookie isolation.** The fake printer sets a session cookie on a request, and the test asserts the browser-facing response carries no `Set-Cookie`, that the proxy replays the cookie upstream on the next request, and that an entry reload clears the jar.

**Decision 5, streaming and caps.** Serve `/huge` at a size just over the cap and assert the body arrives complete and unrewritten with a diagnostic logged, rather than a 502. Serve the endless MJPEG through the proxy, read for two seconds, and assert the proxy's memory does not grow with bytes received, by asserting the response was written in chunks rather than buffered.

**Decision 5, no leaks.** Unload the config entry while a bridge and a stream are both live, and assert that every bridge is closed, the session is closed, and no upstream connection remains on the fake printer. The reference's `test_unload_stops_the_hub_and_the_views` is the template.

**Decision 5, breaker.** Point an entry at a closed port, issue twenty asset requests, and assert the fake recorded far fewer connection attempts than requests, proving the breaker rather than a per-request retry.

**Decision 6, illegal states.** Direct unit tests that the constructors refuse the states the types are supposed to forbid: a `LiteralPatch` with a short hash, a server-side `JS_LITERAL` rule with no patch, and a `RewriteProfile` whose orders are not unique and ascending. These are cheap and they are what makes the type claims real rather than aspirational.

### Live acceptance, not optional

One test cannot be faked and must run against the real printer at `192.168.128.143` through a real Home Assistant and a real browser. It loads the card, waits for the printer's own Angular app to render inside the iframe, asserts the print-status pane shows the live layer count, asserts the camera pane shows moving frames, navigates to a second route, reloads the browser on that deep link, and asserts the app still renders. It then closes the tab and asserts the printer's `NumberOfVideoStreamConnected` returns to its previous value.

That test is the only thing that proves decisions 1 and 2. Everything else can be proven against the loopback fake, and should be, because the real printer is one print job away from being unavailable.

### Tools to keep

The four probes I wrote are the reproducibility artifact for the claims that needed proof rather than reading. They should land in `tools/` beside `verify_sdcp.py`, which is this repository's existing acceptance instrument, and each one should print a pass or fail line rather than a transcript to interpret.

| Tool | Proves |
| --- | --- |
| `tools/probe_ha_view_methods.py` | that a `get` handler serves a WS upgrade and that `websocket` is not a registered method name |
| `tools/probe_handler_cancellation.py` | that `handler_cancellation=True` tears down both legs on abort and close with zero leaked tasks |
| `tools/probe_proxy_grammar.py` | that the port-in-path URL grammar routes, including across slashes on a second port |
| `tools/mine_printer_bundle.py` | already exists, and should be extended to report the webpack publicPath, the `new WebSocket` call site, and every `VideoUrl` reference |
