# Elegoo Centauri Carbon network API surface

Research report for the Home Assistant integration work. Read-only investigation. Every
non-obvious claim carries the URL that was actually fetched.

## Naming used in this report

Two different printers share the "Centauri Carbon" name. They do **not** share a protocol.

| Name | Meaning | Transport |
|---|---|---|
| **CC1** | Original Centauri Carbon | SDCP v3 JSON over WebSocket, port 3030 |
| **CC2** | Centauri Carbon 2 | JSON-RPC over MQTT, port 1883 (TCP) and 9001 (WS) |

The CC1 is the printer that speaks the SDCP API the Mars 5 / Saturn 4 Ultra use. The CC2
does not. Sources: [pycentauri README](https://raw.githubusercontent.com/bjan/pycentauri/main/README.md),
[pycentauri PROTOCOL.md](https://raw.githubusercontent.com/bjan/pycentauri/main/docs/PROTOCOL.md).

---

## 1. Primary control protocol

**Answer. Yes.** The CC1 exposes SDCP v3 JSON over WebSocket. The Mars 5 and Saturn 4 Ultra
speak the same protocol, which is why one Home Assistant integration covers both
([elegoo-homeassistant README](https://raw.githubusercontent.com/danielcherubini/elegoo-homeassistant/main/README.md)).

### 1.1 Ports and paths

| Port | Protocol | Purpose |
|---|---|---|
| 80 | HTTP | Angular web UI, plus the multipart file upload endpoint |
| 3000 | UDP | Discovery, broadcast the literal ASCII string `M99999` |
| 3030 | WS / HTTP | SDCP control socket at `/websocket` |
| 3031 | HTTP | MJPEG camera at `/video` |

Evidence. [pycentauri PROTOCOL.md](https://raw.githubusercontent.com/bjan/pycentauri/main/docs/PROTOCOL.md)
publishes the port table. The Home Assistant integration hard-codes the same values in
[`const.py`](https://raw.githubusercontent.com/danielcherubini/elegoo-homeassistant/main/custom_components/elegoo_printer/const.py):

```python
DISCOVERY_MESSAGE = "M99999"
DISCOVERY_PORT = 3000
VIDEO_PORT = 3031
WEBSOCKET_PORT = 3030
```

The official SDK builds the URL as `ws://<host>:3030/websocket`, and switches to
`wss://<host>:3030/websocket` when given an `https` host
([`elegoo_fdm_cc_protocol.cpp`](https://raw.githubusercontent.com/ELEGOO-3D/elegoo-link/main/src/lan/adapters/elegoo_fdm_cc/elegoo_fdm_cc_protocol.cpp)):

```cpp
if (urlInfo.scheme == "https") {
    std::string connectionUrl = "wss://" + urlInfo.host + ":3030" + "/websocket";
    return connectionUrl;
}
std::string connectionUrl = "ws://" + urlInfo.host + ":3030" + "/websocket";
```

The upstream SDCP v3.0.0 spec agrees on both path and port
([CBD Technology spec](https://raw.githubusercontent.com/cbd-tech/SDCP-Smart-Device-Control-Protocol-V3.0.0/main/SDCP(Smart%20Device%20Control%20Protocol)_V3.0.0_EN.md)).

### 1.2 Request envelope

The SDCP v3.0.0 spec defines the envelope
([spec](https://raw.githubusercontent.com/cbd-tech/SDCP-Smart-Device-Control-Protocol-V3.0.0/main/SDCP(Smart%20Device%20Control%20Protocol)_V3.0.0_EN.md)):

```json
{
  "Id": "uuid-string",
  "Data": {
    "Cmd": 0,
    "Data": {},
    "RequestID": "uuid-string",
    "MainboardID": "string",
    "TimeStamp": 1687069655,
    "From": 0
  },
  "Topic": "sdcp/request/{MainboardID}"
}
```

The printer answers on `sdcp/response/{MainboardID}` with `Data.Data.Ack`. Push traffic
arrives on `sdcp/status/{MainboardID}` and `sdcp/attributes/{MainboardID}`. Error and
notice topics are `sdcp/error/{MainboardID}` and `sdcp/notice/{MainboardID}`.

Field-by-field notes.

| Field | Meaning | Evidence |
|---|---|---|
| `Id` | Envelope id. The SDK sets it to the mainboard id. pycentauri and the official SDK both send something here; the printer does not appear to validate it. | [`createStandardBody()`](https://raw.githubusercontent.com/ELEGOO-3D/elegoo-link/main/src/lan/adapters/elegoo_fdm_cc/elegoo_fdm_cc_message_adapter.cpp), [`sdcp.py`](https://raw.githubusercontent.com/bjan/pycentauri/main/src/pycentauri/sdcp.py) |
| `Data.Cmd` | Numeric command code. | spec |
| `Data.Data` | Command payload, `{}` for commands with no parameters. | spec |
| `Data.RequestID` | Client-generated correlation id. The response echoes it. | spec |
| `Data.MainboardID` | Device identity. Mandatory on every command. | spec |
| `Data.TimeStamp` | Time of send. Seconds in the spec, milliseconds in the Elegoo SDK. See section 7. | spec, [`elegoo_fdm_cc_message_adapter.cpp`](https://raw.githubusercontent.com/ELEGOO-3D/elegoo-link/main/src/lan/adapters/elegoo_fdm_cc/elegoo_fdm_cc_message_adapter.cpp) |
| `Data.From` | Command source enum. `0` = PC, `1` = PC via web, `2` = web, `3` = app, `4` = server. | spec |
| `Topic` | `sdcp/request/<MainboardID>` | spec |

`From` is a genuine ambiguity, not a settled fact. The spec's own examples use `0`. The
official Elegoo SDK sends `1`
([`elegoo_fdm_cc_message_adapter.cpp`](https://raw.githubusercontent.com/ELEGOO-3D/elegoo-link/main/src/lan/adapters/elegoo_fdm_cc/elegoo_fdm_cc_message_adapter.cpp),
`printerMessage["From"] = 1;`). pycentauri sends `1` to match the official client. The
Home Assistant integration sends neither explicitly in the frames read for this report.
`0` is the safe default and is what the protocol spec documents.

### 1.3 Command codes

The authoritative table is the SDK's `COMMAND_MAPPING_TABLE`
([`elegoo_fdm_cc_message_adapter.cpp`](https://raw.githubusercontent.com/ELEGOO-3D/elegoo-link/main/src/lan/adapters/elegoo_fdm_cc/elegoo_fdm_cc_message_adapter.cpp)):

```cpp
const std::vector<std::pair<MethodType, int>> ElegooFdmCCMessageAdapter::COMMAND_MAPPING_TABLE = {
    {MethodType::GET_PRINTER_ATTRIBUTES, 1},
    {MethodType::GET_PRINTER_STATUS, 0},
    {MethodType::START_PRINT, 128},
    {MethodType::PAUSE_PRINT, 129},
    {MethodType::STOP_PRINT, 130},
    {MethodType::RESUME_PRINT, 131},
    {MethodType::UPDATE_PRINTER_NAME, -1},
    // {MethodType::HOME_AXES, 402},
    // {MethodType::MOVE_AXES, 401},
    // {MethodType::SET_TEMPERATURE, 403},
    // {MethodType::SET_LIGHT, 403},
    // {MethodType::SET_FAN_SPEED, 403},
    // {MethodType::SET_PRINT_SPEED, 403},
    // {MethodType::PRINT_TASK_LIST, 320},
    // {MethodType::PRINT_TASK_DETAIL, 321},
    // {MethodType::DELETE_PRINT_TASK, 322},
    // {MethodType::GET_FILE_LIST, 258},
    // {MethodType::GET_FILE_DETAIL, 258},
    // {MethodType::DELETE_FILE, 259},
    // {MethodType::GET_DISK_INFO, 1048},
    // {MethodType::VIDEO_STREAM, 386},
    // {MethodType::SET_PRINTER_NAME, 1043},
    // {MethodType::LOAD_FILAMENT, 1024},
    // {MethodType::UNLOAD_FILAMENT, 1025},
    // {MethodType::EXPORT_TIMELAPSE_VIDEO, 323},
    {MethodType::GET_CANVAS_STATUS, 324}};
```

The commented-out lines are the important part of that quote. They are mapped in the SDK's
dispatch code but disabled in the lookup table, which is why community work had to
re-derive them from the printer's own web UI.

Merged table, with the confirmation status of each row.

| Cmd | Name | Payload | Status |
|---|---|---|---|
| 0 | `GET_PRINTER_STATUS` | `{}` | Confirmed. In the SDK table. |
| 1 | `GET_PRINTER_ATTRIBUTES` | `{}` | Confirmed. In the SDK table. |
| 128 | `START_PRINT` | see below | Confirmed in the SDK table. **Dangerous on CC1.** See section 6. |
| 129 | `PAUSE_PRINT` | `{}` | Confirmed. In the SDK table. |
| 130 | `STOP_PRINT` | `{}` | Confirmed. In the SDK table. |
| 131 | `RESUME_PRINT` / "Continue Print" | `{}` | Confirmed. In the SDK table and the spec. |
| 132 | `STOP_MATERIAL_FEEDING` | `{}` | Spec + HA integration. |
| 133 | `SKIP_PREHEATING` | `{}` | Spec + HA integration. |
| 192 | `CHANGE_PRINTER_NAME` | `{"Name": "..."}` | Spec + HA integration. |
| 255 | `TERMINATE_FILE_TRANSFER` | `{"Uuid": ..., "FileName": ...}` | Spec + HA integration. |
| 257 | `RENAME_FILE` | CC2 only per HA integration | HA integration only. |
| 258 | `GET_FILE_LIST` | `{"Url": "/local"}` or `{"/usb/..."}` | Confirmed live by pycentauri. Commented out in the SDK. |
| 259 | `DELETE_FILE_LIST` | `{"FileList": ["/local/a.gcode"], "FolderList": [...]}` | Confirmed live by pycentauri. Commented out in the SDK. |
| 260 | `GET_FILE_INFO` | CC2 only per HA integration | HA integration only. |
| 320 | `GET_PRINT_HISTORY` | `{}` | Confirmed live by pycentauri. Commented out in the SDK. |
| 321 | `GET_PRINT_HISTORY_DETAIL` | `{"Id": ["<uuid>", ...]}` | Confirmed live by pycentauri. Commented out in the SDK. |
| 322 | `DELETE_HISTORY` | CC2 only per HA integration | HA integration only. |
| 323 | `EXPORT_TIMELAPSE_VIDEO` | unprobed | Commented out in the SDK. |
| 324 | `GET_CANVAS_STATUS` | `{}` | In the SDK table. pycentauri refuses to send it on CC1 because it is unprobed and unknown commands can crash the daemon. |
| 386 | `SET_VIDEO_STREAM` | `{"Enable": 0\|1}` | Spec + OpenCentauri docs + HA integration. Commented out in the SDK. |
| 387 | `SET_TIME_LAPSE_PHOTOGRAPHY` | `{"Enable": 0\|1}` | Spec + HA integration. |
| 401 | `MOVE_AXES` | `{"Axis": "X", "Step": <mm>}` | Commented out in the SDK. HA integration uses it. |
| 402 | `HOME_AXES` | `{"Axis": "X\|Y\|Z\|XYZ"}` | Commented out in the SDK. HA integration uses it. |
| 403 | `CHANGE_PRINT_PARAMS` / `CONTROL_DEVICE` | overloaded, see below | Confirmed live by pycentauri. Commented out in the SDK. |
| 512 | `SUBSCRIBE` | `{"TimePeriod": <ms>}` | Documented by CentauriLink and OctoEverywhere per pycentauri's docstring; used by pycentauri and HA. |
| 64 | `DISCONNECT` | n/a | HA integration only. |

Cmd 403 is polymorphic. The firmware dispatches on which keys are present
([pycentauri `sdcp.py`](https://raw.githubusercontent.com/bjan/pycentauri/main/src/pycentauri/sdcp.py),
[pycentauri PROTOCOL.md](https://raw.githubusercontent.com/bjan/pycentauri/main/docs/PROTOCOL.md)):

```python
# Cmd 403 is overloaded — the payload shape dispatches:
# {"PrintSpeedPct": N}                            → set print speed
# {"TargetFanSpeed": {"ModelFan":..., "BoxFan":..., "AuxiliaryFan":...}} → set fan speeds
# {"TempTargetNozzle": N, "TempTargetHotbed": N, "TempTargetBox": N}    → set heater targets
# {"LightStatus": {"SecondLight": 0|1}}           → chamber light
CHANGE_PRINT_PARAMS = 403
```

Fan payload confirmed in the official SDK's `SET_FAN_SPEED` branch, which writes
`param["TargetFanSpeed"]["ModelFan"]`, `["BoxFan"]`, `["AuxiliaryFan"]`
([`elegoo_fdm_cc_message_adapter.cpp`](https://raw.githubusercontent.com/ELEGOO-3D/elegoo-link/main/src/lan/adapters/elegoo_fdm_cc/elegoo_fdm_cc_message_adapter.cpp)).
Temperature payload confirmed in the same file's `SET_TEMPERATURE` branch, which writes
`TempTargetHotbed`, `TempTargetNozzle`, `TempTargetBox`.

`START_PRINT` payload, from the SDK's builder
([same file](https://raw.githubusercontent.com/ELEGOO-3D/elegoo-link/main/src/lan/adapters/elegoo_fdm_cc/elegoo_fdm_cc_message_adapter.cpp)):

```json
{
  "Filename": "model.gcode",
  "StartLayer": 0,
  "Calibration_switch": 1,
  "PrintPlatformType": 0,
  "Tlp_Switch": 0,
  "slot_map": []
}
```

`Calibration_switch` is auto bed leveling, `Tlp_Switch` is time lapse,
`PrintPlatformType` is the bed type.

**Correction to a common assumption.** The research brief asked whether 131 is "set print
parameters" and 128 is "start print". It is the other way for the parameter command. 131 is
resume (labelled "Continue Print" by OpenCentauri, `RESUME_PRINT` by the SDK). Print
parameters ride on 403.

### 1.4 Response topics

| Topic | Direction | Meaning |
|---|---|---|
| `sdcp/response/<id>` | printer to client | Direct reply to a command, correlated by `RequestID`. Carries `Data.Data.Ack`. |
| `sdcp/status/<id>` | printer to client | Status frame. Arrives as a one-shot answer to Cmd 0 and as a periodic push after Cmd 512. |
| `sdcp/attributes/<id>` | printer to client | Machine attributes. One spontaneous push on connect while idle or printing. |
| `sdcp/error/<id>` | printer to client | Proactive error, `Data.Data.ErrorCode`. |
| `sdcp/notice/<id>` | printer to client | Notification, `Data.Data.Message` and `Type`. Type 1 is history sync complete. |

The brief's guess of "0 for attributes, 1 for file list, 2 for print status" does not match
any fetched source. Topics are string-routed. The numeric codes that *do* exist are these.

`Ack` codes for print control, from the spec's `sdcp_print_ctrl_ack_t`
([spec](https://raw.githubusercontent.com/cbd-tech/SDCP-Smart-Device-Control-Protocol-V3.0.0/main/SDCP(Smart%20Device%20Control%20Protocol)_V3.0.0_EN.md)):

```
0 = OK
1 = Busy
2 = File not found
3 = MD5 verification failed
4 = File read failed
5 = Resolution mismatch
6 = Unrecognized file format
7 = Machine model mismatch
```

The SDK carries the same enum in a comment, with a duplicated value 5 for both
`INVLAID_RESOLUTION` and `UNKNOW_FORMAT`
([`elegoo_fdm_cc_message_adapter.cpp`](https://raw.githubusercontent.com/ELEGOO-3D/elegoo-link/main/src/lan/adapters/elegoo_fdm_cc/elegoo_fdm_cc_message_adapter.cpp)).
Prefer the spec's numbering, which assigns 6 to the format case.

`Ack` codes for video stream enable, Cmd 386
([OpenCentauri API docs](https://raw.githubusercontent.com/suchmememanyskill/OpenCentauri/main/docs/software/api.md)):

```
0 = success
1 = exceeded maximum simultaneous streaming limit
2 = camera does not exist
3 = unknown error
```

`Ack` codes for Cmd 255, terminate file transfer, from the spec's `sdcp_file_transfer_ack_t`:

```
0 = success
1 = printer is not currently transferring files
2 = printer is already in the verification phase
3 = file not found
```

### 1.5 Status object

Top-level `Status` scalars and objects, confirmed by the SDK's `handlePrinterStatus`, the
OpenCentauri docs, and pycentauri's models.

| Field | Type | Notes |
|---|---|---|
| `CurrentStatus` | array of int | Machine state. Array, usually one element. pycentauri reads index 0. |
| `PreviousStatus` | int | Machine state enum. |
| `TempOfNozzle` | number, or `[target, actual]` on older firmware | Nozzle current temperature. |
| `TempTargetNozzle` | number | Nozzle target. Absent on the old pair-form firmware. |
| `TempOfHotbed` | number, or pair | Bed current. |
| `TempTargetHotbed` | number | Bed target. |
| `TempOfBox` | number, or pair | Chamber current. |
| `TempTargetBox` | number | Chamber target. |
| `CurrenCoord` | string `"x,y,z"` | Note the firmware typo. Handle both spellings. |
| `ZOffset` | number | Z offset. |
| `CurrentFanSpeed` | object | `{"ModelFan": pct, "AuxiliaryFan": pct, "BoxFan": pct}`. Also seen: `ModeFan`. |
| `LightStatus` | object | `{"SecondLight": 0\|1}`. `RgbLight` is an array on some firmware. |
| `TimeLapseStatus` | int | 0 off, 1 on. |
| `PlatFormType` | int | Platform type. Firmware capitalisation is inconsistent. |
| `PrintInfo` | object | See below. |

`PrintInfo` fields, confirmed by the SDK's parser
([`elegoo_fdm_cc_message_adapter.cpp`](https://raw.githubusercontent.com/ELEGOO-3D/elegoo-link/main/src/lan/adapters/elegoo_fdm_cc/elegoo_fdm_cc_message_adapter.cpp))
and by pycentauri's `PrintInfo` model
([`models.py`](https://raw.githubusercontent.com/bjan/pycentauri/main/src/pycentauri/models.py)):

| Field | Notes |
|---|---|
| `Status` | Print sub-state. Full enum below. |
| `CurrentLayer` | Layer counter. |
| `TotalLayer` | Layer total. |
| `CurrentTicks` | Elapsed time. Unit is ambiguous, see section 7. |
| `TotalTicks` | Estimated total time, same unit as `CurrentTicks`. |
| `Progress` | Percent. |
| `Filename` | File name, sometimes a storage path such as `/local/x.gcode`. |
| `TaskId` | Job UUID. |
| `PrintSpeedPct` | Print speed percent. Also seen as `PrintSpeed` in OpenCentauri's sample. |
| `ErrorNumber` | Print error enum, see below. |

Remaining time is computed client side on CC1 as `TotalTicks - CurrentTicks`
([SDK parser](https://raw.githubusercontent.com/ELEGOO-3D/elegoo-link/main/src/lan/adapters/elegoo_fdm_cc/elegoo_fdm_cc_message_adapter.cpp),
`printerStatusData.printStatus.estimatedTime = totalTime - currentTime;`). There is no
`RemainTime` field on CC1.

`ZAxisPosition` as a named field was not found in any fetched source. Z position comes from
the third component of `CurrenCoord`. Treat a literal `ZAxisPosition` field as UNCONFIRMED.
`PrintSpeed` exists only as `PrintInfo.PrintSpeedPct` on current firmware.

`CurrentStatus` machine state, from the SDK's `sdcp_machine_status_t`
([same file](https://raw.githubusercontent.com/ELEGOO-3D/elegoo-link/main/src/lan/adapters/elegoo_fdm_cc/elegoo_fdm_cc_message_adapter.cpp)):

```
0  IDLE
1  PRINTING
2  FILE_TRANSFERRING
3  EXPOSURE_TESTING (resin)
4  PRINTERS_TESTING (self check)
5  AUTO_LEVEL
6  RESONANCE_TESTING
7  OTHERS_BUSY
8  FILE_CHECKING
9  HOMING
10 FEED_OUT (filament unload)
11 PID_DETECT
```

The upstream resin-era spec only defined 0 through 4, so the 5 to 11 values are FDM
additions from the Elegoo SDK.

`PrintInfo.Status`, from the SDK's `sdcp_print_status_t`
([same file](https://raw.githubusercontent.com/ELEGOO-3D/elegoo-link/main/src/lan/adapters/elegoo_fdm_cc/elegoo_fdm_cc_message_adapter.cpp)):

```
0  IDLE                14 ERROR
1  HOMING              15 AUTO_LEVELING
2  DROPPING (resin)    16 PREHEATING
3  EXPOSURING (resin)  17 RESONANCE_TESTING
4  LIFTING (resin)     18 PRINT_START
5  PAUSING             19 AUTO_LEVELING_COMPLETED
6  PAUSED              20 PREHEATING_COMPLETED
7  STOPPING            21 HOMING_COMPLETED
8  STOPPED             22 RESONANCE_TESTING_COMPLETED
9  COMPLETE            23 AUTO_FEEDING (LCD)
10 FILE_CHECKING       24 UNLOADING (LCD)
11 PRINTERS_CHECKING   25 UNLOADING_ABNORMAL (LCD)
12 RESUMING            26 UNLOADING_PAUSED (LCD)
13 PRINTING
```

OpenCentauri's documentation only lists 0 through 10 for this enum, which is incomplete.
The SDK's table is the fuller one. pycentauri re-exports it as
`PrintStatus` constants and adds 27 to 29 for CC2 Canvas events.

`PrintInfo.ErrorNumber`, from the spec's `sdcp_print_error_t`:

```
0 normal, 1 MD5 check failed, 2 file read failed,
3 resolution mismatch, 4 format mismatch, 5 machine model mismatch
```

### 1.6 MainboardID

`MainboardID` is the device identity. It is a 32-hex-character serial. It appears in three
places and all three must agree.

1. In the UDP discovery reply, `Data.MainboardID`
   ([spec](https://raw.githubusercontent.com/cbd-tech/SDCP-Smart-Device-Control-Protocol-V3.0.0/main/SDCP(Smart%20Device%20Control%20Protocol)_V3.0.0_EN.md)).
2. In the `Attributes` payload, `Attributes.MainboardID`.
3. In every command envelope, `Data.MainboardID` and `Topic`.

The SDK comments on why it must not be altered
([`elegoo_fdm_cc_message_adapter.cpp`](https://raw.githubusercontent.com/ELEGOO-3D/elegoo-link/main/src/lan/adapters/elegoo_fdm_cc/elegoo_fdm_cc_message_adapter.cpp)):

```cpp
attributesEvent.mainboardId = mainboardId; // This id will be used to control the printer, do not change it here, otherwise the printer cannot be controlled
```

Two consequences matter for an integration.

First, pin the id at config time from discovery. The printer only pushes `Attributes`
spontaneously while idle or printing, so a client that connects while the printer is paused
or errored will never learn the id and cannot send any command
([pycentauri PROTOCOL.md](https://raw.githubusercontent.com/bjan/pycentauri/main/docs/PROTOCOL.md),
[pycentauri README](https://raw.githubusercontent.com/bjan/pycentauri/main/README.md)).

Second, the id is also the routing key the HA integration's local proxy uses for camera
traffic, `http://<proxy>:3031/video?id=<printer.id>`
([camera.py](https://raw.githubusercontent.com/danielcherubini/elegoo-homeassistant/main/custom_components/elegoo_printer/camera.py)).

Example id from a live printer, `48551d180103147000001c0000000000`
([pycentauri PROTOCOL.md](https://raw.githubusercontent.com/bjan/pycentauri/main/docs/PROTOCOL.md)).

### 1.7 Subscribe and heartbeat

Cmd 512 subscribes to status pushes. pycentauri's builder
([`sdcp.py`](https://raw.githubusercontent.com/bjan/pycentauri/main/src/pycentauri/sdcp.py)):

```python
def build_subscribe(mainboard_id: str, period_ms: int = DEFAULT_PUSH_PERIOD_MS) -> dict[str, Any]:
    """Cmd 512 — request status pushes every ``period_ms`` milliseconds."""
    return build_request(Cmd.SUBSCRIBE, {"TimePeriod": int(period_ms)}, mainboard_id)
```

Heartbeat is a bare WebSocket text frame, literally `"ping"`, answered with `"pong"`
([spec](https://raw.githubusercontent.com/cbd-tech/SDCP-Smart-Device-Control-Protocol-V3.0.0/main/SDCP(Smart%20Device%20Control%20Protocol)_V3.0.0_EN.md)).
This is separate from Cmd 512.

---

## 2. HTTP REST surface

**There is no validated REST API on CC1.** This is the part of the brief that most needs
correcting. The endpoint list proposed in the brief, `GET /sdcp/status/<mainboardid>`,
`GET /sdcp/info`, `GET /sdcp/attributes`, `POST /sdcp/request/<mainboardid>`,
`GET /sdcp/file?Id=...&Path=...`, `/sdcp/files`, does not appear in any fetched source.
The two independent port probes that were read both describe port 80 as serving the Angular
web UI plus a single upload route
([pycentauri PROTOCOL.md](https://raw.githubusercontent.com/bjan/pycentauri/main/docs/PROTOCOL.md),
[elegoo-homeassistant CC2_PROTOCOL.md](https://raw.githubusercontent.com/danielcherubini/elegoo-homeassistant/main/docs/CC2_PROTOCOL.md)).

The `sdcp/status/<id>`, `sdcp/attributes/<id>` and `sdcp/response/<id>` strings are
**WebSocket topics**, not HTTP paths. A `GET` against those paths is not documented anywhere
that was fetched.

Confirmed HTTP routes on a CC1.

| Method | Path | Port | Confirmed by |
|---|---|---|---|
| `GET` | `/` | 80 | Angular web UI. pycentauri PROTOCOL.md. |
| `POST` | `/uploadFile/upload` | 80 | Elegoo SDK, pycentauri, OpenCentauri docs, SDCP spec. See section 5. |
| `GET` | `/downloadFile<path>` | 80 | Elegoo SDK `doDownload`, builds `"/downloadFile" + params.remoteFilePath`. |
| `GET` | `/video` | 3031 | MJPEG stream. See section 3. |

The upload port needs a note. The SDCP spec and the OpenCentauri doc both state
`http://<MainboardIP>:3030/uploadFile/upload`. The official SDK posts to
`endpoint + "/uploadFile/upload"` where the endpoint comes from
`UrlUtils::extractEndpoint(printerInfo.host)`, which returns `http://<host>` with no port
when the host carries no explicit port
([`utils.cpp`](https://raw.githubusercontent.com/ELEGOO-3D/elegoo-link/main/src/utils/utils.cpp),
`if (port != 0 && port != defaultPort) Url += ":" + std::to_string(port);`). pycentauri uses
port 80 and its `upload.py` docstring records the endpoint as `POST /uploadFile/upload`
([upload.py](https://raw.githubusercontent.com/bjan/pycentauri/main/src/pycentauri/upload.py)).
OpenCentauri's own Python sample omits the port entirely and so also lands on 80
([OpenCentauri API docs](https://raw.githubusercontent.com/suchmememanyskill/OpenCentauri/main/docs/software/api.md)).

Practical reading. Port 80 is the path that working code uses. Port 3030 is plausible
because the same daemon serves both, and one integration's docs claim it. Try 80 first.

A POST body does **not** use the SDCP envelope. Upload is plain multipart form data. See
section 5.

Thumbnails. On CC1 a thumbnail URL arrives inside Cmd 321's `HistoryDetailList[].Thumbnail`,
for example `http://192.168.1.2/thumb.jpg`
([OpenCentauri API docs](https://raw.githubusercontent.com/suchmememanyskill/OpenCentauri/main/docs/software/api.md)).
There is no separate documented thumbnail endpoint. On CC2 the equivalent is MQTT method
1045 returning base64 PNG
([CC2_PROTOCOL.md](https://raw.githubusercontent.com/danielcherubini/elegoo-homeassistant/main/docs/CC2_PROTOCOL.md)).

---

## 3. Camera and video

**The CC1 has a camera, and it is an unauthenticated MJPEG HTTP stream.**

| Property | Value |
|---|---|
| Port | 3031 |
| Path | `/video` |
| Content type | `multipart/x-mixed-replace; boundary=--foo` |
| Frame format | Full JPEG, `FF D8 ... FF D9` |
| Native rate | about 10 fps at 640x360 |

Evidence. [pycentauri PROTOCOL.md](https://raw.githubusercontent.com/bjan/pycentauri/main/docs/PROTOCOL.md)
gives the port, path and content type. [pycentauri `camera.py`](https://raw.githubusercontent.com/bjan/pycentauri/main/src/pycentauri/camera.py)
implements the grab and states the format in its module docstring:

```python
"""Grab JPEG snapshots from the Centauri Carbon's built-in webcam.

The printer exposes an MJPEG stream at ``http://<host>:3031/video``
(``multipart/x-mixed-replace``). A snapshot is the first complete JPEG
frame (SOI ``FF D8`` through EOI ``FF D9``) we can read, after which we
close the connection.
"""
CAMERA_PORT = 3031
CAMERA_PATH = "/video"
```

The Home Assistant integration reaches the same endpoint
([const.py](https://raw.githubusercontent.com/danielcherubini/elegoo-homeassistant/main/custom_components/elegoo_printer/const.py),
`VIDEO_PORT = 3031`, `VIDEO_ENDPOINT = "video"`) and builds
`http://<printer_ip>:3031/video` directly, or `http://<proxy>:3031/video?id=<mainboard id>`
when its local proxy is enabled
([camera.py](https://raw.githubusercontent.com/danielcherubini/elegoo-homeassistant/main/custom_components/elegoo_printer/camera.py)).

RTSP appears only in the resin path of the HA integration, which uses ffmpeg against the
`VideoUrl` returned by Cmd 386. For an FDM printer that class is not instantiated. The
printer-type switch is explicit in `async_setup_entry`, `PrinterType.FDM` selects the MJPEG
camera and `PrinterType.RESIN` selects the ffmpeg camera.

**Enabling the stream.** Two fetched implementations disagree, and this is a real
uncertainty for the integration.

pycentauri never sends Cmd 386. Its `snapshot()` opens port 3031 and reads a frame
directly, and its client exposes `snapshot()` with no enable step.

The Home Assistant integration does send it, and treats the stream as a scarce resource.
It calls `get_printer_video(enable=True)` before opening a viewer, and it ref-counts
viewers so the printer stream is disabled when the last viewer leaves
([camera.py](https://raw.githubusercontent.com/danielcherubini/elegoo-homeassistant/main/custom_components/elegoo_printer/camera.py)):

```python
async def _ensure_stream_enabled(self) -> None:
    """Enable printer video if not already enabled. Idempotent."""
    ...
    video = await self._printer_client.get_printer_video(enable=True)
    if video.status == ElegooVideoStatus.SUCCESS:
        self._stream_enabled = True
```

The HA integration's stated reason, in the same file, is that the printer advertises a
fixed number of video connections and a stream left enabled with no viewers holds a slot
that "can block other consumers and requires a printer reboot to release".

Practical recommendation for the integration. Read the frame first without sending Cmd 386.
Only add the enable step if the first read returns no frame. This keeps the simple path
simple and avoids consuming a slot when it is not needed.

**Connection-slot leak.** The camera server has few slots and leaks them. A disconnected
client lingers in `FIN-WAIT-2` because the printer never sends its FIN, and once slots run
out new connections receive zero frames. Multiple tabs or per-request proxying starves the
stream. pycentauri's answer is a broadcaster that holds one upstream connection and fans it
out to every browser ([`mjpeg_broadcast.py`](https://raw.githubusercontent.com/bjan/pycentauri/main/src/pycentauri/mjpeg_broadcast.py),
described in [PROTOCOL.md](https://raw.githubusercontent.com/bjan/pycentauri/main/docs/PROTOCOL.md)).
Any HA integration that proxies this camera should do the same.

---

## 4. Authentication

**The CC1 requires no access code on the LAN.** Discovery, WebSocket control, upload and
camera are all open. Evidence.

pycentauri's README states it plainly in the model comparison table: `CC1`, transport
`SDCP v3 over WebSocket (:3030)`, auth `none`
([README](https://raw.githubusercontent.com/bjan/pycentauri/main/README.md)).

The Home Assistant integration supports CC1 with no credential in the config entry. The
credential keys it defines, `CONF_CC2_ACCESS_CODE` and `CONF_CC2_TOKEN_STATUS`, are CC2
specific ([const.py](https://raw.githubusercontent.com/danielcherubini/elegoo-homeassistant/main/custom_components/elegoo_printer/const.py)).

**The CC2 does require authentication, and that is the model that locked down.** Its MQTT
broker wants username `elegoo` and a password that is either the literal `123456` when no
access code is set, or the access code shown on the printer screen. Its HTTP surface wants
the same code as an `X-Token` **query parameter**, not a header
([pycentauri PROTOCOL.md](https://raw.githubusercontent.com/bjan/pycentauri/main/docs/PROTOCOL.md),
[CC2_PROTOCOL.md](https://raw.githubusercontent.com/danielcherubini/elegoo-homeassistant/main/docs/CC2_PROTOCOL.md)).

Two more CC2 lockdown facts worth recording for the integration's docs.

LAN Only mode must be enabled on the CC2's touchscreen or the local API is unreachable at
all. Firmware `02.00.02.00` additionally removes SSH and blocks firmware downgrades
([pycentauri PROTOCOL.md](https://raw.githubusercontent.com/bjan/pycentauri/main/docs/PROTOCOL.md)).

**What changed between firmware versions on CC1.** Two version-gated behaviours were
found, neither of them authentication.

The HA integration notes a firmware `v1.1.29` bug that blocks remote control of lights and
temperatures while a print is in progress, with `v1.1.25` named as the earlier alternative
([README](https://raw.githubusercontent.com/danielcherubini/elegoo-homeassistant/main/README.md)).

The SDK gates multi-filament capability on firmware `> 1.1.x`
([`elegoo_fdm_cc_message_adapter.cpp`](https://raw.githubusercontent.com/ELEGOO-3D/elegoo-link/main/src/lan/adapters/elegoo_fdm_cc/elegoo_fdm_cc_message_adapter.cpp)):

```cpp
if (majorVer > 1 || (majorVer == 1 && minorVer > 1)) {
    supportsMultiFilament = true;
}
```

There is no evidence of an access code, password or token ever being added to the CC1 SDCP
surface. If a future CC1 firmware closes the API, no fetched source documents it.

---

## 5. File upload and G-code

**Transport.** Plain HTTP, port 80, entirely separate from the WebSocket control channel.
Because it never touches the SDCP socket, an upload cannot trip the unknown-command crash
path ([pycentauri PROTOCOL.md](https://raw.githubusercontent.com/bjan/pycentauri/main/docs/PROTOCOL.md)).

**Endpoint.** `POST /uploadFile/upload`.

**Framing.** Multipart form data, one chunk per request. Chunk size is 1 MiB, which is the
documented maximum. Every chunk carries the whole-file MD5, not a per-chunk one, plus a
transfer UUID that is constant across all chunks.

Exact per-chunk fields, from the spec's request parameter block
([spec](https://raw.githubusercontent.com/cbd-tech/SDCP-Smart-Device-Control-Protocol-V3.0.0/main/SDCP(Smart%20Device%20Control%20Protocol)_V3.0.0_EN.md)):

```
S-File-MD5: <md5 of the whole file>
Check:      '1'          // 0 disables verification
Offset:     0            // byte offset of this chunk
Uuid:       xxxxxx       // same value in every chunk
TotalSize:  123          // total file size in bytes
File:       (binary)     // the chunk, with the target filename
```

The official SDK builds exactly these six fields into an `httplib::UploadFormDataItems`
([`elegoo_fdm_cc_http_transfer.cpp`](https://raw.githubusercontent.com/ELEGOO-3D/elegoo-link/main/src/lan/adapters/elegoo_fdm_cc/elegoo_fdm_cc_http_transfer.cpp)):

```cpp
httplib::UploadFormDataItems items = {
    {"Check", "1", "", ""},
    {"S-File-MD5", fileMD5, "", ""},
    {"Offset", std::to_string(offset), "", ""},
    {"Uuid", uuid, "", ""},
    {"TotalSize", std::to_string(totalSize), "", ""},
    {"File", std::string(data.begin(), data.end()), fileName, "application/octet-stream"}};
auto response = client.Post("/uploadFile/upload", items);
```

The same file records the chunk cap in a comment: `const size_t maxChunkSize = 1024 * 1024; // 1MB per chunk`.

**Success and failure.** Per-chunk success is JSON `{"code": "000000"}`
([spec](https://raw.githubusercontent.com/cbd-tech/SDCP-Smart-Device-Control-Protocol-V3.0.0/main/SDCP(Smart%20Device%20Control%20Protocol)_V3.0.0_EN.md)):

```json
{ "code": "000000", "messages": null, "data": {}, "success": true }
```

Failure carries a `messages` array whose entries are either field-level validation reasons
or a numeric code under the sentinel field name `common_field`:

```json
{ "code": "111111",
  "messages": [
    { "field": "common_field", "message": -1 },
    { "field": "filename", "message": "Cannot be empty" } ],
  "data": null, "success": false }
```

Upload error codes, from the spec's table:

| Code | Meaning |
|---|---|
| -1 | Illegal file offset, less than 0 |
| -2 | Offset does not match the current file |
| -3 | File cannot be opened |
| -4 | Unknown error |

**Accepted file types.** `SupportFileType` is advertised by the printer in its `Attributes`
payload. OpenCentauri's Centauri Carbon sample shows `["GCODE"]`, and the same page lists
`Capabilities: ["FILE_TRANSFER", "PRINT_CONTROL", "VIDEO_STREAM"]`
([OpenCentauri API docs](https://raw.githubusercontent.com/suchmememanyskill/OpenCentauri/main/docs/software/api.md)).
The upstream resin spec's sample shows `["CTB"]`, which is the resin format. Read
`SupportFileType` from the printer rather than hard-coding, because the same integration
serves resin and FDM models.

**Where files land.** Internal storage, and `start_print` addresses them by name. pycentauri
notes the printer returns full paths such as `/local/x.gcode` and strips to the bare name
([`client.py`](https://raw.githubusercontent.com/bjan/pycentauri/main/src/pycentauri/client.py),
`"filename": f.get("name", "").rsplit("/", 1)[-1]`). The SDK's builder prefixes
`/local` for internal and `/usb` for USB
([`elegoo_fdm_cc_message_adapter.cpp`](https://raw.githubusercontent.com/ELEGOO-3D/elegoo-link/main/src/lan/adapters/elegoo_fdm_cc/elegoo_fdm_cc_message_adapter.cpp)).

**Max size.** UNCONFIRMED. No fetched source states a total file size cap. The only
documented limit is the 1 MiB per-chunk maximum. The printer does advertise free space in
`Attributes.RemainingMemory` in bytes
([OpenCentauri API docs](https://raw.githubusercontent.com/suchmememanyskill/OpenCentauri/main/docs/software/api.md)),
so an integration can refuse an upload that will not fit by checking that field first. That
is the recommendation, since a truncated upload that fails midway is the worse failure.

**Download.** `GET /downloadFile<path>` on port 80, built by the SDK's `doDownload` and
`getDownloadUrl` ([`elegoo_fdm_cc_http_transfer.cpp`](https://raw.githubusercontent.com/ELEGOO-3D/elegoo-link/main/src/lan/adapters/elegoo_fdm_cc/elegoo_fdm_cc_http_transfer.cpp)).
The SDK issues a `HEAD` first, reads `content-length`, then streams the `GET`.

---

## 6. Existing open-source implementations

### 6.1 The protocol-owning SDKs and specs

**Elegoo's official C++ SDK.** <https://github.com/ELEGOO-3D/elegoo-link>. Redirects to
`elegooofficial/elegoo-link`, Apache-2.0. This is the only authoritative artifact for CC1
command codes and enums. Key file,
<https://raw.githubusercontent.com/ELEGOO-3D/elegoo-link/main/src/lan/adapters/elegoo_fdm_cc/elegoo_fdm_cc_message_adapter.cpp>.
It is the source of the `COMMAND_MAPPING_TABLE`, the `sdcp_machine_status_t` and
`sdcp_print_status_t` enums, the `START_PRINT` payload, and the 403 dispatch. Its
commented-out mapping rows are a map of everything Elegoo built but did not enable.

Other files in the same repo that matter. Upload and download live in
<https://raw.githubusercontent.com/ELEGOO-3D/elegoo-link/main/src/lan/adapters/elegoo_fdm_cc/elegoo_fdm_cc_http_transfer.cpp>.
URL construction and TLS scheme selection live in
<https://raw.githubusercontent.com/ELEGOO-3D/elegoo-link/main/src/lan/adapters/elegoo_fdm_cc/elegoo_fdm_cc_protocol.cpp>.
Discovery lives in
<https://raw.githubusercontent.com/ELEGOO-3D/elegoo-link/main/src/lan/adapters/elegoo_fdm_cc/elegoo_fdm_cc_discovery_strategy.cpp>,
which returns the literal `"M99999"` probe.

**CBD Technology SDCP v3.0.0 spec.** <https://github.com/cbd-tech/SDCP-Smart-Device-Control-Protocol-V3.0.0>.
Raw file fetched,
<https://raw.githubusercontent.com/cbd-tech/SDCP-Smart-Device-Control-Protocol-V3.0.0/main/SDCP(Smart%20Device%20Control%20Protocol)_V3.0.0_EN.md>.
This is the vendor spec. It is resin-era in places, so its `sdcp_machine_status_t` stops at
value 4 and its `SupportFileType` sample is `CTB`, but its envelope, topics, Ack enums,
upload format and error codes are the contract.

**OpenCentauri Centauri-specific API docs.** The best prose reference for the CC1 quirk
layer. Rendered at <https://docs.opencentauri.cc/software/api/>. Source markdown at
<https://raw.githubusercontent.com/suchmememanyskill/OpenCentauri/main/docs/software/api.md>.
Written by remmylee from the Elegoo Discord. It is the source for Cmd 386 returning
`VideoUrl`, the `from: 0` convention, the field-name typo list, and the CC1 specific
`Capabilities` sample. Its `HTTP File Transfer Interface` section says port 3030, which
conflicts with the SDK's port 80, as noted in section 2.

**OpenCentauri project.** <https://github.com/OpenCentauri/OpenCentauri>, 548 stars, MIT.
Patched and replacement firmware for the CC1. Adds SSH and a debug shell but does not patch
the SDCP daemon ([pycentauri PROTOCOL.md](https://raw.githubusercontent.com/bjan/pycentauri/main/docs/PROTOCOL.md),
"OpenCentauri did not patch the SDCP daemon — it's the same unmodified Elegoo app binary").

**Elegoo open firmware.** <https://github.com/elegooofficial/CentauriCarbon> (GPL-3.0) and
<https://github.com/elegooofficial/CentauriCarbon2>. These carry the embedded Klipper
motion stack but not the network layer. Useful for status field provenance, not for the API.

### 6.2 Python client with the most complete field notes

**bjan/pycentauri.** <https://github.com/bjan/pycentauri>. Apache-2.0. Async client, CLI,
MCP server, FastAPI server and RTSP bridge for both CC1 and CC2. This is the single richest
community artifact that was read, and its PROTOCOL.md is unusually honest about what was
tested and when.

Exact files fetched and what each proves.

- <https://raw.githubusercontent.com/bjan/pycentauri/main/docs/PROTOCOL.md>. The port table,
  the crash hazard, the confirmed versus unprobed command split, the status schemas for two
  firmware generations, the Cmd 321 task-status decoding, the camera framing and slot leak,
  and the upload field table.
- <https://raw.githubusercontent.com/bjan/pycentauri/main/src/pycentauri/sdcp.py>. The
  envelope, the `Cmd` IntEnum, `MessageType`, and `parse_message` with its
  leading-digit-length workaround.
- <https://raw.githubusercontent.com/bjan/pycentauri/main/src/pycentauri/client.py>. The
  WebSocket URL constant, `WS_PORT = 3030`, `WS_PATH = "/websocket"`, `max_size=None` on
  connect, the Cmd 512 subscribe, and the Cmd 0 fallback when pushes go silent.
- <https://raw.githubusercontent.com/bjan/pycentauri/main/src/pycentauri/upload.py>. The
  exact multipart field names, the 1 MiB `CHUNK_SIZE`, and `UPLOAD_PORT = 80`.
- <https://raw.githubusercontent.com/bjan/pycentauri/main/src/pycentauri/camera.py>. The
  MJPEG snapshot implementation, `CAMERA_PORT = 3031`, `CAMERA_PATH = "/video"`.
- <https://raw.githubusercontent.com/bjan/pycentauri/main/src/pycentauri/discovery.py>.
  `DISCOVERY_PORT = 3000`, `DISCOVERY_PROBE = b"M99999"`, and the retransmit logic.
- <https://raw.githubusercontent.com/bjan/pycentauri/main/src/pycentauri/models.py>. The
  `PrintStatus` constant table, the dual temperature format handling in `_extract_temp`, and
  the `CurrenStatus` versus `CurrentStatus` fallback.

### 6.3 The Home Assistant integration

**danielcherubini/elegoo-homeassistant.** <https://github.com/danielcherubini/elegoo-homeassistant>.
HACS default. This is the closest existing prior art for the integration being built.

The README states the coverage directly
([README](https://raw.githubusercontent.com/danielcherubini/elegoo-homeassistant/main/README.md)):
"Most newer models utilize WebSockets for communication. This integration offers full
support for: Mars Range (e.g., Mars 5, 5 Ultra), Saturn Range (e.g., Saturn 4, 4 Ultra),
Centauri Range (e.g., Centauri Carbon)". It also states the connection limit and the proxy
workaround: "Modern Elegoo printers often have a built-in limit of 4 simultaneous
connections. Since the video stream consumes one of these by itself, users can easily hit
this limit. The optional proxy server acts as a single gateway".

Two README findings are load-bearing for the integration's scope.

First, a start-print hazard recorded against this exact model. The README's `start_print`
section: "Not available for the first-generation Centauri Carbon or resin printers, where
the equivalent SDCP command crashed the printer (#297)."

Second, the firmware `v1.1.29` light and temperature limitation noted in section 4.

File-level detail, all fetched.

- <https://raw.githubusercontent.com/danielcherubini/elegoo-homeassistant/main/custom_components/elegoo_printer/const.py>.
  `DISCOVERY_MESSAGE = "M99999"`, `DISCOVERY_PORT = 3000`, `PROXY_HOST = "127.0.0.1"`,
  `VIDEO_ENDPOINT = "video"`, `VIDEO_PORT = 3031`, `WEBSOCKET_PORT = 3030`.
- <https://raw.githubusercontent.com/danielcherubini/elegoo-homeassistant/main/custom_components/elegoo_printer/sdcp/const.py>.
  The full command constant list, including CC2-only codes 257, 260, 322, 323 and the
  AMS range 500 to 505.
- <https://raw.githubusercontent.com/danielcherubini/elegoo-homeassistant/main/custom_components/elegoo_printer/websocket/client.py>.
  `url = f"ws://{self.printer.ip_address}:{WEBSOCKET_PORT}/websocket"` with
  `heartbeat=30`, and the topic router that dispatches on the second topic segment.
- <https://raw.githubusercontent.com/danielcherubini/elegoo-homeassistant/main/custom_components/elegoo_printer/camera.py>.
  The ref-counted video lifecycle and the Cmd 386 enable and disable.
- <https://raw.githubusercontent.com/danielcherubini/elegoo-homeassistant/main/docs/CC2_PROTOCOL.md>.
  108 KB of CC2 protocol detail. Authoritative for CC2, and it is the source for the
  statement that CC1 discovery uses `M99999` on UDP 3000 while CC2 uses
  `{"id":0,"method":7000}` on UDP 52700.

One caution on that CC2 document. Its CC1 comparison table calls the CC1 transport "TCP"
and says "Max Clients: Unlimited". Both are wrong against every other source read here.
CC1 transport is WebSocket and the limit is 5. Cross-check its CC1 rows before relying on
them.

### 6.4 Others

**HA-Centauri-Carbon.** <https://github.com/terriblyvile/HA-Centauri-Carbon>. Home Assistant
integration specifically for the Centauri Carbon. The repository page was fetched but its
source files were not read, so treat the design as UNINSPECTED.

**elegoo-printer-proxy.** <https://github.com/lantern-eight/elegoo-printer-proxy>. Sits
between ElegooSlicer and the printer, captures every G-code at upload time and parses
per-slot filament data. Supports CC1 and CC2. Cited by the HA integration's README, which
also explains why it is needed: per-slot filament usage exists only inside the G-code file,
and files cannot be read back off the printer
([README](https://raw.githubusercontent.com/danielcherubini/elegoo-homeassistant/main/README.md)).

**CentauriLink.** `CentauriLink/Centauri-Link`, a Kivy GUI. Its `main.py` is cited twice by
pycentauri as documenting the SDCP envelope and the OctoEverywhere tunnel layer. **The
repository returned HTTP 404 from both the API and the raw file host when fetched for this
report.** The project is cited second-hand only. Do not rely on the link without
re-verifying it, and note that the brief's suggested `caesar1111/elegoo-link` also could not
be located.

**OctoPrint-OctoEverywhere.** <https://github.com/QuinnDamerell/OctoPrint-OctoEverywhere>,
AGPL-3.0, 302 stars. Description includes Elegoo. pycentauri credits it as a source for the
Cmd 512 subscribe being the status-push command.

**OctoPrint-Chituboard.** <https://github.com/rudetrooper/Octoprint-Chituboard>. Adds
Chitu-board printer support to OctoPrint, covering Elegoo Mars, Anycubic Photon and Phrozen.
This is the older Chitu/CTB world, not SDCP.

**NOT FOUND.** No Homey, openHAB or Node-RED SDCP implementation surfaced in the searches
run. A Node-RED node, an openHAB binding, and the brief's `cbd-taylor/elegoo-*`,
`mistergibson/...` and `knoopx/...` names were all searched for and none were located. Treat
all of those as non-existent until someone produces a URL.

---

## 7. Known gotchas

**TimeStamp units are inconsistent between sources.** The spec's examples show
`1687069655`, which is seconds. The Elegoo SDK generates milliseconds
([`elegoo_fdm_cc_message_adapter.cpp`](https://raw.githubusercontent.com/ELEGOO-3D/elegoo-link/main/src/lan/adapters/elegoo_fdm_cc/elegoo_fdm_cc_message_adapter.cpp)):

```cpp
printerMessage["TimeStamp"] = std::chrono::duration_cast<std::chrono::milliseconds>(
                                  std::chrono::steady_clock::now().time_since_epoch()).count();
```

pycentauri sends `int(time.time() * 1000)`
([`sdcp.py`](https://raw.githubusercontent.com/bjan/pycentauri/main/src/pycentauri/sdcp.py)). OpenCentauri's
implementation notes say the opposite, "All timestamps are Unix epoch in seconds".

**Recommendation.** Send milliseconds, matching what the official client sends to real CC1
hardware. No fetched source reports the printer rejecting either unit, and no fetched client
validates an incoming `TimeStamp`, so this is low risk either way.

**Tick fields are worse than the envelope timestamp.** The spec says `CurrentTicks` and
`TotalTicks` are milliseconds. OpenCentauri's sample shows `"CurrentTicks": 3600` for a
`TotalTicks` of 36000, which reads as seconds. pycentauri's model accepts either int or
float and explicitly notes that the firmware mixes them across revisions
([`models.py`](https://raw.githubusercontent.com/bjan/pycentauri/main/src/pycentauri/models.py),
"the printer mixes int and float for tick/time fields across firmware revisions").
Treat ticks as UNCONFIRMED and sanity-check against a known-duration job.

**Concurrent clients.** The CC1 WebSocket server is a hard-capped pool, not a per-client
lock. Six concurrent connections fail, five do not. pycentauri is specific
([PROTOCOL.md](https://raw.githubusercontent.com/bjan/pycentauri/main/docs/PROTOCOL.md)):
"A 6th `connect()` returns HTTP 500 with literal body `\"too many client\"`. Slots release
immediately on close — there's no cooldown." The HA integration states 4 for modern Elegoo
printers and adds that the video stream consumes one of those slots
([README](https://raw.githubusercontent.com/danielcherubini/elegoo-homeassistant/main/README.md)).
Treat 5 as the CC1 figure and hold exactly one connection.

The printer does not limit you to one client. Multiple clients may connect, and any of them
can drive the machine. That is a safety consideration, not just a capacity one. The
integration should hold one long-lived connection and serialise commands through it, which
is what both pycentauri's server and the HA integration's proxy do.

**Unknown commands crash the daemon, which kills the print.** This is the sharpest edge on
the whole platform. pycentauri's PROTOCOL.md opens with a boxed warning: a few unrecognised
`Cmd` codes in quick succession crash the printer's `app` daemon, and on both stock and
OpenCentauri that daemon is the entire host firmware including the gcode interpreter. There
is no Klipper or Moonraker process underneath to keep the job alive. Motion halts, the MCU
watchdog cuts heater power, the screen goes dark, and the part is destroyed. Recovery is a
power cycle.

Two supporting details. An unexpected *payload shape* on a known command can trip the same
path, which is why pycentauri retracted an earlier claim that Cmd 258 was dangerous.
Cmds 258, 259, 320 and 321 were later confirmed working with the correct payloads, and the
read-only ones were hammered against an active print with no disturbance.

**Practical rule for the integration.** Never send a command that is not on the confirmed
list. Gate writes on `PrintInfo.Status == 0` while probing. Never probe while a print runs.

**Start print is the one confirmed command that has reportedly bricked a job.** Two
implementations reach opposite conclusions, which the integration must resolve.

The Elegoo SDK enables `START_PRINT` at 128 and builds a full payload for it.
pycentauri enables it and its README documents the flow as upload then start.

The HA integration deliberately omits it for this exact printer, citing a crash. Its README
says `start_print` is "Not available for the first-generation Centauri Carbon or resin
printers, where the equivalent SDCP command crashed the printer (#297)".

**Recommendation.** Ship start-print disabled by default for CC1, expose it behind an
explicit opt-in, and verify against the real printer with a short benign file before letting
it anywhere near a user's automation. Do not treat the SDK's enabled mapping as proof that
the CC1 firmware handles it safely.

**TLS is not on 443.** The SDK maps an `https` host to `wss://<host>:3030/websocket`, so
TLS reuses port 3030 with a secure scheme rather than moving to 443. Nothing in any fetched
source shows port 443 open on a CC1. A plain `ws://` on 3030 over HTTP on 80 is the normal
LAN configuration.

**The push scheduler can die while commands still work.** On firmware V0.3.0-o, Cmd 512 is
ACKed but no status frame is ever pushed while the printer idles. Cmd 0 still returns
`Ack=0` and still emits its one-shot status frame. A reboot does not fix it. Pushes resume
at full rate once a print starts. pycentauri's answer is to send Cmd 0 whenever a subscribe
produces no push within one period, and to treat request-based polling as the primary path
([client.py](https://raw.githubusercontent.com/bjan/pycentauri/main/src/pycentauri/client.py),
`status()` and `watch()`). An integration that waits passively for pushes will hang on an
idle printer. Whether stock V1.1.46 behaves the same is UNCONFIRMED.

**Some firmware prefixes WebSocket frames with a decimal length.** Frames can arrive as
`"123{json...}"`. pycentauri strips leading digits before the first `{`
([`sdcp.py`](https://raw.githubusercontent.com/bjan/pycentauri/main/src/pycentauri/sdcp.py)):

```python
text = text.lstrip()
if text and text[0].isdigit():
    first_brace = text.find("{")
    if first_brace > 0:
        text = text[first_brace:]
```

**Frame size caps can kill the reader.** pycentauri passes `max_size=None` to `connect()`
because length spikes on the WS reader crash some `websockets` versions
([client.py](https://raw.githubusercontent.com/bjan/pycentauri/main/src/pycentauri/client.py)).
Do the same in any Python client, and raise the limit in whatever library the integration
uses.

**Field name typos are part of the protocol.** `CurrenCoord`, `RelaseFilmState`,
`MaximumCloudSDCPSercicesAllowed`, `PlatFormType`. Accept both spellings on read. Do not
"fix" them on write.

**Paused and errored printers do not push Attributes.** Covered in section 1.6. Pin the
mainboard id from discovery at config time.

**Discovery can drop on a busy LAN.** The UDP probe is unreliable. Retransmit a few times
within the timeout window
([`discovery.py`](https://raw.githubusercontent.com/bjan/pycentauri/main/src/pycentauri/discovery.py)).

---

## MINIMAL WORKING SEQUENCE

Literal frames a Python developer can implement. All of this is CC1. The CC2 needs MQTT
instead and is out of scope for these snippets.

### (a) Connect

Three steps. Discover, then open the socket, then learn the id.

```python
import asyncio, json, socket, time, secrets
import websockets

DISCOVERY_PROBE = b"M99999"

def discover(timeout=3.0):
    """UDP broadcast. Returns (host, mainboard_id) or None."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(timeout)
    s.sendto(DISCOVERY_PROBE, ("255.255.255.255", 3000))
    try:
        data, addr = s.recvfrom(4096)
    except socket.timeout:
        return None
    finally:
        s.close()
    inner = json.loads(data.decode())["Data"]
    return addr[0], inner["MainboardID"]   # id is 32 hex chars

async def connect(host):
    # max_size=None: firmware occasionally sends oversized frames.
    return await websockets.connect(f"ws://{host}:3030/websocket", max_size=None)
```

The first frame to arrive may be the spontaneous `Attributes` push. Its payload carries
`MainboardID`. If discovery succeeded, you already have it and should not wait for this.

```json
{
  "Id": "48551d180103147000001c0000000000",
  "Data": { "Cmd": 1,
    "Data": {}, "RequestID": "000000000001d354",
    "MainboardID": "48551d180103147000001c0000000000",
    "TimeStamp": 1687069655, "From": 0 },
  "Topic": "sdcp/request/48551d180103147000001c0000000000"
}
```

The printer answers on `Data.Data.Ack`:

```json
{
  "Id": "48551d180103147000001c0000000000",
  "Data": { "Cmd": 1, "Data": { "Ack": 0 },
    "RequestID": "000000000001d354",
    "MainboardID": "48551d180103147000001c0000000000",
    "TimeStamp": 1687069655 },
  "Topic": "sdcp/response/48551d180103147000001c0000000000"
}
```

Enable "too many client" handling now. The 6th connection gets HTTP 500 with that body.

### (b) Read status

Subscribe, then poll as the reliable path.

```python
def frame(cmd, data, mid, request_id=None):
    return json.dumps({
        "Id": mid,
        "Data": {
            "Cmd": cmd,
            "Data": data,
            "RequestID": request_id or secrets.token_hex(8),
            "MainboardID": mid,
            "TimeStamp": int(time.time() * 1000),   # milliseconds, matching the SDK
            "From": 0,
        },
        "Topic": f"sdcp/request/{mid}",
    }, separators=(",", ":"))

async def subscribe_stats(ws, mid, period_ms=5000):
    await ws.send(frame(512, {"TimePeriod": period_ms}, mid))
    # Ack arrives on sdcp/response. Status frames then arrive on sdcp/status
    # at the requested rate. If nothing arrives within one period, fall back:
    await asyncio.sleep(period_ms / 1000 + 1)
    await ws.send(frame(0, {}, mid))   # one-shot status on sdcp/status
```

The one-shot Cmd 0 frame:

```json
{"Id":"48551d180103147000001c0000000000",
 "Data":{"Cmd":0,"Data":{},"RequestID":"a1b2c3d4e5f60718",
         "MainboardID":"48551d180103147000001c0000000000",
         "TimeStamp":1687069655000,"From":0},
 "Topic":"sdcp/request/48551d180103147000001c0000000000"}
```

The status frame that comes back on `sdcp/status/<id>`. Shape from the SDK's
`handlePrinterStatus` and pycentauri's schema.

```json
{
  "Status": {
    "CurrentStatus": [1],
    "TimeLapseStatus": 0,
    "PlatFormType": 0,
    "TempOfHotbed": 50.04, "TempOfNozzle": 255.01, "TempOfBox": 29.20,
    "TempTargetHotbed": 50, "TempTargetNozzle": 255, "TempTargetBox": 0,
    "CurrenCoord": "139.96,123.71,5.33",
    "CurrentFanSpeed": { "ModelFan": 58, "AuxiliaryFan": 0, "BoxFan": 68 },
    "ZOffset": 0.415,
    "LightStatus": { "SecondLight": 1, "RgbLight": [0, 0, 0] },
    "PrintInfo": {
      "Status": 13, "CurrentLayer": 34, "TotalLayer": 438,
      "CurrentTicks": 364.98, "TotalTicks": 4504,
      "Filename": "cube.gcode",
      "TaskId": "295cb186-daf5-4b84-9668-59a520e4640a",
      "PrintSpeedPct": 100, "Progress": 9
    }
  },
  "MainboardID": "48551d180103147000001c0000000000",
  "TimeStamp": 1687069655000,
  "Topic": "sdcp/status/48551d180103147000001c0000000000"
}
```

Remaining seconds on CC1 is `TotalTicks - CurrentTicks`. There is no remainder field.

### (c) Fetch the file list

```json
{"Id":"48551d180103147000001c0000000000",
 "Data":{"Cmd":258,"Data":{"Url":"/local"},"RequestID":"11aa22bb33cc44dd",
         "MainboardID":"48551d180103147000001c0000000000",
         "TimeStamp":1687069655000,"From":0},
 "Topic":"sdcp/request/48551d180103147000001c0000000000"}
```

Use `/usb/<path>` instead of `/local` for USB storage. The response arrives on
`sdcp/response/<id>`.

```json
{"Data": { "Cmd": 258,
    "Data": { "Ack": 0,
      "FileList": [
        { "name": "/local/cube.gcode", "usedSize": 123456, "totalSize": 123456,
          "storageType": 0, "type": 1 } ] },
    "RequestID": "11aa22bb33cc44dd",
    "MainboardID": "48551d180103147000001c0000000000" },
  "Topic": "sdcp/response/48551d180103147000001c0000000000"}
```

`type` 0 is a folder, 1 is a file. `storageType` 0 is internal, 1 is external. Strip the
path prefix before passing the name to start print.

Upload the file first with the section 5 multipart call, otherwise start print answers
`Ack = 2`.

### (d) Start a print

**Read section 7 first.** The HA integration reports this command crashing this exact model.

```json
{"Id":"48551d180103147000001c0000000000",
 "Data":{"Cmd":128,
   "Data":{"Filename":"cube.gcode","StartLayer":0,"Calibration_switch":1,
           "PrintPlatformType":0,"Tlp_Switch":0,"slot_map":[]},
   "RequestID":"deadbeefcafe0001",
   "MainboardID":"48551d180103147000001c0000000000",
   "TimeStamp":1687069655000,"From":0},
 "Topic":"sdcp/request/48551d180103147000001c0000000000"}
```

`Calibration_switch` 1 enables auto bed leveling, 0 disables. `Tlp_Switch` 1 enables time
lapse. Success is `Ack = 0`. `Ack = 2` means the file name was not found.

### (e) Pause

```json
{"Id":"48551d180103147000001c0000000000",
 "Data":{"Cmd":129,"Data":{},"RequestID":"deadbeefcafe0002",
         "MainboardID":"48551d180103147000001c0000000000",
         "TimeStamp":1687069655000,"From":0},
 "Topic":"sdcp/request/48551d180103147000001c0000000000"}
```

To resume, send the same frame with `"Cmd": 131`.

### (f) Stop

```json
{"Id":"48551d180103147000001c0000000000",
 "Data":{"Cmd":130,"Data":{},"RequestID":"deadbeefcafe0003",
         "MainboardID":"48551d180103147000001c0000000000",
         "TimeStamp":1687069655000,"From":0},
 "Topic":"sdcp/request/48551d180103147000001c0000000000"}
```

### (g) Fetch a camera frame

No WebSocket command required. Plain HTTP GET. Read bytes until SOI then EOI.

```python
import httpx

SOI, EOI = b"\xff\xd8", b"\xff\xd9"

async def snapshot(host, port=3031, path="/video", cap=8 * 1024 * 1024):
    buf, start = bytearray(), None
    async with httpx.AsyncClient(timeout=10.0) as client:
        async with client.stream("GET", f"http://{host}:{port}{path}") as r:
            if r.status_code != 200:
                raise RuntimeError(f"camera HTTP {r.status_code}")
            async for chunk in r.aiter_bytes():
                buf.extend(chunk)
                if start is None:
                    i = buf.find(SOI)
                    if i >= 0:
                        start = i
                if start is not None:
                    j = buf.find(EOI, start + 2)
                    if j >= 0:
                        return bytes(buf[start:j + 2])
                if len(buf) > cap:
                    raise RuntimeError("frame exceeded cap")
    raise RuntimeError("stream ended before a complete JPEG")
```

Use a `curl` one-liner to smoke-test first, `curl -s http://<host>:3031/video -m 1 -o /tmp/frame.jpg`.
Content type is `multipart/x-mixed-replace; boundary=--foo`.

If that returns no bytes, send Cmd 386 with `{"Enable": 1}` and retry. The response carries
`Data.Data.VideoUrl`. Disable with `{"Enable": 0}` when the last viewer leaves, because a
stream left open holds a slot the printer needs a reboot to release.

### Verify a printer

`tools/verify_sdcp.py` in this repository runs steps (a), (b), (c) and (g) against a real
printer, prints the raw frames, and refuses to send anything outside the read-only set. It
is the acceptance instrument for this research. Run it before writing integration logic.

```
python tools/verify_sdcp.py --discover
python tools/verify_sdcp.py --host 192.168.1.209 --snapshot frame.jpg
```

Standard library only, so it needs no virtualenv. `--upload` and `--start-print` are
opt-in, and `--start-print` prints the crash warning before it sends.

`tools/test_verify_sdcp.py` proves the probe without hardware. It stands up a loopback
WebSocket server that answers Cmd 1, 512 and 258 in the printer's shape, then drives the
probe's own client against it.

```
python tools/test_verify_sdcp.py
```

It passes on Python 3.14. What it exercises, and why each one matters for the integration.

| Check | Claim it pins down |
|---|---|
| WS handshake and text framing | The envelope and the `drain`-free reader work against a real RFC 6455 peer |
| MainboardID learned from the Attributes push | Section 1.6, the id bootstrap |
| Cmd 1 returns attributes | Section 1.3 |
| Cmd 512 subscribe, then the Cmd 0 fallback when no push arrives | Section 7, the dead push scheduler |
| Status frame prefixed with a decimal length parses | Section 7, the `"123{json...}"` quirk |
| `PrintInfo` and remaining-time arithmetic | Section 1.5 |
| Cmd 258 split across an Ack frame and a data frame | Section 1.3, and it caught a real bug in the probe |
| MJPEG SOI-to-EOI extraction | Section 3 |
| Multipart body carries all six upload fields | Section 5 |
| Write commands are opt-in and parsed after argv | The safety rule in section 7 |

Two bugs the self-test caught are worth recording, because both would have shown up as
mystery failures on hardware. Cmd 258 does not deliver its file list in the Ack frame, and
the probe initially read only one frame per command. And a hard-closing HTTP peer on Windows
raises `ConnectionResetError` on a `recv` that the code assumed would return the buffered
body, so the camera reader now tolerates a missing header terminator.

---

## CONFIDENCE AND GAPS

### Confirmed against a primary artifact

- CC1 speaks SDCP over WebSocket at `ws://<host>:3030/websocket`. SDK, spec, and two clients agree.
- UDP discovery on 3000 with the literal probe `M99999`, and the reply shape. SDK, spec, pycentauri, HA integration.
- The request envelope fields and the five topic patterns. Spec, plus SDK and pycentauri implementations.
- Command codes 0, 1, 128, 129, 130, 131 from the SDK's own mapping table.
- Command codes 258, 259, 320, 321 from pycentauri, live on firmware V0.3.0-o.
- Cmd 403 payload shapes for speed, fans, temperatures and light. SDK builder plus live verification recorded by pycentauri.
- Cmd 386 and 387 and their Ack codes. Spec and OpenCentauri docs.
- The `PrintInfo.Status` enum. SDK's `sdcp_print_status_t`, quoted verbatim above.
- The `sdcp_print_ctrl_ack_t` Ack codes. Spec, cross-checked against the SDK comment.
- Upload endpoint, multipart field names, 1 MiB chunking, and the `{"code": "000000"}` success body. SDK source plus spec.
- Camera on port 3031 at `/video`, MJPEG `multipart/x-mixed-replace`. pycentauri implementation and HA integration constants.
- No authentication on CC1. pycentauri README and HA integration config schema.
- The 5-connection WebSocket cap and the `"too many client"` body. pycentauri, from a direct probe.
- The unknown-command daemon crash. pycentauri, reproduced via SSH process inspection on stock and OpenCentauri.

### Contradictions between sources, unresolved

- **Upload port.** Spec and OpenCentauri docs say 3030. The SDK's endpoint builder and pycentauri's `UPLOAD_PORT` land on 80. Working code uses 80. Try 80, fall back to 3030.
- **TimeStamp unit.** Spec says seconds, SDK and pycentauri send milliseconds, OpenCentauri's notes say seconds. Recommend milliseconds.
- **Tick unit.** Spec says milliseconds, OpenCentauri's sample reads as seconds, pycentauri tolerates both.
- **Connection limit.** pycentauri measured 5 on CC1. The HA README says 4 for modern Elegoo printers. Different model coverage is the likely explanation. Assume the lower number is safe.
- **Whether Cmd 386 is required before reading the camera.** pycentauri reads frames without it. The HA integration always enables first and ref-counts. Test on hardware.
- **Whether Cmd 128 is safe on CC1.** Elegoo ships it enabled. The HA integration says it crashed the printer and omits it. This is the most consequential open item.
- **The HA CC2 document's CC1 rows.** It calls CC1 transport "TCP" and CC1 max clients "Unlimited". Both contradict all other sources.

### Not confirmed

- **Any HTTP REST endpoint other than `/`, `/uploadFile/upload`, `/downloadFile<path>` and the camera.** The `/sdcp/status/<id>`, `/sdcp/info`, `/sdcp/attributes`, `POST /sdcp/request/<id>`, `/sdcp/file` and `/sdcp/files` routes in the brief appear in no fetched source. The `sdcp/...` strings are WebSocket topics. UNCONFIRMED, and probably a category error in the brief.
- **Thumbnail endpoint on CC1.** A thumbnail URL is delivered inside Cmd 321's detail records. No direct endpoint was found.
- **Maximum total upload size.** Only the 1 MiB per-chunk cap is documented. Use `Attributes.RemainingMemory` as the guard.
- **`ZAxisPosition` and `RemainTime` as CC1 status fields.** Neither appears in any fetched source. Z comes from `CurrenCoord`. Remaining time is computed.
- **Whether stock V1.1.46 has the dead push scheduler.** Verified only on OpenCentauri V0.3.0-o.
- **HTTP or WebSocket video on CC1.** CC1 is MJPEG only in every source read. `WebRTC`, `H.264 in a WebSocket` and a proprietary `/video` variant beyond the MJPEG path are all absent.
- **CC1 access-code firmware.** No source shows an access code ever being added to CC1 SDCP.
- **CC2 details beyond what is quoted.** Not verified against hardware for this report.
- **`caesar1111/elegoo-link`, `CentauriLink/Centauri-Link`, `cbd-taylor/elegoo-*`, `mistergibson/...`, `knoopx/...`, Node-RED, openHAB and Homey.** None located. The first and second are cited second-hand by pycentauri; both returned 404 when fetched. Every other name returned nothing.
- **`terriblyvile/HA-Centauri-Carbon` source.** Repository page fetched, source files not read.
