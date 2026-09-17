# Changelog

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
