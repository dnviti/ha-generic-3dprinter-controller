"""Switches for the printer's own lights.

A printer's chamber light is the one thing a user reaches for constantly and the
one thing most protocols expose as a plain on/off. The switch exists only when the
printer grants ``SET_LIGHT``, and its state comes from the snapshot's light set
rather than from a local flag, so a light turned on at the printer's own panel
shows as on here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import Capability, Command, LightChannel
from .coordinator import PrinterCoordinator
from .entity import (
    Generic3DPrinterEntity,
    async_require_coordinator,
    granted_capabilities,
)


@dataclass(frozen=True, kw_only=True)
class Generic3DPrinterSwitchDescription(SwitchEntityDescription):
    """One switchable light and the channel it addresses."""

    channel: LightChannel


SWITCH_DESCRIPTIONS: Final[tuple[Generic3DPrinterSwitchDescription, ...]] = (
    Generic3DPrinterSwitchDescription(
        key="chamber_light",
        icon="mdi:lightbulb",
        channel=LightChannel.CHAMBER,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the lights this printer's capabilities allow."""
    if not granted_capabilities(entry.runtime_data)[Capability.SET_LIGHT]:
        return
    coordinator = async_require_coordinator(hass, entry.entry_id)
    async_add_entities(
        Generic3DPrinterSwitch(coordinator, description)
        for description in SWITCH_DESCRIPTIONS
    )


class Generic3DPrinterSwitch(Generic3DPrinterEntity, SwitchEntity):
    """One switchable light on the printer."""

    entity_description: Generic3DPrinterSwitchDescription

    def __init__(
        self,
        coordinator: PrinterCoordinator,
        description: Generic3DPrinterSwitchDescription,
    ) -> None:
        """Bind the entity to its coordinator and its description."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool:
        """Return whether the printer reports this light as on."""
        return self.entity_description.channel in self.coordinator.data.lights

    async def async_turn_on(self, **kwargs: object) -> None:
        """Turn the light on."""
        await self._async_set_light(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        """Turn the light off."""
        await self._async_set_light(False)

    async def _async_set_light(self, on: bool) -> None:
        await self.coordinator.async_send_command(
            Command.SET_LIGHT, on=on, channel=self.entity_description.channel.value
        )
