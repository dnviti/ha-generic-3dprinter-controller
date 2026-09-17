# Protocol adapter layer design

A decision document for the adapter layer of the `generic_3dprinter` Home Assistant integration. No implementation.

Evidence base. I read the reference implementation in `ha-generic-video-proxy`: `models.py`, `const.py`, `runtime.py`, `hub.py`, `config_flow.py`, `tests/test_models.py`. I read `protocol-research.md` in this repository, including the capability matrix, the Klipper, OctoPrint, PrusaLink, Bambu, Duet, Anycubic, and Creality sections, the GAPS list, and the adapter interface implications. I also read the live-protocol notes for the Elegoo Centauri Carbon in [pycentauri/docs/PROTOCOL.md](https://raw.githubusercontent.com/bjan/pycentauri/main/docs/PROTOCOL.md), which corrects one premise in the brief. That correction shapes section 7.

## DECISION

| # | Point | Decision |
| --- | --- | --- |
| 1 | Adapter interface | One `Protocol` ABC with 8 members: `async_setup`, `async_teardown`, `async_read`, `async_send`, `async_list_files`, `async_upload_file`, `async_camera_frame`, plus a `capabilities` property. No `async_connect`, no per-protocol command methods, no `web_ui` accessor. |
| 2 | Capability model | A `frozenset[Capability]` computed per read and carried on the snapshot as `capabilities`. Consumers query `Capability.X in snapshot.capabilities`. Zero `if protocol == ...` branches outside adapter modules. |
| 3 | Normalised snapshot | `PrinterSnapshot`, frozen and slots, JSON-safe primitives plus three small value objects (`Temps`, `Fans`, `Axis`). Every measured field is `Optional` with `None` for unknown. A `@property` derives `idle` from `print_state` so the two cannot disagree. |
| 4 | Command vocabulary | One `Command` StrEnum of 14 members and one `async_send(cmd, **params)` union method. The `Command -> Capability` requirement is declared once in a lookup table, and the shared base class rejects an unsupported command before any adapter code runs. SDCP `403` collapses to four `Command` members inside one adapter. |
| 5 | Registration | One `ADAPTERS: dict[ProtocolId, AdapterRegistration]` in `adapters/__init__.py`. A new protocol is one new module plus one dict entry. The config flow, coordinator, entities, and card read the registry and never name a protocol. |
| 6 | Two transports | A web-only printer is an ordinary adapter with an empty capability set, `WEB_UI`, and a `read` that reports `connected` from a single HTTP GET. The coordinator has no second code path; the web adapter opts out of push by not implementing `async_subscribe`. |
| 7 | Safety gate | `ProtocolRegistration.unsafe: tuple[UnsafeFeature, ...]`, each naming a capability it gates and a reason. `unsafe_enabled` in the config entry opts in. The base class subtracts un-enabled capabilities and rejects the command at the boundary. |

The load-bearing premise from the brief is partly wrong. Command 128 is on the confirmed-working list for the Centauri Carbon, verified live, and the daemon crash came from unknown command codes and from confirmed codes sent with the wrong payload shape. I keep the gate, because unknown codes are still a live hazard, and I widen it from "code 128" to "any command or payload shape not on the confirmed list". Details in section 7.

## 1. The adapter interface

One abstract base class per protocol, one instance per config entry, stored in the entry's runtime object next to the hub, following the shape `ProxyRuntime` uses in `runtime.py`.

```python
# protocols.py
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, NewType

# ------------------------------------------------------------------ config
# PrinterConfig references ProtocolId, UnsafeFeature, and Capability, which the
# registry section defines. In the package they live in const.py and protocols.py
# and this module imports them, so there is no cycle.

CREDENTIAL_KEYS: Final[tuple[str, ...]] = ("password", "token", "access_code", "api_key")


@dataclass(frozen=True, slots=True)
class PrinterConfig:
    """Immutable, validated description of one printer.

    Built only by parse_config in the config flow, exactly as ProxyConfig is
    built by parse_config in the reference integration.
    """

    name: str
    protocol: ProtocolId
    host: str
    port: int | None = None
    tls: bool = False
    verify_ssl: bool = True
    scan_interval: int = 30
    web_url: str | None = None
    camera_port: int | None = None
    #: Values, not keys. Never serialised into diagnostics.
    credentials: Mapping[str, str] = field(default_factory=dict)
    #: Ids of UnsafeFeature the user explicitly opted into.
    unsafe_enabled: frozenset[str] = frozenset()

    @property
    def base_url(self) -> str: ...

    @property
    def redacted_url(self) -> str:
        """Return the URL with credentials stripped, for diagnostics."""
        ...

    def with_overrides(self, data: Mapping[str, Any]) -> PrinterConfig:
        """Return a copy with data applied, for the options flow."""
        ...


def parse_config(
    data: Mapping[str, Any], *, existing: PrinterConfig | None = None
) -> PrinterConfig:
    """Validate raw config entry data and options into a PrinterConfig.

    Raises ConfigError. Rejects a protocol id not in ADAPTERS, a field the
    protocol does not declare, and an unsafe id the protocol does not declare.
    """
    ...


# ------------------------------------------------------------- branded scalars

Celsius = NewType("Celsius", float)
Seconds = NewType("Seconds", float)
Percent = NewType("Percent", float)  # 100.0 means "100 percent"
Millimetres = NewType("Millimetres", float)

# --------------------------------------------------------------- error surface


class ProtocolError(Exception):
    """Base class for every failure raised across the adapter boundary."""


class UnreachableError(ProtocolError):
    """The device did not answer."""


class AuthError(ProtocolError):
    """The device answered and refused the credential."""


class ProtocolShapeError(ProtocolError):
    """The device answered with data this adapter cannot parse."""


class CommandRejectedError(ProtocolError):
    """The device accepted the request and refused the command."""

    def __init__(
        self, message: str, *, code: int | str | None = None, reason: str | None = None
    ) -> None: ...


class UnsupportedCommandError(ProtocolError):
    """The adapter does not support this command on this printer."""

    def __init__(self, command: Command, missing: Capability | None = None) -> None: ...


class UnsafeCommandError(ProtocolError):
    """The command is gated behind an opt-in that this entry did not grant."""

    def __init__(self, feature: UnsafeFeature) -> None: ...


# ------------------------------------------------------------------ interface


class Protocol(ABC):
    """Transport adapter for one printer. One instance per config entry."""

    def __init__(self, config: PrinterConfig, session: aiohttp.ClientSession) -> None: ...

    @property
    def capabilities(self) -> frozenset[Capability]:
        """Capabilities this printer grants right now, after every gate is applied."""
        ...

    @abstractmethod
    async def async_setup(self) -> None:
        """Open whatever this protocol needs and verify the credential once.

        Idempotent. Raises UnreachableError or AuthError. Called at entry setup
        and again after a failed read, never per poll.
        """
        ...

    @abstractmethod
    async def async_teardown(self) -> None:
        """Close sockets, brokers, and sessions. Idempotent."""
        ...

    @abstractmethod
    async def async_read(self) -> PrinterSnapshot:
        """Return one complete snapshot of printer state.

        Raises UnreachableError or AuthError. Never raises for a field the
        protocol cannot express: that field is None in the returned snapshot.
        """
        ...

    def async_subscribe(self) -> AsyncIterator[PrinterSnapshot] | None:
        """Return a push iterator, or None when this adapter can only be polled.

        The default returns None. Adapters with a wire push (Moonraker
        notify_status_update, SDCP status topic, Creality port 9999, MQTT) override it.
        """
        return None

    @abstractmethod
    async def async_send(self, command: Command, **params: Any) -> None:
        """Ask the printer to do something.

        The concrete base class validates capability and parameters before
        dispatch, so an adapter body handles only its own happy path.
        """
        ...

    @abstractmethod
    async def async_list_files(self) -> Sequence[FileEntry]:
        """List stored print jobs."""
        ...

    @abstractmethod
    async def async_upload_file(
        self,
        name: str,
        stream: AsyncIterator[bytes],
        *,
        size: int | None = None,
    ) -> FileEntry:
        """Upload one G-code file and return its stored entry."""
        ...

    async def async_camera_frame(self) -> bytes:
        """Return one complete JPEG frame.

        Raises UnreachableError when the camera cannot be reached. Adapters
        without a camera are never called, because CAMERA is absent from
        capabilities.
        """
        raise UnreachableError("this protocol has no camera")
```

Three members I considered and cut, each with the reason in section "WHAT I DELETED AND WHY": `async_connect`/`async_disconnect` as a separate pair, `async_snapshot()` for the camera, and `web_ui_url()`.

Why each surviving method exists:

`async_setup` and `async_teardown` are separate from `async_read` because three protocols need a one-time handshake that is wasted work per poll. Anycubic does a signed AES exchange to obtain MQTT credentials. Moonraker needs `GET /access/info` to learn whether a key is required at all. SDCP needs `Cmd 512` subscribe, and it must seed `MainboardID` from discovery because the printer does not push attributes while paused or errored. Authentication failures also need to be distinguishable from unreachable, because the config flow reports different errors.

`async_read` is async and mandatory for every adapter, including pull-less ones. I considered a synchronous `read`, since push adapters already hold the state. I rejected it for two reasons. Bambu and Anycubic have partial state that needs a request on demand. More importantly, a synchronous `read` invites an adapter to hide I/O behind a property accessor, and then the coordinator cannot time it out.

`async_subscribe` is not abstract and defaults to `None`. Without it, the coordinator has to poll every printer, and the two protocols that push well (Moonraker, Creality port 9999) pay a request per interval for nothing. With it, the coordinator still has a poll path, so the web-only adapter and PrusaLink need no special code.

`async_send` takes the normalised vocabulary rather than a raw protocol string. A raw-string escape hatch (`async_send_raw`) would let the entity layer or an automation bypass the capability gate, which defeats point 2 and point 4. Automations that need vendor-specific G-code get it through dedicated `Command` members, not a passthrough.

`async_list_files` and `async_upload_file` exist because the feature list in the brief requires them and because the card's file picker and the start-print flow need them. I considered making them optional with `NotImplementedError` defaults, so a printer without file access would not have to define them. I rejected that: the capability set already says whether `FILE_LIST` and `FILE_UPLOAD` are present, and a method that exists only to raise gives the reader no information the capability does not.

`async_camera_frame` returns JPEG bytes rather than a URL. A URL would force the browser to reach the printer directly, which breaks the moment HA is the only host on the printer's network, and it leaks the printer's credentials into the browser. It also cannot represent the two stream shapes in this fleet. The Centauri Carbon serves `multipart/x-mixed-replace` on port 3031 with a tiny, leaky connection pool, so the integration must hold exactly one upstream connection and fan frames out. That is precisely what `StreamHub` already does in `hub.py`, so the adapter feeds bytes and the hub fans them out. RTSP printers get frames the same way.

## 2. The capability model

A StrEnum member per thing a printer can do, and one frozen set per printer that says which are granted right now.

```python
class Capability(StrEnum):
    """One thing the entity layer and the card may offer."""

    START_PRINT = "start_print"
    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"
    SET_HOTEND_TEMP = "set_hotend_temp"
    SET_BED_TEMP = "set_bed_temp"
    SET_CHAMBER_TEMP = "set_chamber_temp"
    SET_FAN_SPEED = "set_fan_speed"
    SET_SPEED = "set_speed"
    SET_FLOW = "set_flow"
    SET_LIGHT = "set_light"
    HOME = "home"
    JOG = "jog"
    FILE_LIST = "file_list"
    FILE_UPLOAD = "file_upload"
    FILE_DELETE = "file_delete"
    CAMERA = "camera"
    WEB_UI = "web_ui"
```

The set lives on the snapshot, not only on the adapter, and not in the config entry.

Why on the snapshot. Two capabilities in this fleet are firmware state, not model properties. Bambu gates every `print.*` write behind the `fun` bit `0x20000000`, and whether that bit is set changes with a firmware update. Anycubic ignores temperature, fan, and speed writes while idle and has no preheat command at all, so several settings are valid only mid-job. A capability set computed once at setup would claim a control works for the rest of the entry's life. Computing it per read costs nothing and is always true.

Why a `frozenset` and not a feature table of booleans. A table forces every consumer to know every member name and to treat an absent key as false. A set makes the query one expression and makes an added capability a one-line change in the adapter. The `SOURCE_TYPES` tuple in the reference `const.py` is the same idea at config level, and the eight `if self._config.source_type == ...` checks scattered through `hub.py` are the failure mode this avoids.

How a consumer queries it:

```python
# entity layer, sensor.py
if Capability.SET_HOTEND_TEMP in runtime.snapshot.capabilities:
    entities.append(PrinterHotendNumber(runtime))
```

```python
# entity layer, button.py
class PrinterButton(CoordinatorEntity[PrinterCoordinator], ButtonEntity):
    """One button per action this printer supports."""

    def __init__(self, runtime: PrinterRuntime, command: Command) -> None:
        self._command = command

    @property
    def available(self) -> bool:
        return command_capability(self._command) in self.coordinator.data.capabilities
```

```python
# card / websocket status payload
def status(self) -> dict[str, object]:
    return {
        "entry_id": self.entry_id,
        "name": self.config.name,
        "protocol": self.config.protocol.value,
        "capabilities": sorted(c.value for c in self.snapshot.capabilities),
        "printer": self.snapshot.as_dict(),
    }
```

The card reads one array and draws a widget per capability present. It never learns the protocol id beyond displaying it. A "pause" control appears for a Moonraker printer and not for a PrusaLink printer that reports `ATTENTION`, without a single protocol comparison on either side of the boundary.

One consequence worth stating plainly. Capability granularity is per action, not per parameter. `SET_FAN_SPEED` cannot say "model fan yes, chamber fan no". If that matters later, the fix is separate `Capability` members, not an optional argument map, because an argument map puts the branch back in the consumer.

## 3. The normalised snapshot

```python
# models.py


@dataclass(frozen=True, slots=True)
class Temps:
    """Current and target temperature for one heater."""

    current: Celsius | None = None
    target: Celsius | None = None


@dataclass(frozen=True, slots=True)
class Fans:
    """Fan duty per channel, as a percentage."""

    model: Percent | None = None
    auxiliary: Percent | None = None
    chamber: Percent | None = None
    hotend: Percent | None = None
    controller: Percent | None = None


@dataclass(frozen=True, slots=True)
class Axis:
    """Toolhead position in millimetres."""

    x: Millimetres | None = None
    y: Millimetres | None = None
    z: Millimetres | None = None


@dataclass(frozen=True, slots=True)
class FileEntry:
    """One stored G-code file."""

    name: str
    path: str
    size: int | None = None
    modified: datetime | None = None
    thumbnail: str | None = None


class LightChannel(StrEnum):
    """One independently switchable light."""

    CHAMBER = "chamber"
    STATUS = "status"
    RGB = "rgb"


class PrintState(StrEnum):
    """What the machine is doing, normalised across protocols."""

    UNKNOWN = "unknown"
    IDLE = "idle"
    PREPARING = "preparing"
    PRINTING = "printing"
    PAUSED = "paused"
    FINISHED = "finished"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class PrinterSnapshot:
    """One complete, normalised view of a printer at one instant."""

    # --- always known, because the coordinator or the adapter set them
    protocol: ProtocolId
    connected: bool
    capabilities: frozenset[Capability]
    print_state: PrintState = PrintState.UNKNOWN
    online: bool = False

    # --- job
    progress: Percent | None = None
    current_layer: int | None = None
    total_layers: int | None = None
    remaining: Seconds | None = None
    elapsed: Seconds | None = None
    filename: str | None = None
    job_id: str | None = None
    speed_factor: Percent | None = None
    flow_factor: Percent | None = None

    # --- temperatures
    hotend: Temps = Temps()
    bed: Temps = Temps()
    chamber: Temps = Temps()

    # --- mechanism
    fans: Fans = Fans()
    position: Axis | None = None
    homed_axes: frozenset[str] = frozenset()
    lights: frozenset[LightChannel] = frozenset()

    # --- camera
    camera: bool = False

    # --- provenance
    model: str | None = None
    firmware: str | None = None
    serial: str | None = None
    errors: tuple[str, ...] = ()

    @property
    def idle(self) -> bool:
        """Derived. Never stored, so it cannot contradict print_state."""
        return self.print_state is PrintState.IDLE

    @property
    def can_print(self) -> bool:
        return Capability.START_PRINT in self.capabilities

    def as_dict(self) -> dict[str, object]:
        """Return a JSON-safe document for the card and diagnostics."""
        ...
```

Required versus optional. Four fields are required because no adapter can fail to know them: `protocol` is an enum the adapter owns, `connected` is the result of its own socket, `capabilities` is computed from its own table, and `print_state` defaults to `UNKNOWN` rather than being required. Everything the printer reports is optional with a `None` or empty default. The reason is the capability matrix: layer counts exist on Moonraker, Anycubic, Bambu, Creality, and Duet but not on OctoPrint or PrusaLink, and the research note says explicitly not to make `current_layer` required. Chamber temperature is absent from PrusaLink entirely. PrusaLink reports fan speeds in RPM while every other protocol reports duty, so `Fans` carries neither for PrusaLink rather than a value in the wrong unit. Making any of those required would force adapters to invent a value, and an invented value is worse than an absent one.

How "unknown" is represented: `None`, everywhere, with no sentinel. A sentinel such as `-1` for temperature or `0` for progress is indistinguishable from a real reading. `0 °C` is a real chamber temperature. `0 %` is a real fan setting. This is the same argument that makes `ProxyConfig.snapshot_url: str | None` in the reference `models.py` correct rather than `snapshot_url: str = ""`.

How a protocol that cannot express a field reports it. It leaves the field at its default. The adapter does not fabricate, does not compute a stand-in, and does not raise. PrusaLink's `read` returns `current_layer=None` and `chamber=Temps()`. The consumer sees `None` and hides the row. A protocol that cannot express a field but whose capability set claims a related control is a bug in the adapter table, and the model makes it visible: a `SET_CHAMBER_TEMP` capability with `chamber.current is None` is a contradiction a test can catch.

Where "illegal states unrepresentable" applies and where it does not. Three places earn it.

`idle` is derived from `print_state`, so the two cannot disagree. A stored `is_idle: bool` next to a `print_state` is the `{completed, completedAt}` anti-pattern from type system discipline.

`PrintState` is a closed enum. Creality has no authoritative status field and its integration derives status from four raw signals into its own ten-member set. That derivation belongs in the Creality adapter, and it must land on one of the eight members. A new protocol that needs a ninth state is a decision to make deliberately, not a string to smuggle through.

`position: Axis | None` separates "no position data" from "position is 0,0,0". PrusaLink omits `axis_x` and `axis_y` on some models, and Bambu has no coordinate key at all.

Where weaker types are the right call. `PrinterSnapshot` does not enforce "progress implies printing". It could, by splitting into a printing variant and an idle variant, and I rejected that. The entity layer already hides a progress row when `progress is None`, HA's coordinator update is per-poll, and a sum type here would double every constructor call in every adapter for no guard a test cannot provide more cheaply. Precision that no operation needs costs reuse and buys nothing, which is the guideline from type system discipline about strengthening a type only where partiality appears.

The `Seconds` and `Percent` brands are the one place I do want the compiler's help. Bambu reports `mc_remaining_time` in minutes and Anycubic reports `remain_time` in minutes, while Moonraker, OctoPrint, Creality, and Duet report seconds. That mistake is invisible in a `float` and is exactly the class of bug a newtype catches at the assignment. Both brands are `NewType`, so at runtime they are floats and `as_dict()` serialises them unchanged.

## 4. The command vocabulary

```python
class Command(StrEnum):
    """The normalised command vocabulary. Adapters implement a subset."""

    START_PRINT = "start_print"
    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"
    SET_HOTEND_TEMP = "set_hotend_temp"
    SET_BED_TEMP = "set_bed_temp"
    SET_CHAMBER_TEMP = "set_chamber_temp"
    SET_FAN_SPEED = "set_fan_speed"
    SET_SPEED = "set_speed"
    SET_FLOW = "set_flow"
    SET_LIGHT = "set_light"
    HOME = "home"
    JOG = "jog"
    DELETE_FILE = "delete_file"


COMMAND_CAPABILITY: Final[Mapping[Command, Capability]] = MappingProxyType(
    {
        Command.START_PRINT: Capability.START_PRINT,
        Command.PAUSE: Capability.PAUSE,
        Command.RESUME: Capability.RESUME,
        Command.STOP: Capability.STOP,
        Command.SET_HOTEND_TEMP: Capability.SET_HOTEND_TEMP,
        Command.SET_BED_TEMP: Capability.SET_BED_TEMP,
        Command.SET_CHAMBER_TEMP: Capability.SET_CHAMBER_TEMP,
        Command.SET_FAN_SPEED: Capability.SET_FAN_SPEED,
        Command.SET_SPEED: Capability.SET_SPEED,
        Command.SET_FLOW: Capability.SET_FLOW,
        Command.SET_LIGHT: Capability.SET_LIGHT,
        Command.HOME: Capability.HOME,
        Command.JOG: Capability.JOG,
        Command.DELETE_FILE: Capability.FILE_DELETE,
    }
)


def command_capability(command: Command) -> Capability:
    """Return the capability a command requires. One source of truth."""
    return COMMAND_CAPABILITY[command]
```

Rejection happens at the boundary, before the adapter's own code runs. The shared concrete base does the guard once, so an adapter body contains only its happy path.

```python
class BaseProtocol(Protocol):
    """Concrete shared behaviour. Vendor adapters subclass this."""

    def __init__(
        self,
        config: PrinterConfig,
        session: aiohttp.ClientSession,
        *,
        granted: frozenset[Capability],
    ) -> None: ...

    @property
    def capabilities(self) -> frozenset[Capability]:
        """The granted set, minus anything a runtime gate is withholding."""
        return self._capabilities

    async def async_send(self, command: Command, **params: Any) -> None:
        required = command_capability(command)
        if required not in self.capabilities:
            raise UnsupportedCommandError(command, required)
        validated = validate_params(command, params)   # raises ProtocolError
        await self._async_dispatch(command, validated)

    @abstractmethod
    async def _async_dispatch(self, command: Command, params: Mapping[str, Any]) -> None:
        """Adapter-specific happy path. Only reached once the guards passed."""
        ...
```

An unsupported command never reaches a device. Three distinct failures stay distinguishable: `UnsupportedCommandError` for "this printer cannot", `UnsafeCommandError` for "this printer can, and you have not opted in", `CommandRejectedError` for "the device said no". The first two are ours; the third carries the device's own code. The OctoPrint and Moonraker integrations both surface the device's HTTP status; SDCP surfaces `Data.Data.Ack != 0`; Duet surfaces `{"err": N}`; a Bambu `print.*` publish surfaces `result:"failed"` with a reason string. That last one is why `CommandRejectedError` carries both `code` and `reason`.

The SDCP `403` question. `403 CHANGE_PRINT_PARAMS` is one command code whose payload shape the firmware dispatches on which keys are present, and the four shapes are print speed (`PrintSpeedPct`), fan speeds (`TargetFanSpeed{ModelFan, BoxFan, AuxiliaryFan}`), temperatures (`TempTargetNozzle`, `TempTargetHotbed`, `TempTargetBox`), and light (`LightStatus`).

I choose **several normalised methods, one command code inside the adapter**. Normalised: `SET_SPEED`, `SET_FAN_SPEED`, `SET_HOTEND_TEMP`, `SET_BED_TEMP`, `SET_CHAMBER_TEMP`, `SET_LIGHT`. Inside `ElegooCentauriProtocol._async_dispatch`, a mapping from `Command` to a payload-builder collapses onto the literal `403`. The firmware can also be sent only the keys that changed, and the live notes confirm a partial payload such as `{"TargetFanSpeed": {"ModelFan": 50}}` leaves the other fans alone, so the builder per command is a natural fit.

Rejected alternative: one `async_send(Command.SET_PRINT_PARAMS, payload=...)` with a union payload. It moves the discriminator into the caller, so the entity layer would build protocol-shaped dicts, the capability set could not distinguish "can set speed" from "can set light", and an unsupported sub-variant would fail deep inside an adapter instead of at the boundary. That is the branch-through-the-stack shape this whole document exists to avoid.

Rejected alternative: four separate command codes as separate `Command` members. The `Command` vocabulary is a statement about printers, not about Elegoo. `START_PRINT` means the same thing to a Moonraker printer and to a Centauri Carbon, and a vocabulary that grows a member per vendor wire shape stops being normalised. The vendor divergence stays one layer down, where it belongs.

The same shape applies elsewhere. Duet cancel is `M25` then `M0` and its emergency stop is `M112` which needs a controller reset afterwards. Anycubic stop is `print`/`stop` and its start is `print`/`start` with a payload only Rinkhals documents. Bambu temperatures are raw G-code through `gcode_line` while Bambu's home is `G28`. None of that reaches the vocabulary.

## 5. Registration

```python
# adapters/__init__.py


@dataclass(frozen=True, slots=True)
class UnsafeFeature:
    """A command or payload shape the integration will not send unless asked."""

    id: str
    label: str
    reason: str
    gates: frozenset[Capability]
    evidence: str


class ProtocolId(StrEnum):
    """Every protocol this integration speaks."""

    MOONRAKER = "moonraker"
    OCTOPRINT = "octoprint"
    PRUSALINK = "prusalink"
    BAMBU = "bambu"
    DUET = "duet"
    CREALITY_WS = "creality_ws"
    ANYCUBIC_MQTT = "anycubic_mqtt"
    ANYCUBIC_PHOTON = "anycubic_photon"
    SDCP_CC1 = "sdcp_cc1"
    WEB_ONLY = "web_only"


@dataclass(frozen=True, slots=True)
class AdapterRegistration:
    """Everything the rest of the integration needs to know about a protocol."""

    id: ProtocolId
    label: str
    adapter: type[Protocol]
    #: Capabilities the wire protocol can express, before any gate.
    capabilities: frozenset[Capability]
    #: Config keys this protocol needs, beyond name/host.
    fields: tuple[str, ...]
    #: Credential keys this protocol needs.
    credentials: tuple[str, ...]
    #: Default TCP ports, in the order the config flow should try them.
    ports: tuple[int, ...]
    #: Commands and payload shapes that need an explicit opt-in.
    unsafe: tuple[UnsafeFeature, ...] = ()
    #: What is verified and what is inferred. Shown in diagnostics.
    evidence: Mapping[str, str] = field(default_factory=dict)


ADAPTERS: Final[Mapping[ProtocolId, AdapterRegistration]] = MappingProxyType(
    {
        ProtocolId.MOONRAKER: AdapterRegistration(
            id=ProtocolId.MOONRAKER,
            label="Klipper via Moonraker",
            adapter=MoonrakerProtocol,
            capabilities=frozenset(
                {
                    Capability.START_PRINT, Capability.PAUSE, Capability.RESUME,
                    Capability.STOP, Capability.SET_HOTEND_TEMP, Capability.SET_BED_TEMP,
                    Capability.SET_CHAMBER_TEMP, Capability.SET_FAN_SPEED,
                    Capability.SET_SPEED, Capability.SET_FLOW, Capability.HOME,
                    Capability.JOG, Capability.FILE_LIST, Capability.FILE_UPLOAD,
                    Capability.FILE_DELETE, Capability.CAMERA,
                }
            ),
            fields=("port", "tls", "path"),
            credentials=("api_key",),
            ports=(7125,),
        ),
        ProtocolId.SDCP_CC1: AdapterRegistration(
            id=ProtocolId.SDCP_CC1,
            label="Elegoo SDCP (Centauri Carbon)",
            adapter=ElegooSdcpProtocol,
            # Cmd 258/259/320/321 verified safe mid-print; 403 variants verified live.
            # Cmd 401 HOME_AXES and 402 MOVE_AXES are commented out in Elegoo's SDK
            # and stay out of the set until someone confirms them.
            capabilities=frozenset(
                {
                    Capability.PAUSE, Capability.RESUME, Capability.STOP,
                    Capability.SET_HOTEND_TEMP, Capability.SET_BED_TEMP,
                    Capability.SET_CHAMBER_TEMP, Capability.SET_FAN_SPEED,
                    Capability.SET_SPEED, Capability.SET_LIGHT,
                    Capability.FILE_LIST, Capability.FILE_UPLOAD, Capability.FILE_DELETE,
                    Capability.CAMERA, Capability.WEB_UI,
                }
            ),
            fields=("port", "camera_port", "poll_period"),
            credentials=(),
            ports=(3030,),
            unsafe=(
                UnsafeFeature(
                    id="sdcp_unlisted_command",
                    label="Send an SDCP command that is not on the confirmed list",
                    reason=(
                        "Unrecognised SDCP command codes, and confirmed codes sent with an "
                        "unexpected payload shape, crash the printer's app daemon. On this "
                        "device app is the whole host firmware, including the motion stack, "
                        "so an active print is destroyed and a power cycle is required."
                    ),
                    gates=frozenset({Capability.START_PRINT}),
                    evidence="pycentauri/docs/PROTOCOL.md, unknown-Cmd crash mode",
                ),
            ),
        ),
        ProtocolId.WEB_ONLY: AdapterRegistration(
            id=ProtocolId.WEB_ONLY,
            label="Web page only (no control API)",
            adapter=WebOnlyProtocol,
            capabilities=frozenset({Capability.WEB_UI}),
            fields=("web_url", "verify_ssl"),
            credentials=("username", "password"),
            ports=(80, 443),
        ),
    }
)
```

Lookup is one dict access:

```python
def get_registration(protocol: ProtocolId) -> AdapterRegistration:
    """Return the registration for a protocol."""
    return ADAPTERS[protocol]


def build_adapter(config: PrinterConfig, session: aiohttp.ClientSession) -> Protocol:
    """Construct the adapter for a parsed config. The only construction site."""
    registration = get_registration(config.protocol)
    return registration.adapter(
        config,
        session,
        granted=granted_capabilities(registration, config.unsafe_enabled),
    )
```

Adding a protocol means adding `adapters/<name>.py` and one dict entry. No coordinator, entity, or card edit, because all three read `capabilities` and none of them names a `ProtocolId`. The config flow builds its protocol menu from `ADAPTERS` and its per-protocol schema from `registration.fields` and `registration.credentials`, so it needs no edit either. That is the test of the registry: a new entry should be the whole diff outside the new module.

## 6. The two transports

A web-only printer is an ordinary adapter. `WebOnlyProtocol` grants `{Capability.WEB_UI}`, implements `async_read` as one HTTP `GET` of `web_url` with `verify_ssl` and optional basic auth, and implements the four transport methods as bounded stubs that raise `UnsupportedCommandError` or `ProtocolError`. It does not implement `async_subscribe`, so it is polled.

Rejected alternative: a separate runtime branch such as `WebOnlyRuntime`. It duplicates the config entry plumbing, the device registry, the diagnostics, and the status document, and it makes every consumer learn that two kinds of runtime exist. It also breaks the invariant that the coordinator has one update path.

Rejected alternative: a discriminated union `Runtime = ApiRuntime | WebOnlyRuntime` at the type level. It is honest about the difference and it is more work than the difference is worth, because the difference is small and the union infects every function that touches a runtime. The capability set already carries the distinction, and the `None` fields already carry the absence.

How the coordinator handles an unpollable printer: it polls it. There is no unpollable printer in this fleet in the sense that matters. A web-only device answers an HTTP GET on its web port, and that answer is a real reachability signal, so `connected` is meaningful. What such a printer cannot do is restore printer state, and the snapshot says so honestly.

```python
async def _async_read_web_only(self) -> PrinterSnapshot:
    """A web page is reachability, not printer state."""
    try:
        async with self._session.get(self._web_url, timeout=...) as response:
            connected = response.status < 500
    except (aiohttp.ClientError, TimeoutError):
        connected = False
    return PrinterSnapshot(
        protocol=ProtocolId.WEB_ONLY,
        connected=connected,
        capabilities=frozenset({Capability.WEB_UI}),
        print_state=PrintState.UNKNOWN,
        online=connected,
    )
```

Precisely which fields are absent for a web-only printer, and why: `progress`, `current_layer`, `total_layers`, `remaining`, `elapsed`, `filename`, `job_id`, `speed_factor`, `flow_factor` are all `None`, because there is no job API. `hotend`, `bed`, and `chamber` stay at the all-`None` default, because there is no temperature surface. `fans` stays all `None`. `position` is `None` and `homed_axes` is empty. `lights` is empty. `camera` is `False`. `model`, `firmware`, and `serial` are `None`, because the page does not report them in any structured form. `print_state` is `UNKNOWN`, never `IDLE`, because a web page cannot distinguish "idle" from "printing" and claiming `IDLE` would be a fabrication that a user reads as truth.

The entity layer then creates one entity for such a printer, a link to the web UI, and nothing else. The card draws one button. Neither needed a protocol check to get there.

One honest caveat. `online=True` with `print_state=UNKNOWN` is a mildly awkward pair, and a card that renders "online" for a printer it cannot see into is telling the user less than the user wants. I would rather ship the honest pair and label it "reachable, no API" than manufacture a state.

## 7. Safety gate for unverified commands

The gate is three pieces of data and one subtraction. No `if protocol == ...` and no `if firmware == ...` anywhere.

Piece one is `UnsafeFeature` in the registration, as declared in section 5. It names the capabilities it gates and the reason in plain words.

Piece two is the opt-in in the config entry: `unsafe_enabled: frozenset[str]` on `PrinterConfig`, empty by default, saved by the options flow. It mirrors the `ProxyConfig` house pattern exactly: parse at the boundary into a frozen validated model, then trust it.

Piece three is the subtraction, in one function called from `build_adapter`:

```python
def granted_capabilities(
    registration: AdapterRegistration,
    unsafe_enabled: frozenset[str],
) -> frozenset[Capability]:
    """Subtract every gated capability whose opt-in the entry did not grant."""
    granted = set(registration.capabilities)
    for feature in registration.unsafe:
        if feature.id not in unsafe_enabled:
            granted -= feature.gates
    return frozenset(granted)
```

The config and options flow needs no `vol.Schema` branch per protocol. It iterates the registration's own unsafe list:

```python
def unsafe_schema(
    registration: AdapterRegistration, current: frozenset[str]
) -> dict[Any, Any]:
    """One boolean per declared hazard, in the protocol's own words."""
    return {
        vol.Optional(feature.id, default=feature.id in current): selector.BooleanSelector()
        for feature in registration.unsafe
    }
```

The refusal path is already in `BaseProtocol.async_send`. A gated capability is not in `self.capabilities`, so the send raises `UnsupportedCommandError` before the adapter body runs. To keep the message useful and to distinguish "cannot" from "not allowed", the base class checks the registration's unsafe list first and raises `UnsafeCommandError` with the feature's own `label` and `reason`, which HA renders as the service call error. The user sees the reason, not a bare "unsupported".

Now the correction that matters. The brief states that command 128 crashed this exact model's firmware. The live notes contradict it. `128 START_PRINT` is on the confirmed-working list with `Ack=0`, and the daemon crash reported in those notes traced to two other causes: genuinely unrecognised command codes sent repeatedly, and confirmed codes sent with an empty or wrong payload. `258`, `320`, and `321` were listed as crashing until the correct payloads (`{"Url": "/local"}` and `{}`) were matched to what the printer's own web UI sends, after which all three were stable. The notes also establish that a wrong payload shape trips the same crash path as an unknown code.

Three consequences for the design, all of which make the gate more useful rather than less.

First, the gate should not be keyed on a command code alone. `UnsafeFeature.gates` is a capability set and `UnsafeFeature.evidence` is a string, so a future feature can gate a payload shape without a schema change. I would add a fourth field later only if a real case needs it.

Second, the probe discipline is itself a gate, and it is the one the brief actually needs. No unconfirmed command may be sent while `print_state` is `PRINTING` or `PAUSED`. That belongs in `validate_params` or in `async_send` as a precondition on the gated path, and its reason string should say that recovery is a power cycle.

Third, `START_PRINT` on SDCP stays gated by default anyway, and now for a defensible reason. Its confirmed payload includes `Filename`, `StartLayer`, `Calibration_switch`, `PrintPlatformType`, `Tlp_Switch`, and `slot_map`, and the notes record that payload mismatch is what kills the daemon. The integration would be constructing that payload itself, so the risk is real even though the code is confirmed. The capability set above therefore omits `START_PRINT` from `SDCP_CC1`, and the `UnsafeFeature` names it as the capability to grant. A user who opts in gets start-print; a user who does not gets a printer that cannot be told to start a print over the network, which is the safe default.

Confidence, stated honestly. The `SDCP_CC1` capability set is grounded in the live notes and I would ship it. The Moonraker, OctoPrint, PrusaLink, Duet, Bambu, and Creality sets are grounded in `protocol-research.md`, which is a research artifact, and several matrix cells and endpoints in it are marked **UNCONFIRMED**. The `WEB_ONLY` set is a design choice, not a finding. Treat every adapter's capability set as a claim to falsify on real hardware, which is why `AdapterRegistration.evidence` exists.

## WHAT I DELETED AND WHY

`async_connect` and `async_disconnect` as a separate pair from setup and teardown. Two names for one lifecycle. I kept `async_setup` and `async_teardown` because they say what actually happens, a one-time handshake and a guaranteed close, and because HA already uses that pair for `ConfigEntry.async_setup` and `async_unload`.

`async_snapshot()` on the protocol. I had it, next to `async_camera_frame`, on the theory that a still and a stream are different things. They are not different things at this interface. A still is one frame from the same source, the SDCP camera's only documented single-shot method is to grab one frame off the MJPEG stream, and the Creality K1 has no snapshot endpoint at all and its reference integration hunts for `\xff\xd8` and `\xff\xd9` in the stream. One method that returns a JPEG, and a hub that can use it for either purpose.

`camera_url()` returning a stream URL. It is the same frame bytes with a credential leak and a network-path assumption attached.

`web_ui_url()` on the protocol. The config entry already carries the host, and the web port is a registration field. A method that returns `f"http://{host}/"` is a pass-through, and the tell is that it has one caller and reduces to no protocol knowledge.

`async_send_raw(payload)` as an escape hatch for vendor G-code. It would let an automation bypass the capability gate and the unsafe gate in one line, which deletes the value of sections 2 and 7. Duet and Bambu reach their temperatures through G-code inside their own adapters, where that is a private detail.

`async_home()`, `async_pause()`, `async_resume()` and the rest as separate protocol methods. They are one `async_send(command)` with a shared guard. Fourteen near-identical signatures with fourteen bodies that each repeat the capability check is the definition of a choice repeated in several places.

`Capabilities` as a feature table with a boolean per member, and `Capability` as a plain class with class attributes. The `StrEnum` plus `frozenset` makes the query one expression, serialises with no conversion, and fails loudly on a typo.

`PrinterSnapshot` as a sum type over printing and idle variants. Discussed in section 3. No operation in the entity layer or the card needs the extra precision, and it would double the constructor surface of every adapter.

`Progress` as a `Percent` with a validated 0 to 100 range. Klipper's `virtual_sdcard.progress` is a 0.0 to 1.0 float, OctoPrint and PrusaLink report integers, and SDCP reports an integer. Normalising is real work at the boundary, but a validating constructor invites a runtime assertion in the middle of an adapter, and the entity layer's HA unit conversion already owns the display. Normalise the scale at the boundary and note it in the adapter, do not build a type to police it.

`FileEntry` with a `printable: bool`. Every listed file in every protocol that lists files is printable except Elegoo's internal storage entries, and the SDCP adapter knows which is which when it builds the entry. A boolean on the shared type is a protocol detail in a shared model.

`AdapterRegistration` as a class with `__init_subclass__` self-registration, and a `ProtocolRegistry` object with `register` and `lookup` methods and duplicate detection. Twenty lines of machinery for a dict with ten entries that a reader can see at once. The dict is the registry.

A `frame` capability separate from `camera`, distinguishing MJPEG from snapshot. No consumer would behave differently, because the hub fans out bytes either way.

## RISKS

The `SDCP_CC1` gate reason in the brief is factually wrong and I am designing against the corrected version. If the parent's separate research report is right and the live notes are wrong, then command 128 must be absent from the defaults, which it already is. The gate is generic in either direction, so the design survives being wrong about which command is dangerous. The one thing I would not do is ship an SDCP probe or a start-print against hardware without re-checking `PrintInfo.Status == 0` first, because recovery is a wall power cycle.

Status pushes on a Centauri Carbon cannot be relied on. The live notes record `Cmd 512` being acknowledged with no frames ever pushed, surviving a reboot, while `Cmd 0` one-shots still work, and record pushes resuming mid-print. An adapter that waits for a push hangs. The SDCP adapter must fall back to a `Cmd 0` poll when no push arrives within one period. That is an adapter-internal decision and it does not change the interface, but skipping it produces a printer that looks dead while it is fine.

The SDCP camera server "has very few connection slots and leaks them", with a disconnected client sitting in `FIN-WAIT-2` and holding a slot. This is why the integration must hold one upstream connection and fan frames out through the hub. If an adapter is written to open a connection per request, the camera will starve and the fix will look like a firmware problem.

Anycubic's start-print payload is documented in one place, by Rinkhals, and the research marks the file-list request shape unverified. Its `prog.remain_time` is in minutes and its state string has a firmware misspelling, `stoped`. Its `data.project.pause` is the authoritative pause flag, not `data.state`. An Anycubic adapter built from the capability matrix alone will be wrong in at least two places.

Bambu's write gate is the highest-risk behaviour in the fleet. `print.fun` bit `0x20000000` is the truth, three different sources give three different firmware thresholds for when it applies, and there is an open bug where the derived `developer_lan_mode` flag reports on when the raw bit says off. The adapter must read the bit, and a probe by sending a command and watching for `mqtt message verify failed` "can destabilize some firmware MQTT brokers" according to the research. Do not probe. Read the bit, and surface a distinct state for "developer mode required" rather than an error.

Several capability sets are asserted rather than verified. `prusalink` has no start-print method in `pyprusalink` and the v1 spec was not fetched, so I have written `START_PRINT` out of its defaults, but that could be wrong in the user's favour. `octoprint` has `current_layer` and `total_layers` absent from the REST responses the research read, so its adapter cannot populate them. `creality_ws` has no start-print frame in the reference integration and no emergency stop, and its status is derived rather than reported. `duet` reports progress as computed rather than native. Each of those is a claim to check against hardware before the adapter ships, and `AdapterRegistration.evidence` is where the check belongs.

`WEB_ONLY` is my invention in this document, not a finding from the research. The research's closest cases are Marlin over a serial bridge with a third-party HTTP front-end, and a Creality printer reached only through its port-80 UI. I do not know whether the user's fleet actually contains such a printer, or whether "web-only" in the brief means "has a web UI and no API I have found yet".

The `Seconds` and `Percent` newtypes will annoy anyone who forgets them. `NewType` has no runtime enforcement, so a plain `float` assigned to a `Seconds` field checks fine unless the project runs a strict type checker in CI. Without that check the brands are documentation. I would add the check, because the Bambu and Anycubic minute-versus-second divergence is a real defect waiting in a future adapter.

Two smaller ones. `Fans` carries duty percentages for every protocol that populates it, while PrusaLink reports fan speeds in RPM. I left PrusaLink's fan values `None` rather than converting, and a future contributor may "fix" that by converting and introduce a unit bug. And `homed_axes: frozenset[str]` holds bare strings where a small `HomedAxis` enum would be tighter. I left it loose because Klipper reports a string containing some of `x`, `y`, and `z`, and a strict enum forces a parse whose only consumer is a card badge.
