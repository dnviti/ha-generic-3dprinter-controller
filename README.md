# Generic 3D Printer Controller

One Home Assistant integration for a **mixed 3D printer fleet**. Each printer is a
config entry, each protocol is an adapter, and everything a user sees reads one
shared model, so the dashboard, the entities and the automations never learn which
protocol a printer speaks.

Covers **Elegoo SDCP** (Centauri Carbon and siblings), **Klipper via Moonraker**,
**OctoPrint**, **Duet / RepRapFirmware**, and any printer whose only interface is
its own embedded web page. Adding a protocol is one module plus one registry
entry.

Ships its own Lovelace card, `custom:generic-3dprinter-card`.

## Why this exists

A home lab accumulates printers that share nothing. Each vendor invents its own
LAN protocol, its own idea of a print job, its own way to say "72 percent done".
The usual outcome is one integration per protocol, each with its own entities and
its own card, and no single place to see the fleet.

This integration inverts that. Every protocol difference lives behind one adapter
interface, and the capability set is data, so a printer that cannot pause has no
pause button rather than a button that fails when pressed.

## What you get per printer

**Sensors.** Printer state, progress, current and total layer, time remaining,
time elapsed, file name, nozzle / bed / chamber temperature and target, fan duty,
speed and flow factor, and diagnostics for protocol, model, firmware, serial and
address.

**Controls.** Pause, resume, stop and home buttons; target temperatures, speed
factor, flow factor and fan duty as numbers; the chamber light as a switch. Each
one exists only when the printer says it supports it.

**Camera.** A camera entity fed by one shared upstream connection, so a dashboard
tile, a card and a notification do not each open their own. This matters on
hardware whose camera server keeps only a few connection slots and leaks them.

**The printer's own page.** For a printer with no control API, its embedded page is
reverse-proxied through Home Assistant and its control WebSocket is bridged, so a
dashboard served over HTTPS can embed a printer that only speaks HTTP on the LAN.

## Install

### HACS

1. In Home Assistant, open **HACS**, then the three-dot menu, then **Custom
   repositories**.
2. Add `https://github.com/dnviti/ha-generic-3dprinter-controller` with category
   **Integration**.
3. Search HACS for **Generic 3D Printer Controller** and install it.
4. Restart Home Assistant.

### Manually

Copy `custom_components/generic_3dprinter` into your Home Assistant
`config/custom_components/` directory and restart.

## Configure a printer

**Settings → Devices & Services → Add Integration → Generic 3D Printer
Controller.**

You can let it look for a printer on the network, or pick the protocol yourself.
Discovery is a convenience, never a guess: a probe that finds something fills in
the form and you still confirm it. Nothing in the config flow sends a command to a
printer.

| Protocol | Default port | Credential |
| --- | --- | --- |
| Elegoo SDCP (Centauri Carbon) | 3030, camera 3031 | none on the LAN |
| Klipper via Moonraker | 7125 | API key, if Moonraker requires one |
| OctoPrint | 5000 or 80 | API key |
| Duet (RepRapFirmware) | 80 | password, if the board has one |
| Web page only | 80 | username and password, if the page asks |

The camera port for SDCP defaults to 3031 and only needs changing if you moved it.

### The one dangerous setting

On Elegoo SDCP, **starting a print over the network** is off by default and is
asked as an explicit opt-in when you add the printer. Read the reason before you
turn it on.

Elegoo's SDCP start-print command carries a six-field payload that this
integration builds itself. On Centauri Carbon firmware, an unrecognised command
code, or a recognised code sent with an unexpected payload shape, has been
reported to crash the printer's `app` daemon. On that hardware `app` is the whole
host firmware including the motion stack, so a crash destroys a running print and
needs a power cycle at the wall.

Everything else the integration sends is a confirmed-working command, and the
adapter never probes an unknown code. You can enable or disable the opt-in later
in the integration's **Configure** dialog.

## The card

The integration registers the card automatically, so there is no Lovelace resource
to add. Put it on a dashboard:

```yaml
type: custom:generic-3dprinter-card
```

```yaml
type: custom:generic-3dprinter-card
title: Printers
fleet: true
```

