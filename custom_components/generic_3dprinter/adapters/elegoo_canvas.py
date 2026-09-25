"""Elegoo's CANVAS multi-material unit, as both Centauri Carbon models report it.

The Centauri Carbon answers SDCP command 324 and the Centauri Carbon 2 answers
method 2005 with the same document: the active unit and slot, the auto-refill
setting, and one entry per unit with its four trays. This module turns that
document into a :class:`~..models.FilamentSystem`, so the two adapters differ only
in how they ask for it.

Measured on a live Centauri Carbon with a CANVAS on firmware V1.4.49::

    {"active_canvas_id": 0, "active_tray_id": -1, "auto_refill": 1,
     "canvas_list": [{"canvas_id": 0, "connected": 1, "tray_list": [
        {"tray_id": 0, "brand": "Generic", "filament_type": "PETG",
         "filament_name": "PETG PRO", "filament_code": "0x00000",
         "filament_color": "#000000", "min_nozzle_temp": 230,
         "max_nozzle_temp": 260, "status": 0}, ...]}]}

``status`` is 0 for an empty tray, 1 for a loaded one and 2 for the tray feeding
the nozzle. An empty tray keeps the filament last recorded for it, which is why
the printer's own page shows "/" for it rather than the name.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from ..models import Celsius, FilamentSlot, FilamentSystem, FilamentUnit

UNIT_NAME: Final = "CANVAS"

_COLOR_RE: Final = re.compile(r"#?([0-9A-Fa-f]{6})")


@dataclass(frozen=True, slots=True)
class FilamentPreset:
    """One filament Elegoo's own software offers for a slot."""

    material: str
    name: str
    code: str
    min_temp: int
    max_temp: int
    elegoo: bool
    generic: bool

    def as_dict(self) -> dict[str, Any]:
        """Return the preset as the card reads it. The code stays in the adapter."""
        offered = (("ELEGOO", self.elegoo), ("Generic", self.generic))
        brands = [brand for brand, available in offered if available]
        return {
            "material": self.material,
            "name": self.name,
            "min_temp": self.min_temp,
            "max_temp": self.max_temp,
            "brands": brands,
        }


def _preset(
    material: str, name: str, code: str, low: int, high: int, elegoo: bool = True
) -> FilamentPreset:
    return FilamentPreset(material, name, code, low, high, elegoo, True)


