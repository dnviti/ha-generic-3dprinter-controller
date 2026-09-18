# Changelog

## 0.3.0

**Fixed**

* **A printer that was switched off and back on reconnects on its own.** It did
  not before: the entry had to be reloaded by hand. Reading a printer is now what
  reconnects to it. Before, a read only consulted the last status the adapter had
  cached and reported "offline" without ever trying to reach the printer again, so
  the connection was never re-established however long the printer had been back.
* The adapter treated a socket object that was present but closed as a live
  connection, which made its own reconnect a silent no-op even when something did
  call it.
* An unanswered request now drops the socket instead of leaving it in place, so the
  next attempt reconnects rather than sending into a dead connection for ever.
* A poll that cannot reach the printer returns an offline snapshot instead of
  raising. A printer that is switched off is an expected state, not an error, and
  raising left the entities unavailable with no reading of why.

**Added**

* Five reconnect tests that power the printer off and on again against the real
  coordinator and adapter, including a long outage, a failed command while the
  printer is off, and a check that recovery clears the stale error rather than only
  flipping the connected flag.

**Notes**

* Verified on a live Elegoo Centauri Carbon: 21 of 21 adapter checks and 39
  distinct camera frames in 4 seconds, about 10 frames per second.

## 0.2.0

**Fixed**

* **The camera is live.** It was not before. The card loaded a still snapshot, which
  only changed when the card polled, so a live camera looked like a slideshow that
  updated every few seconds. The card now loads the integration's MJPEG stream and
  the picture moves.
* **The camera stream never worked at all**, which is why the snapshot was the only
  thing that appeared. The stream view handed the frame iterator to `async with`,
  and an async generator is not a context manager, so the response closed empty on
  the first line. A regression test now drives the real camera view over HTTP and
  asserts that distinct frames keep arriving.
* The camera's idle timer is cancelled on unload instead of being left sleeping on
  the event loop after the printer has gone.

**Added**

* `tools/acceptance_camera.py`, which measures a printer's camera against real
  hardware and reports the frame rate and how many frames were distinct.

**Notes**

* Verified on a live Elegoo Centauri Carbon: 49 distinct frames in 6 seconds, about
  8 frames per second, one upstream connection for the whole run.

## 0.1.0

First release.

**Added**

* Multi-protocol adapter layer. One config entry per printer, one adapter per
  protocol, one shared snapshot, and no protocol name anywhere outside
  `adapters/`.
* Elegoo SDCP adapter, developed against a live Centauri Carbon on firmware
  `V1.4.49`. Status, attributes, file list, pause, resume, stop, temperatures,
  fan duty, print speed, chamber light, and the MJPEG camera.
* Klipper via Moonraker adapter, with a WebSocket status subscription.
* OctoPrint adapter.
* Duet / RepRapFirmware adapter, including the Digest round trip and the session
  key, `M25` before `M0` for a cancel, and `G91`/`G1`/`G90` for a relative jog.
* Web-only adapter for a printer whose only interface is its own page.
* Reverse proxy for a printer's embedded web UI, with the upstream port as a path
  segment, signed path-segment tokens, response-header scrubbing, and HTML and CSS
  rewriting.
* WebSocket bridge for a proxied printer's control socket.
* Camera support with one shared upstream connection per printer, fanned out to
  every viewer. This is a requirement on hardware whose camera server keeps only a
  few connection slots and leaks them.
* Entity platforms for sensors, binary sensors, buttons, numbers, switches and
  cameras. Every entity exists only when the printer grants the capability it
  needs.
* `custom:generic-3dprinter-card`, a Lovelace card for one printer or a whole
  fleet. It draws a control per capability the backend reports.
* A per-printer opt-in for commands that are known to be risky on specific
  firmware, declared as data by the adapter rather than as a branch in the
  integration. On Elegoo SDCP, starting a print over the network is off by default.
* Diagnostics, with every credential key redacted.
* Tools for working on the SDCP protocol against real hardware: an acceptance
  check that drives the real adapter, a raw frame probe, a status schema dumper,
  and two bundle miners.

**Known gaps**

* PrusaLink is registered but not implemented, so it is not offered in the config
  flow. Its registration and capability set are in place for the adapter to fill
  in.
* The Klipper, OctoPrint and Duet adapters come from the documented API surface of
  those protocols. No printer of those kinds was available during development, so
  each registration records what is inferred rather than verified.
* Bambu Lab, Creality and Anycubic are researched but not implemented. Their
  transports need bespoke binary clients.
* The Elegoo Centauri Carbon 2 uses a different protocol and is not supported.

**Notes**

* Entity names come from `translations/en.json`. Run
  `tools/sync_translations.py` after editing `strings.json`.