| Option | Meaning |
| --- | --- |
| `entry_id` | Show one specific printer |
| `fleet` | Show every configured printer |
| `title` | Heading above the card |

With neither `entry_id` nor `fleet`, the card shows the first printer.

The card draws a control only when the printer reports the capability for it, so a
PrusaLink printer shows no start button and a printer with no camera shows no
camera pane.

## Automations

Every reading is a normal entity, so the usual patterns work.

```yaml
automation:
  - alias: Notify when the print finishes
    triggers:
      - trigger: state
        entity_id: sensor.centauri_carbon_state
        to: finished
    actions:
      - action: notify.mobile_app_phone
        data:
          message: >-
            {{ state_attr('sensor.centauri_carbon_state', 'friendly_name') }}
            finished {{ states('sensor.centauri_carbon_file') }}
```

```yaml
  - alias: Pause if the chamber gets too hot
    triggers:
      - trigger: numeric_state
        entity_id: sensor.centauri_carbon_chamber_temperature
        above: 45
    actions:
      - action: button.press
        target:
          entity_id: button.centauri_carbon_pause
```

## Troubleshooting

**The printer is unreachable.** Confirm the address and that Home Assistant can
reach the printer's network. The integration reports the printer as offline rather
than showing stale numbers.

**The camera is black or shows the last frame.** These cameras serve a very small
number of connections. The integration holds one upstream connection and fans it
out, but a second client talking to the printer directly can still take the last
slot. Close the printer's own web page and any slicer watching it.

**Elegoo SDCP will not connect.** The printer allows five simultaneous SDCP
clients. If a slicer and a browser already hold them, the printer refuses the
handshake with HTTP 500 and the integration says so explicitly.

**Status stops updating while the printer is idle.** On some SDCP firmware the
push scheduler wedges while idle. The adapter requests status explicitly instead of
waiting for a push, so this recovers on the next poll.

**Entity names look generic.** Confirm `translations/en.json` shipped with the
component. Entity names come from there, not from `strings.json`.

## Compatibility

* Klipper, OctoPrint and Duet adapters are built from the documented API surface of
  those protocols. No printer of those kinds was available while this was written,
  so treat them as claims to verify on your hardware. Each adapter's registration
  carries an `evidence` mapping that says what is verified and what is inferred,
  and the integration's diagnostics report it.
* The Elegoo SDCP adapter was developed against a live Centauri Carbon on firmware
  `V1.4.49` and verified end to end.
* The Elegoo **Centauri Carbon 2** is a different machine on a different protocol
  (MQTT with an access code) and is not supported yet.

## Documentation

| Document | Contents |
| --- | --- |
| `docs/architecture.md` | Why the integration is shaped this way |
| `docs/protocol-elegoo-sdcp-verified.md` | Every SDCP fact observed on real hardware |
| `docs/protocol-adapter-layer-design.md` | The adapter interface and its types |
| `docs/web-proxy-transport-design.md` | The reverse proxy and socket bridge |
| `docs/research/` | The cited protocol research, with its open questions marked |
| `docs/protocol-research-matrix.md` | The multi-protocol capability matrix |

## Development

```bash
python -m pytest tests/ -q --ignore=tests/test_integration_setup.py
python -m pytest tests/test_integration_setup.py -q
npm test
```

The fast suite runs in about fifteen seconds. The integration test loads a real
Home Assistant and takes about two minutes because the test double's socket keeps
Home Assistant's shutdown waiting; that cost is documented in the file.

`tools/` holds the instruments used to reverse the SDCP protocol, each one
runnable against real hardware:

| Tool | Purpose |
| --- | --- |
| `tools/acceptance_sdcp.py` | Drive the real adapter against a real printer, 21 checks |
| `tools/probe_sdcp.py` | Dump every raw SDCP frame a printer sends |
| `tools/dump_status.py` | Print the status, attributes and file-list schemas |
| `tools/mine_printer_bundle.py` | Walk a printer's own JS bundle for its API surface |
| `tools/extract_printer_commands.py` | Dump the command table from that bundle |
| `tools/sync_translations.py` | Regenerate `translations/en.json` from `strings.json` |

Run `python tools/sync_translations.py` after editing `strings.json`, or the guard
test will fail.

## Licence

MIT. See `LICENSE`.