#: The filaments Elegoo's own web page offers for a CANVAS slot, with the code the
#: printer stores for each. Taken from the community elegoo-web project, which
#: copied the table from Elegoo's page.
FILAMENT_PRESETS: Final[tuple[FilamentPreset, ...]] = (
    _preset("PLA", "PLA", "0x0000", 190, 230),
    _preset("PLA", "PLA+", "0x0001", 190, 230),
    _preset("PLA", "PLA PRO", "0x0002", 190, 230),
    _preset("PLA", "PLA Silk", "0x0003", 190, 230),
    _preset("PLA", "PLA-CF", "0x0004", 210, 240),
    _preset("PLA", "PLA Carbon", "0x0005", 190, 230, elegoo=False),
    _preset("PLA", "PLA Matte", "0x0006", 190, 230),
    _preset("PLA", "PLA Fluo", "0x0007", 190, 230, elegoo=False),
    _preset("PLA", "PLA Wood", "0x0008", 190, 230),
    _preset("PLA", "PLA Basic", "0x0009", 190, 230),
    _preset("PLA", "RAPID PLA+", "0x000A", 190, 230),
    _preset("PLA", "PLA Marble", "0x000B", 190, 230),
    _preset("PLA", "PLA Galaxy", "0x000C", 190, 230),
    _preset("PLA", "PLA Red Copper", "0x000D", 190, 230),
    _preset("PLA", "PLA Sparkle", "0x000E", 190, 230, elegoo=False),
    _preset("PETG", "PETG", "0x0100", 230, 260),
    _preset("PETG", "PETG-CF", "0x0101", 240, 270),
    _preset("PETG", "PETG-GF", "0x0102", 240, 270),
    _preset("PETG", "PETG PRO", "0x0103", 230, 260),
    _preset("PETG", "PETG Translucent", "0x0104", 230, 260),
    _preset("PETG", "RAPID PETG", "0x0105", 230, 260),
    _preset("ABS", "ABS", "0x0200", 240, 280),
    _preset("ABS", "ABS-GF", "0x0201", 240, 280, elegoo=False),
    _preset("TPU", "TPU", "0x0300", 220, 240, elegoo=False),
    _preset("TPU", "TPU 95A", "0x0301", 220, 240),
    _preset("TPU", "RAPID TPU 95A", "0x0302", 220, 240),
    _preset("PA", "PA", "0x0400", 260, 290, elegoo=False),
    _preset("PA", "PA-CF", "0x0401", 260, 300, elegoo=False),
    _preset("PA", "PAHT-CF", "0x0402", 280, 320),
    _preset("PA", "PA6", "0x0403", 260, 290, elegoo=False),
    _preset("PA", "PA6-CF", "0x0404", 270, 310, elegoo=False),
    _preset("PA", "PA12", "0x0405", 240, 270, elegoo=False),
    _preset("PA", "PA12-CF", "0x0406", 260, 290, elegoo=False),
    _preset("CPE", "CPE", "0x0500", 220, 250, elegoo=False),
    _preset("PC", "PC", "0x0600", 260, 290),
    _preset("PC", "PCTG", "0x0601", 260, 290, elegoo=False),
    _preset("PC", "PC-FR", "0x0602", 260, 290),
    _preset("PVA", "PVA", "0x0700", 180, 210, elegoo=False),
    _preset("ASA", "ASA", "0x0800", 240, 280),
    _preset("BVOH", "BVOH", "0x0900", 190, 210, elegoo=False),
    _preset("EVA", "EVA", "0x0A00", 180, 220, elegoo=False),
    _preset("HIPS", "HIPS", "0x0B00", 220, 250, elegoo=False),
    _preset("PP", "PP", "0x0C00", 210, 250, elegoo=False),
    _preset("PP", "PP-CF", "0x0C01", 220, 260, elegoo=False),
    _preset("PP", "PP-GF", "0x0C02", 230, 250, elegoo=False),
    _preset("PPA", "PPA", "0x0D00", 290, 310, elegoo=False),
    _preset("PPA", "PPA-CF", "0x0D01", 300, 320, elegoo=False),
    _preset("PPA", "PPA-GF", "0x0D02", 290, 310, elegoo=False),
    _preset("PPS", "PPS", "0x0E00", 330, 340, elegoo=False),
    _preset("PPS", "PPS-CF", "0x0E01", 340, 360, elegoo=False),
)

_PRESET_BY_NAME: Final[Mapping[str, FilamentPreset]] = MappingProxyType(
    {preset.name.upper(): preset for preset in FILAMENT_PRESETS}
)

#: What the printer is doing while it loads or unloads, by ``sub_status``. The
#: CANVAS codes come from the community elegoo-web project; the extruder codes are
#: the same steps without a CANVAS.
ACTIVITY_BY_SUB_STATUS: Final[Mapping[int, str]] = MappingProxyType(
    {
        1063: "Filament loaded",
        1064: "Filament unloaded",
        1133: "Heating the nozzle",
        1134: "Insert the filament",
        1135: "Gripping the filament",
        1136: "Filament gripped",
        1143: "Cutting the filament",
        1144: "Ejecting the filament",
        1145: "Filament ejected",
        1150: "Loading: starting",
        1151: "Loading: heating the nozzle",
        1152: "Loading: inserting the filament",
        1153: "Loading: cutting the old filament",
        1154: "Loading: retracting the old filament",
        1155: "Loading: feeding the filament",
        1156: "Loading: purging the old colour",
        1157: "Loading complete",
        1158: "Loading failed",
        1160: "Unloading: starting",
        1161: "Unloading: heating the nozzle",
        1162: "Unloading: checking the filament",
        1163: "Unloading: cutting the filament",
        1164: "Unloading: retracting the filament",
        1165: "Unloading complete",
        1166: "Unloading failed",
    }
)


def preset_for(name: str | None) -> FilamentPreset | None:
    """Return the preset with this filament name, ignoring case."""
    if not name:
        return None
    return _PRESET_BY_NAME.get(name.strip().upper())


def filament_code(name: str | None, material: str | None) -> str:
    """Return the code the printer stores for a filament.

    A name Elegoo's table knows gets its own code. Anything else gets the code of
    the first entry of its material family, and a material the table does not know
    gets PLA's, which is what the printer shows for an unknown filament.
    """
    preset = preset_for(name)
    if preset is not None:
        return preset.code
    family = (material or "").strip().upper()
    for preset in FILAMENT_PRESETS:
        if preset.material.upper() == family:
            return preset.code
    return FILAMENT_PRESETS[0].code


