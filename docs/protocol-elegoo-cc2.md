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
| Protocol version | `1.0.0` |
| Network mode | cloud (`lan_status: 0`) |
| Access code | none set (`token_status: 0`) |

## Measured

**Open ports.** A full TCP scan found exactly three: `1883` (MQTT), `8080`
(camera), `9001` (MQTT over WebSocket). Port `80`, which the sources give as the
upload endpoint on stock firmware, was closed in cloud mode, as were `3030` and
`3031`, SDCP's ports.

**Discovery.** `{"id": 0, "method": 7000}` sent to UDP `52700` was answered with:

```json
{"id":0,"result":{"host_name":"CC2 QAZJ","lan_status":0,"machine_model":"Centauri Carbon 2","protocol_version":"1.0.0","sn":"F01BXKSWL13QAZJ","token_status":0}}
```

The SDCP discovery literal `M99999` on UDP `3000` got no answer.

**The broker.** A CONNECT with user `elegoo` and password `123456` was accepted
(CONNACK `0`), and SUBSCRIBE to the three topics below was granted at QoS 0. A
wildcard subscription to `#` saw only this client's own messages.

**Cloud mode.** With the printer in cloud mode, nothing answered: not the
registration, not the heartbeat, not methods 1001 or 1002, and no status push
arrived. That matches the SDK, which routes a printer whose discovery reply says
`lan_status: 0` through Elegoo's cloud and never through the local broker. So the
config flow refuses a printer in cloud mode, and the adapter names the mode when a
registration goes unanswered.

**The camera port.** Port `8080` first accepted connections and answered no HTTP
request within five seconds. After four such probes it stopped accepting
connections altogether, and had not recovered some forty minutes later, while
discovery and the broker kept answering. The sources record that the camera allows
a single viewer; whether the probes exhausted its slots or the firmware stops the
server in cloud mode is not known. A power cycle is the expected remedy.

## Sourced, not yet measured here

### Session

| Step | Topic | Payload | Source |
| --- | --- | --- | --- |
| Register | publish `elegoo/<sn>/api_register` | `{"client_id": "1_PC_1234", "request_id": "1_PC_1234_req"}` | SDK |
| Registration answer | `elegoo/<sn>/<request_id>/register_response` | `{"client_id": ..., "error": "ok"}` or `"too many clients"` | SDK |
| Requests | publish `elegoo/<sn>/<client_id>/api_request` | `{"id": n, "method": m, "params": {...}}` | SDK |
| Answers | `elegoo/<sn>/<client_id>/api_response` | `{"id": n, "method": m, "result": {"error_code": 0, ...}}` | SDK |
| Status | `elegoo/<sn>/api_status` | method 6000, a delta against the last full status | SDK |
| Heartbeat | the request topic, every 10 s | `{"type": "PING"}`, answered `{"type": "PONG"}` | SDK |

The client id has the SDK's form, `1_PC_` and four digits. The printer drops a
client that is silent for 65 seconds, and the adapter treats a printer silent for
as long as gone. Deltas carry a sequence number; five gaps in a row trigger a full
read, as in the SDK, and a full read is also made every five minutes.

### Methods the adapter sends

| Method | Name | Params | Source |
| --- | --- | --- | --- |
| 1001 | attributes | `{}` | SDK |
| 1002 | full status | `{}` | SDK |
| 1020 | start print | `{"storage_media": "local", "filename": ..., "config": {"printer_check": true, "slot_map": []}}` | SDK, reduced to the fields measured by source 2 |
| 1021 | pause | `{}` | SDK |
| 1022 | stop | `{}` | SDK |
| 1023 | resume | `{}` | sources 2 and 3; acknowledged only once resumed, 122 s in source 2's measurement |
| 1026 | home | `{"homed_axes": "xyz"}` | built by the SDK's request converter, whose method table leaves it commented out; sent by source 3 |
| 1027 | move | `{"axes": "z", "distance": 10.0}` | as 1026 |
| 1028 | set temperature | `{"extruder": 215}` or `{"heater_bed": 60}` | SDK converter; sources 2 and 3 |
| 1029 | light | `{"power": 1}` | Elegoo's own web page, per source 2; source 3 |
| 1030 | fan | `{"fan": 0-255}`, `{"aux_fan": ...}`, `{"box_fan": ...}` | SDK converter; sources 2 and 3 |
| 1031 | speed mode | `{"mode": 0-3}`, 50, 100, 150 and 200 percent | SDK converter; sources 2 and 3 |
| 1042 | camera on | `{"enable": 1}` | sources 2 and 3 |
| 1044 | file list | `{"storage_media": "local", "path": "/"}` | source 2, the parameters ElegooSlicer sends |

Deliberately not sent: 1047 (delete), because source 3 sends
`{"file_path": [...]}` while source 2's document gives `{"filename": ...}` and marks
it untested. A method the printer does not know is answered with error 1001; unlike
the first Centauri Carbon, no source reports one crashing the printer.

### Two findings from source 2 that shape the adapter

* `printer_check` in the start-print config **persists**: a job that omits it
  inherits the previous job's value. The adapter always sends `true`.
* A `slot_map` entry that is partial or out of range is acknowledged with error 0
  and printed from tray 0. The adapter sends an empty map, which lets the printer
  choose.

### Upload

`PUT http://<host>:80/upload` in 1 MiB chunks, with `Content-Range`, `X-File-Name`,
`X-File-MD5` of the whole file and `X-Token` (the access code, or `123456`). The
answer is `{"error_code": 0, ...}`. Source 2 measured that a fresh connection per
chunk is answered with HTTP 429 on the fourth chunk and that a retried chunk
corrupts the file, so the adapter keeps one connection and never retries.

### Status fields read

`machine_status.status` and `sub_status`, `print_status` (file name, uuid, layers,
durations, progress), `extruder`, `heater_bed`, `ztemperature_sensor` (the chamber,
a reading only), `fans.*.speed` (PWM 0 to 255), `gcode_move_inf` (position and speed
mode), `toolhead.homed_axes`, `led.status`, `external_device.camera`. Older
firmware names are accepted too: `gcode_move`, `tool_head`, `chamber`.

## To verify once LAN-only mode is on

Run `python tools/acceptance_cc2.py <host>` (read-only), then check by hand, one at
a time: pause and resume on a test print, a nozzle target, a fan, the light, a jog
of 1 mm and a home with the bed clear, an upload, and the camera. Record each
result here and move the method from "sourced" to "measured".
