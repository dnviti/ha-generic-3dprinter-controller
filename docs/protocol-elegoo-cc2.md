# Elegoo Centauri Carbon 2, protocol surface

The Centauri Carbon 2 does not speak SDCP. This document separates what this
repository **measured** on a live printer from what it **takes from a source**, and
names the source every time. The adapter is `adapters/elegoo_cc2.py`; the read-only
acceptance check is `tools/acceptance_cc2.py`.

Sources, in order of authority:

1. [ELEGOO-3D/elegoo-link](https://github.com/ELEGOO-3D/elegoo-link), Elegoo's own
   C++ SDK, the library ElegooSlicer uses. Its LAN adapter for this printer lives in
   `src/lan/adapters/elegoo_fdm_cc2/`.
2. [danielcherubini/elegoo-homeassistant](https://github.com/danielcherubini/elegoo-homeassistant),
   whose `docs/CC2_PROTOCOL.md` and client were measured on firmware `02.01.00.00`.
3. [runnane/elegoo-web](https://github.com/runnane/elegoo-web), a web client that
   sends the methods the SDK leaves commented out.

## Device under test

| Property | Value |
| --- | --- |
| Model | Elegoo Centauri Carbon 2 |
| LAN address | `192.168.128.146` |
| Serial | `F01BXKSWL13QAZJ` |
| Firmware | `02.01.00.00` (`software_version.ota_version`), MCU `00.00.00.00` |
| Protocol version | `1.0.0` |
| Storage | 511 MB internal, 100 MB used |

Measured on 2026-09-25, first in cloud mode and then in LAN-only mode with an
access code set.

## Measured in LAN-only mode

`tools/acceptance_cc2.py` passed 13 of 13 read-only checks. With the owner's
approval, active checks through the real adapter then passed 21 of 23; the two that
did not are firmware behaviour, described below, not faults in the adapter.

**Ports.** `80` (upload), `1883` (MQTT), `8080` (camera) and `9001` (MQTT over
WebSocket) were open. `3030` stayed closed.

**Discovery** now answered `lan_status: 1` and `token_status: 1`: LAN-only mode,
access code set.

**Session.** The broker accepted user `elegoo` with the access code as password.
The registration was answered `{"client_id": ..., "error": "ok"}` at once on
`elegoo/<sn>/<client_id>_req/register_response`. Every `{"type":"PING"}` was answered
`{"type":"PONG"}` on the client's `api_response` topic. Answers to requests arrive on
that same topic, with the request's `id` and `method`.

**Status pushes** arrive on `api_status` as method 6000, one object per change, with
a sequence number that counted up by one (137, 138, 139, 140 in one capture). An idle
printer pushes little: a bed temperature moving by a degree was the only change in
twenty seconds. During homing they arrive several times a second.

**Field names.** This firmware sends `gcode_move` (with `extruder`, `speed`,
`speed_mode`, `x`, `y`, `z`) and `tool_head`, not the `gcode_move_inf` and
`toolhead` the community documentation gives; the adapter reads both. Fan `speed`
is a float from 0.0 to 255.0 with no `rpm`. `machine_status` also carries
`sub_status_reason_code`, and `print_status` carries `bed_mesh_detect`, `enable`,
`filament_detect` and `state`, which was empty while idle.

A full status of the idle printer, method 1002:

```json
{"external_device":{"camera":true,"type":"0303","u_disk":false},
 "extruder":{"filament_detect_enable":1,"filament_detected":0,"target":0,"temperature":33},
 "fans":{"aux_fan":{"speed":0.0},"box_fan":{"speed":0.0},"controller_fan":{"speed":0.0},"fan":{"speed":0.0},"heater_fan":{"speed":0.0}},
 "gcode_move":{"extruder":0.0,"speed":1500,"speed_mode":1,"x":5.0,"y":5.0,"z":0.1574},
 "heater_bed":{"target":0,"temperature":30},
 "led":{"status":1},
 "machine_status":{"exception_status":[],"progress":0,"status":1,"sub_status":0,"sub_status_reason_code":0},
 "print_status":{"bed_mesh_detect":false,"current_layer":0,"enable":false,"filament_detect":false,"filename":"","print_duration":0,"remaining_time_sec":0,"state":"","total_duration":0,"uuid":""},
 "tool_head":{"homed_axes":""},
 "ztemperature_sensor":{"measured_max_temperature":0,"measured_min_temperature":0,"temperature":27}}
```

### Methods, as the printer answered them

| Method | Sent | Answer |
| --- | --- | --- |
| 1001 attributes | `{}` | `hostname`, `ip`, `machine_model`, `protocol_version`, `sn`, `software_version` |
| 1002 full status | `{}` | the object above |
| 1026 home | `{"homed_axes": "xyz"}` | `error_code: 0` at once; the homing then took 44 s |
| 1027 move | `{"axes": "x", "distance": 10}` | `error_code: 0` at once; the head moved within a second |
| 1028 temperature | `{"extruder": 60}`, `{"heater_bed": 40}` | `error_code: 0`; the targets were reported and the nozzle warmed |
| 1029 light | `{"power": 0}`, `{"power": 1}` | `error_code: 0`; `led.status` followed |
| 1030 fan | `{"fan": 77}`, `{"aux_fan": 77}`, `{"box_fan": 77}`, then 0 | `error_code: 0`; each fan reported 77, which is 30 percent |
| 1031 speed mode | `{"mode": 0}` while idle | **`error_code: 1010`, not printing** |
| 1042 camera on | `{"enable": 1}` | `error_code: 0` and `url: http://<host>:8080/?action=stream` |
| 1044 file list | `{"storage_media": "local", "path": "/"}` | `file_list`, plus `offset` and `total` |
| 1044 file list | `{"storage_media": "u-disk", "path": "/"}` | `error_code: 1017` with no USB drive in |
| 1048 disk | `{"storage_media": "local"}` | `internal.total_bytes`, `internal.used_bytes` |

**Upload.** `PUT http://<host>:80/upload` with `X-Token` set to the access code
stored a 129-byte file, and the next file list showed it.

**The camera** served multipart JPEG on port 8080, 640 by 360.

### Firmware behaviour worth knowing

* **The speed mode can only be changed during a print.** An idle printer answers
  1031 with error 1010. The card's speed slider therefore reports the printer's
  refusal outside a print.
* **Homing heats the nozzle.** The printer's own homing goes through status 10
  with sub-status 2801, then 1045 (nozzle preheating, to about 140 °C, for its
  probe), then 1066, then 2802, and back to idle. Once it finished the nozzle was
  at 139 °C with its target at 0, cooling with the hotend and board fans at full
  duty. The card's power button warns about a hot nozzle for exactly this case.
* **After homing, Y parks at 264 mm, beyond the printable 256.** A move of +10 in Y
  from there was accepted and ended at 256: the firmware clamps a move to the
  printable area. The following −10 went from 256 to 246, and X and Z moved by
  exactly the distance asked.
* **Home and move are acknowledged at once**, not when the motion ends. The adapter
  waits a few seconds for a refusal and does not depend on either timing.

## Measured in cloud mode

With `lan_status: 0`, the broker accepted the connection and the subscriptions, and
then nothing answered: not the registration, not a request, and no status push
arrived. A wildcard subscription to `#` saw only the client's own messages. This
matches the SDK, which routes a printer in cloud mode through Elegoo's cloud and
never through the local broker. So the config flow refuses a printer in cloud mode,
and the adapter names the mode when a registration goes unanswered.

In cloud mode port `80` was closed. Port `8080` accepted connections but answered no
HTTP request, and after four probes stopped accepting connections until the printer
was power cycled.

## Sourced, not yet measured here

These need a print in progress, which was not part of the checks:

| Method | Name | Params | Source |
| --- | --- | --- | --- |
| 1020 | start print | `{"storage_media": "local", "filename": ..., "config": {"printer_check": true, "slot_map": []}}` | SDK, reduced to the fields measured by source 2 |
| 1021 | pause | `{}` | SDK |
| 1022 | stop | `{}` | SDK |
| 1023 | resume | `{}` | sources 2 and 3; acknowledged only once resumed, 122 s in source 2's measurement |
| 1031 | speed mode during a print | `{"mode": 0-3}`, 50, 100, 150 and 200 percent | SDK converter; sources 2 and 3 |

Deliberately not sent: 1047 (delete), because source 3 sends `{"file_path": [...]}`
while source 2's document gives `{"filename": ...}` and marks it untested.

### The CANVAS

The test printer's CANVAS methods have not been sent here yet. They come from the
community elegoo-web project, which recorded Elegoo's own page, and from the SDK for
2004 and 2005:

| Method | Does | Params |
| --- | --- | --- |
| 2005 | read the CANVAS | `{}`; answers `{"canvas_info": {...}}` in the shape the Centauri Carbon's `Cmd` 324 returns |
| 2001 | load a tray into the nozzle | `{"canvas_id": 0, "tray_id": 0-3}` |
| 2002 | unload a tray | `{"canvas_id": 0, "tray_id": 0-3}` |
| 2003 | record a tray's filament | `canvas_id`, `tray_id`, `brand`, `filament_type`, `filament_name`, `filament_code`, `filament_color`, `filament_min_temp`, `filament_max_temp` |
| 2004 | auto-refill | `{"auto_refill": true}` |

A status push may carry `canvas_info`. It is a delta, and a delta of the tray list
would replace the whole list, so the adapter reads 2005 again instead of merging
it. While the CANVAS loads or unloads, `machine_status.sub_status` runs through
1150 to 1158 and 1160 to 1166, which the adapter reports in words. Loading,
unloading and editing are refused unless the printer is idle, and a tray the
printer does not report is refused before it is sent, because source 2 found that a
request for a tray that does not exist is acknowledged and carried out on tray 0.

### Two findings from source 2 that shape the start of a print

* `printer_check` in the start-print config **persists**: a job that omits it
  inherits the previous job's value. The adapter always sends `true`.
* A `slot_map` entry that is partial or out of range is acknowledged with error 0
  and printed from tray 0. The adapter sends an empty map, which lets the printer
  choose.

### Upload constraints from source 2

A fresh connection per chunk is answered with HTTP 429 on the fourth chunk, and a
retried chunk corrupts the file, so the adapter sends 1 MiB ranged chunks over one
kept-alive connection and never retries. The single-chunk upload measured here does
not exercise that.

## Still to verify

Pause, resume and stop on a test print, a speed mode change during that print, a
multi-chunk upload, starting a print once its opt-in is enabled, and the CANVAS
methods 2001 to 2005.
