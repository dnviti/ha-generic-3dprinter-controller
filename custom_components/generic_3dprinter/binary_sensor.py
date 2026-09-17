"""Binary state sensors for one printer.

Two readings that Home Assistant and every dashboard already understand without a
template: whether the printer is reachable, and whether a job is running. Both are
present for every printer, because both derive from fields the adapter itself owns
rather than from anything a protocol has to report.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import PrintState
from .coordinator import PrinterCoordinator
from .entity import Generic3DPrinterEntity, async_require_coordinator
from .models import PrinterSnapshot


@dataclass(frozen=True, kw_only=True)
class Generic3DPrinterBinarySensorDescription(BinarySensorEntityDescription):
    """One binary reading and the snapshot field behind it."""

    value_fn: Callable[[PrinterSnapshot], bool]


BINARY_SENSOR_DESCRIPTIONS: tuple[Generic3DPrinterBinarySensorDescription, ...] = (
    Generic3DPrinterBinarySensorDescription(
        key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda snapshot: snapshot.connected,
    ),
    Generic3DPrinterBinarySensorDescription(
        key="active_job",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda snapshot: snapshot.print_state is PrintState.PRINTING,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the binary sensors for this printer."""
    coordinator = async_require_coordinator(hass, entry.entry_id)
    async_add_entities(
        Generic3DPrinterBinarySensor(coordinator, description)
        for description in BINARY_SENSOR_DESCRIPTIONS
    )


class Generic3DPrinterBinarySensor(Generic3DPrinterEntity, BinarySensorEntity):
    """One binary reading from the shared snapshot."""

    entity_description: Generic3DPrinterBinarySensorDescription

    def __init__(
        self,
        coordinator: PrinterCoordinator,
        description: Generic3DPrinterBinarySensorDescription,
    ) -> None:
        """Bind the entity to its coordinator and its description."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        """Return the current reading."""
        return self.entity_description.value_fn(self.coordinator.data)
