"""Command parameter validation.

The boundary between "a service call arrived from Home Assistant" and "an adapter
is about to talk to a printer" is the one place where a value has to be proven
sane. An adapter body therefore contains only its happy path, and no adapter
repeats a range check for a nozzle temperature.

Values are normalised to **device units** here, not to display units. Temperature
is Celsius, speed and flow are percentages, fan duty is a percentage, distances
are millimetres, and a position is millimetres on each axis. Every adapter then
converts from that into whatever its protocol wants.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Final, Mapping

from .const import Command

#: Plausible physical ceilings. These are sanity bounds for a consumer 3D
#: printer, not a claim about any particular machine. A printer that cannot reach
#: one rejects the command itself.
MAX_TEMPERATURE: Final = 350.0
MIN_TEMPERATURE: Final = 0.0
MAX_PERCENT: Final = 100.0
MIN_PERCENT: Final = 0.0
MAX_JOG_MM: Final = 500.0
#: Units and slots of a multi-material system are numbered from 0. Four units of
#: four slots is the most any system in this fleet chains; the ceiling leaves room.
MAX_SLOT_INDEX: Final = 63
#: Material, brand and filament names are short labels, not free text.
MAX_LABEL_LENGTH: Final = 40

_COLOR_RE: Final = re.compile(r"#?([0-9A-Fa-f]{6})")


class ParamKind(StrEnum):
    """How one command parameter is validated."""

    TEMPERATURE = "temperature"
    PERCENT = "percent"
    DISTANCE = "distance"
    TEXT = "text"
    AXIS = "axis"
    LIGHT = "light"
    BOOLEAN = "boolean"
    INDEX = "index"
    COLOR = "color"


class ParamError(ValueError):
    """Raised when a command parameter is missing or out of range."""


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """One accepted parameter of one command."""

    kind: ParamKind
    required: bool = False
    default: Any = None
    choices: tuple[str, ...] = ()
    max_length: int | None = None

    def coerce(self, command: Command, name: str, raw: Any) -> Any:
        """Return the validated, unit-normalised value of ``raw``."""
        label = f"{command.value}.{name}"
        if self.kind is ParamKind.TEXT:
            if not isinstance(raw, str) or not raw.strip():
                raise ParamError(f"{label} must be a non-empty string")
            if self.max_length is not None and len(raw.strip()) > self.max_length:
                raise ParamError(f"{label} must be at most {self.max_length} characters")
            return raw.strip()
        if self.kind is ParamKind.INDEX:
            if isinstance(raw, bool):
                raise ParamError(f"{label} must be a whole number")
            try:
                number = float(raw)
            except (TypeError, ValueError) as err:
                raise ParamError(f"{label} must be a whole number") from err
            if not number.is_integer() or not 0 <= number <= MAX_SLOT_INDEX:
                raise ParamError(f"{label} must be a whole number from 0 to {MAX_SLOT_INDEX}")
            return int(number)
        if self.kind is ParamKind.COLOR:
            match = _COLOR_RE.fullmatch(raw.strip()) if isinstance(raw, str) else None
            if match is None:
                raise ParamError(f"{label} must be a colour written as #RRGGBB")
            return f"#{match.group(1).upper()}"
        if self.kind is ParamKind.BOOLEAN:
            if isinstance(raw, bool):
                return raw
            raise ParamError(f"{label} must be true or false")
        if self.kind is ParamKind.AXIS:
            value = str(raw).upper().strip()
            if value not in self.choices:
                allowed = ", ".join(self.choices)
                raise ParamError(f"{label} must be one of: {allowed}")
            return value
        if self.kind is ParamKind.LIGHT:
            value = str(raw).lower().strip()
            if value not in self.choices:
                allowed = ", ".join(self.choices)
                raise ParamError(f"{label} must be one of: {allowed}")
            return value

        try:
            number = float(raw)
        except (TypeError, ValueError) as err:
            raise ParamError(f"{label} must be a number") from err
        if math.isnan(number) or math.isinf(number):
            raise ParamError(f"{label} must be a finite number")

        minimum, maximum = _RANGE_BY_KIND[self.kind]
        if not minimum <= number <= maximum:
            raise ParamError(f"{label} must be between {minimum:g} and {maximum:g}")
        return number


_RANGE_BY_KIND: Final[Mapping[ParamKind, tuple[float, float]]] = MappingProxyType(
    {
        ParamKind.TEMPERATURE: (MIN_TEMPERATURE, MAX_TEMPERATURE),
        ParamKind.PERCENT: (MIN_PERCENT, MAX_PERCENT),
        ParamKind.DISTANCE: (-MAX_JOG_MM, MAX_JOG_MM),
    }
)

_AXIS_CHOICES: Final = ("X", "Y", "Z")
_HOME_CHOICES: Final = ("X", "Y", "Z", "XY", "XZ", "YZ", "XYZ")
_LIGHT_CHOICES: Final = ("chamber", "status", "rgb")

#: Which slot a filament command addresses. The unit defaults to the first, which
#: is the only one most printers have.
_SLOT_PARAMS: Final[Mapping[str, ParamSpec]] = MappingProxyType(
    {
        "unit": ParamSpec(ParamKind.INDEX, default=0),
        "slot": ParamSpec(ParamKind.INDEX, required=True),
    }
)

#: The complete parameter surface of the normalised command vocabulary. Adding a
#: command means adding one entry here; no adapter edits a range check.
PARAM_SCHEMA: Final[Mapping[Command, Mapping[str, ParamSpec]]] = MappingProxyType(
    {
        Command.START_PRINT: MappingProxyType(
            {
                "filename": ParamSpec(ParamKind.TEXT, required=True),
            }
        ),
        Command.SET_HOTEND_TEMP: MappingProxyType(
            {"value": ParamSpec(ParamKind.TEMPERATURE, required=True)}
        ),
        Command.SET_BED_TEMP: MappingProxyType(
            {"value": ParamSpec(ParamKind.TEMPERATURE, required=True)}
        ),
        Command.SET_CHAMBER_TEMP: MappingProxyType(
            {"value": ParamSpec(ParamKind.TEMPERATURE, required=True)}
        ),
        Command.SET_SPEED: MappingProxyType(
            {"value": ParamSpec(ParamKind.PERCENT, required=True)}
        ),
        Command.SET_FLOW: MappingProxyType(
            {"value": ParamSpec(ParamKind.PERCENT, required=True)}
        ),
        Command.SET_FAN_SPEED: MappingProxyType(
            {
                "value": ParamSpec(ParamKind.PERCENT, required=True),
                "channel": ParamSpec(
                    ParamKind.TEXT,
                    default="model",
                    choices=("model", "auxiliary", "chamber", "hotend", "controller"),
                ),
            }
        ),
        Command.SET_LIGHT: MappingProxyType(
            {
                "on": ParamSpec(ParamKind.BOOLEAN, required=True),
                "channel": ParamSpec(
                    ParamKind.LIGHT, default="chamber", choices=_LIGHT_CHOICES
                ),
            }
        ),
        Command.HOME: MappingProxyType(
            {
                "axes": ParamSpec(
                    ParamKind.AXIS, default="XYZ", choices=_HOME_CHOICES
                )
            }
        ),
        Command.JOG: MappingProxyType(
            {
                "axis": ParamSpec(ParamKind.AXIS, required=True, choices=_AXIS_CHOICES),
                "distance": ParamSpec(ParamKind.DISTANCE, required=True),
            }
        ),
        Command.DELETE_FILE: MappingProxyType(
            {"filename": ParamSpec(ParamKind.TEXT, required=True)}
        ),
        Command.PAUSE: MappingProxyType({}),
        Command.RESUME: MappingProxyType({}),
        Command.STOP: MappingProxyType({}),
        Command.LOAD_FILAMENT: _SLOT_PARAMS,
        Command.UNLOAD_FILAMENT: _SLOT_PARAMS,
        Command.SET_FILAMENT: MappingProxyType(
            {
                **_SLOT_PARAMS,
                "material": ParamSpec(
                    ParamKind.TEXT, required=True, max_length=MAX_LABEL_LENGTH
                ),
                "name": ParamSpec(ParamKind.TEXT, max_length=MAX_LABEL_LENGTH),
                "brand": ParamSpec(ParamKind.TEXT, max_length=MAX_LABEL_LENGTH),
                "color": ParamSpec(ParamKind.COLOR, required=True),
                "min_temp": ParamSpec(ParamKind.TEMPERATURE),
                "max_temp": ParamSpec(ParamKind.TEMPERATURE),
            }
        ),
        Command.SET_AUTO_REFILL: MappingProxyType(
            {"on": ParamSpec(ParamKind.BOOLEAN, required=True)}
        ),
    }
)


def validate_params(command: Command, params: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalise the parameters of ``command``.

    Raises :class:`ParamError` for a missing required parameter, an unknown
    parameter, or a value outside its range.
    """
    schema = PARAM_SCHEMA[command]
    unknown = set(params) - set(schema)
    if unknown:
        allowed = ", ".join(sorted(schema)) or "none"
        raise ParamError(
            f"{command.value} takes no parameter named {sorted(unknown)[0]} "
            f"(accepted: {allowed})"
        )

    validated: dict[str, Any] = {}
    for name, spec in schema.items():
        if name in params and params[name] is not None:
            validated[name] = spec.coerce(command, name, params[name])
        elif spec.required:
            raise ParamError(f"{command.value} requires the {name} parameter")
        elif spec.default is not None:
            validated[name] = spec.default
    return validated


def command_parameters(command: Command) -> Mapping[str, ParamSpec]:
    """Return the parameter schema of one command, for the card and docs."""
    return PARAM_SCHEMA[command]
