"""Read-only sensors for one printer.

One entity per reading, so a dashboard, an automation and the recorder all see the
same named value instead of one opaque attribute blob. A sensor exists only when
the printer can actually produce the reading, which is what `_gate` on each
description states: a printer that cannot report a chamber temperature and cannot
be told one gets no chamber sensor at all, rather than a permanently empty one.

Layers are the one exception to "gate on the capability that names the value",
because no protocol declares a layer capability. See :data:`SENSOR_DESCRIPTIONS`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfLength,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import Capability, PrintState
from .coordinator import PrinterCoordinator
from .entity import (
    Generic3DPrinterEntity,
    async_require_coordinator,
    granted_capabilities,
)
from .models import PrinterSnapshot
from .runtime import PrinterRuntime

type CapabilityGate = Callable[[Mapping[Capability, bool]], bool]


def _always(_: Mapping[Capability, bool]) -> bool:
    """Return ``True`` for a reading every protocol can produce."""
    return True


def _gate_for(*capabilities: Capability, any_of: bool = False) -> CapabilityGate:
    """Return a predicate over the granted capability set.

    ``any_of`` asks for one of the capabilities rather than all of them, which is
    how the position sensors accept either a jog control or a home control.
    """
    if any_of:
        return lambda granted: any(granted[item] for item in capabilities)
    return lambda granted: all(granted[item] for item in capabilities)


def _axis(axis: str) -> Callable[[PrinterSnapshot], StateType | None]:
    def read(snapshot: PrinterSnapshot) -> StateType | None:
        position = snapshot.position
        return getattr(position, axis) if position is not None else None

    return read


def _configured_address(_: PrinterSnapshot) -> StateType | None:
    """Mark a reading that comes from the configuration rather than the printer.

    The address is the one diagnostic the snapshot cannot carry: an adapter knows
    where it was pointed, and the printer never reports it.
    """
    return None


@dataclass(frozen=True, kw_only=True)
class Generic3DPrinterSensorDescription(SensorEntityDescription):
    """One reading, its Home Assistant metadata, and when it exists."""

    value_fn: Callable[[PrinterSnapshot], StateType | None]
    gate: CapabilityGate = _always


#: Every sensor this platform can offer.
#:
#: The layer counters are gated on ``FILE_UPLOAD`` rather than on a layer
#: capability, because no protocol in this fleet declares one: OctoPrint and
#: PrusaLink upload files and report no layer count at all, while Moonraker, SDCP
#: and Duet all report layers. ``FILE_UPLOAD`` is the honest proxy for "this
#: protocol has a job it tracks layer by layer", and it is deterministic at entry
#: setup, unlike "the first snapshot happened to be taken mid-print".
SENSOR_DESCRIPTIONS: tuple[Generic3DPrinterSensorDescription, ...] = (
    Generic3DPrinterSensorDescription(
        key="printer_state",
        device_class=SensorDeviceClass.ENUM,
        options=[item.value for item in PrintState],
        value_fn=lambda snapshot: snapshot.print_state.value,
    ),
    Generic3DPrinterSensorDescription(
        key="progress",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda snapshot: snapshot.progress,
        gate=_gate_for(Capability.START_PRINT, Capability.PAUSE, any_of=True),
    ),
    Generic3DPrinterSensorDescription(
        key="current_layer",
        value_fn=lambda snapshot: snapshot.current_layer,
        gate=_gate_for(Capability.FILE_UPLOAD),
    ),
    Generic3DPrinterSensorDescription(
        key="total_layers",
        value_fn=lambda snapshot: snapshot.total_layers,
        gate=_gate_for(Capability.FILE_UPLOAD),
    ),
    Generic3DPrinterSensorDescription(
        key="remaining_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=lambda snapshot: snapshot.remaining,
    ),
    Generic3DPrinterSensorDescription(
        key="elapsed_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        value_fn=lambda snapshot: snapshot.elapsed,
    ),
    Generic3DPrinterSensorDescription(
        key="filename",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda snapshot: snapshot.filename,
    ),
    Generic3DPrinterSensorDescription(
        key="nozzle_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda snapshot: snapshot.hotend.current,
        suggested_display_precision=1,
    ),
    Generic3DPrinterSensorDescription(
        key="nozzle_target_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda snapshot: snapshot.hotend.target,
        suggested_display_precision=1,
    ),
    Generic3DPrinterSensorDescription(
        key="bed_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda snapshot: snapshot.bed.current,
        suggested_display_precision=1,
    ),
    Generic3DPrinterSensorDescription(
        key="bed_target_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda snapshot: snapshot.bed.target,
        suggested_display_precision=1,
    ),
    Generic3DPrinterSensorDescription(
        key="chamber_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda snapshot: snapshot.chamber.current,
        suggested_display_precision=1,
        gate=_gate_for(Capability.SET_CHAMBER_TEMP),
    ),
    Generic3DPrinterSensorDescription(
        key="chamber_target_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda snapshot: snapshot.chamber.target,
        suggested_display_precision=1,
        gate=_gate_for(Capability.SET_CHAMBER_TEMP),
    ),
    Generic3DPrinterSensorDescription(
        key="fan_model_speed",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda snapshot: snapshot.fans.model,
        gate=_gate_for(Capability.SET_FAN_SPEED),
    ),
    Generic3DPrinterSensorDescription(
        key="fan_auxiliary_speed",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda snapshot: snapshot.fans.auxiliary,
        gate=_gate_for(Capability.SET_FAN_SPEED),
    ),
    Generic3DPrinterSensorDescription(
        key="position_x",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        value_fn=_axis("x"),
        gate=_gate_for(Capability.JOG, Capability.HOME, any_of=True),
    ),
    Generic3DPrinterSensorDescription(
        key="position_y",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        value_fn=_axis("y"),
        gate=_gate_for(Capability.JOG, Capability.HOME, any_of=True),
    ),
    Generic3DPrinterSensorDescription(
        key="position_z",
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        value_fn=_axis("z"),
        gate=_gate_for(Capability.JOG, Capability.HOME, any_of=True),
    ),
    Generic3DPrinterSensorDescription(
        key="speed_factor",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda snapshot: snapshot.speed_factor,
        gate=_gate_for(Capability.SET_SPEED),
    ),
    Generic3DPrinterSensorDescription(
        key="flow_factor",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda snapshot: snapshot.flow_factor,
        gate=_gate_for(Capability.SET_FLOW),
    ),
    Generic3DPrinterSensorDescription(
        key="protocol",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda snapshot: snapshot.protocol.value,
    ),
    Generic3DPrinterSensorDescription(
        key="firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda snapshot: snapshot.firmware,
    ),
    Generic3DPrinterSensorDescription(
        key="model",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda snapshot: snapshot.model,
    ),
    Generic3DPrinterSensorDescription(
        key="serial",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda snapshot: snapshot.serial,
    ),
    Generic3DPrinterSensorDescription(
        key="ip_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_configured_address,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the sensors this printer's capabilities allow."""
    coordinator = async_require_coordinator(hass, entry.entry_id)
    runtime: PrinterRuntime = entry.runtime_data
    granted = granted_capabilities(runtime)
    async_add_entities(
        Generic3DPrinterSensor(coordinator, description)
        for description in SENSOR_DESCRIPTIONS
        if description.gate(granted)
    )


class Generic3DPrinterSensor(Generic3DPrinterEntity, SensorEntity):
    """One reading from the shared snapshot."""

    entity_description: Generic3DPrinterSensorDescription

    def __init__(
        self,
        coordinator: PrinterCoordinator,
        description: Generic3DPrinterSensorDescription,
    ) -> None:
        """Bind the entity to its coordinator and its description."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType | None:
        """Return the current reading, or ``None`` when the printer has none."""
        if self.entity_description.value_fn is _configured_address:
            return self.runtime.config.host
        return self.entity_description.value_fn(self.coordinator.data)