def _text(value: Any) -> str | None:
    """Return a label, or ``None`` for the blanks and placeholders the printer sends."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text in ("?", "/") or text.startswith("—"):
        return None
    return text


def _integer(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _celsius(value: Any) -> Celsius | None:
    number = _integer(value)
    return Celsius(float(number)) if number else None


def normalise_color(value: Any) -> str | None:
    """Return ``#RRGGBB`` in upper case, or ``None`` for anything else."""
    if not isinstance(value, str):
        return None
    match = _COLOR_RE.fullmatch(value.strip())
    return f"#{match.group(1).upper()}" if match else None


def _auto_refill(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    number = _integer(value)
    return None if number is None else number != 0


def parse_canvas(info: Any, *, activity: str | None = None) -> FilamentSystem | None:
    """Return the system a ``canvas_info`` document describes.

    ``None`` when the document lists no unit at all, which is what a printer with
    no CANVAS attached answers.
    """
    if not isinstance(info, Mapping):
        return None
    units_raw = info.get("canvas_list")
    if not isinstance(units_raw, Sequence) or isinstance(units_raw, (str, bytes)):
        return None

    active_unit = _integer(info.get("active_canvas_id"))
    active_slot = _integer(info.get("active_tray_id"))
    units: list[FilamentUnit] = []
    for position, unit_raw in enumerate(units_raw):
        if not isinstance(unit_raw, Mapping):
            continue
        unit_id = _integer(unit_raw.get("canvas_id"))
        unit_id = position if unit_id is None or unit_id < 0 else unit_id
        trays = unit_raw.get("tray_list")
        slots: list[FilamentSlot] = []
        if isinstance(trays, Sequence) and not isinstance(trays, (str, bytes)):
            for index, tray in enumerate(trays):
                if not isinstance(tray, Mapping):
                    continue
                slot_id = _integer(tray.get("tray_id"))
                slot_id = index if slot_id is None or slot_id < 0 else slot_id
                status = _integer(tray.get("status")) or 0
                # An empty tray cannot feed the nozzle, whatever the active ids say.
                active = status == 2 or (
                    status > 0
                    and active_slot is not None
                    and active_slot >= 0
                    and active_unit == unit_id
                    and active_slot == slot_id
                )
                material = _text(tray.get("filament_type"))
                slots.append(
                    FilamentSlot(
                        unit=unit_id,
                        slot=slot_id,
                        loaded=status > 0,
                        active=active,
                        material=material,
                        name=_text(tray.get("filament_name")) or material,
                        brand=_text(tray.get("brand")),
                        color=normalise_color(tray.get("filament_color")),
                        min_temp=_celsius(tray.get("min_nozzle_temp")),
                        max_temp=_celsius(tray.get("max_nozzle_temp")),
                    )
                )
        units.append(
            FilamentUnit(
                unit=unit_id,
                connected=bool(_integer(unit_raw.get("connected"))),
                name=UNIT_NAME,
                slots=tuple(sorted(slots, key=lambda item: item.slot)),
            )
        )
    if not units:
        return None
    return FilamentSystem(
        units=tuple(sorted(units, key=lambda item: item.unit)),
        auto_refill=_auto_refill(info.get("auto_refill")),
        activity=activity,
    )


def edit_payload(params: Mapping[str, Any]) -> dict[str, Any]:
    """Return the tray document a set-filament request carries.

    The field names are the ones Elegoo's page sends with method 2003. The
    temperatures default to the preset's, and to the material family's when the
    name is not one the table knows.
    """
    name = str(params.get("name") or params["material"])
    material = str(params["material"])
    preset = preset_for(name)
    low = params.get("min_temp")
    high = params.get("max_temp")
    if low is None:
        low = preset.min_temp if preset else 190
    if high is None:
        high = preset.max_temp if preset else 230
    if low > high:
        raise ValueError("the lowest nozzle temperature is above the highest")
    return {
        "canvas_id": int(params.get("unit", 0)),
        "tray_id": int(params["slot"]),
        "brand": str(params.get("brand") or "Generic"),
        "filament_type": material,
        "filament_name": name,
        "filament_code": filament_code(name, material),
        "filament_color": str(params["color"]),
        "filament_min_temp": round(low),
        "filament_max_temp": round(high),
    }
