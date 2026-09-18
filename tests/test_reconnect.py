"""A printer that powers off and back on must recover without a reload.

This is the failure a user hits with no way to diagnose it: the printer's control
socket dies when the printer loses power, and the integration keeps reporting the
last thing it knew until somebody reloads the config entry by hand.

The fake printer can be stopped and started again on the same addresses, which is
what makes the scenario reproducible. The assertions drive the real coordinator and
the real adapter, so a recovery here is a recovery in production.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "custom_components"))

from custom_components.generic_3dprinter.const import (  # noqa: E402
    DATA_COORDINATORS,
    DATA_RUNTIMES,
    Command,
)
from custom_components.generic_3dprinter.coordinator import PrinterError  # noqa: E402
from custom_components.generic_3dprinter.protocols import ProtocolError  # noqa: E402


@asynccontextmanager
async def loaded(hass: HomeAssistant, entry: MockConfigEntry):
    """Load an entry and always unload it again before the test finishes."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    try:
        yield hass.data[DATA_RUNTIMES][entry.entry_id], hass.data[DATA_COORDINATORS][
            entry.entry_id
        ]
    finally:
        with contextlib.suppress(Exception):
            await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def poll(coordinator) -> object:
    """Run one poll, returning the snapshot or the exception it raised.

    The coordinator's own update path is what a real poll takes, so the test
    exercises the reconnect logic rather than a copy of it.
    """
    try:
        return await coordinator._async_update_data()  # noqa: SLF001
    except Exception as err:  # noqa: BLE001 - the exception is the observation
        return err


async def test_the_printer_goes_away_and_comes_back(
    hass: HomeAssistant, config_entry: MockConfigEntry, printer
) -> None:
    async with loaded(hass, config_entry) as (runtime, coordinator):
        first = await poll(coordinator)
        assert not isinstance(first, Exception), first
        assert first.connected is True
        assert first.print_state.value == "printing"

        # Power off: the socket dies with the printer.
        await printer.stop()
        await asyncio.sleep(0.1)

        offline = await poll(coordinator)
        assert not isinstance(offline, Exception), offline
        assert offline.connected is False, "a dead printer still reported as connected"

        # Power on: same address, so this is a reconnect and not a reconfiguration.
        await printer.restart()
        await asyncio.sleep(0.1)

        recovered = None
        for _ in range(4):
            result = await poll(coordinator)
            if not isinstance(result, Exception) and result.connected:
                recovered = result
                break
            await asyncio.sleep(0.2)

        assert recovered is not None, (
            "the printer came back but the integration never reconnected, so the "
            "entry would have to be reloaded by hand"
        )
        assert recovered.print_state.value == "printing"
        assert recovered.hotend.current is not None
        assert runtime.last_error is None


async def test_recovery_survives_several_failed_polls(
    hass: HomeAssistant, config_entry: MockConfigEntry, printer
) -> None:
    """A printer that stays off for a while must still reconnect when it returns."""
    async with loaded(hass, config_entry) as (_runtime, coordinator):
        assert not isinstance(await poll(coordinator), Exception)
        await printer.stop()

        failures = 0
        for _ in range(3):
            result = await poll(coordinator)
            if isinstance(result, Exception) or not result.connected:
                failures += 1
            await asyncio.sleep(0.05)
        assert failures == 3, f"expected three offline polls, saw {failures} online"

        await printer.restart()
        recovered = None
        for _ in range(4):
            result = await poll(coordinator)
            if not isinstance(result, Exception) and result.connected:
                recovered = result
                break
            await asyncio.sleep(0.2)
        assert recovered is not None, "no recovery after a long outage"


async def test_a_command_sent_while_the_printer_is_off_does_not_wedge_it(
    hass: HomeAssistant, config_entry: MockConfigEntry, printer
) -> None:
    """A failed command must not leave the session unusable for the next poll."""
    async with loaded(hass, config_entry) as (_runtime, coordinator):
        assert not isinstance(await poll(coordinator), Exception)
        await printer.stop()

        with pytest.raises(PrinterError):
            await coordinator.async_send_command(Command.PAUSE)

        await printer.restart()
        recovered = None
        for _ in range(4):
            result = await poll(coordinator)
            if not isinstance(result, Exception) and result.connected:
                recovered = result
                break
            await asyncio.sleep(0.2)
        assert recovered is not None, "a failed command left the integration wedged"


async def test_the_adapter_reconnects_on_its_own(
    hass: HomeAssistant, config_entry: MockConfigEntry, printer
) -> None:
    """The adapter heals itself, not only when the coordinator happens to help.

    Reading is what reconnects. A read that only consulted the cached status would
    report offline for ever, which is the state a user has to reload to escape.
    """
    async with loaded(hass, config_entry) as (runtime, _coordinator):
        adapter = runtime.adapter
        before = await adapter.async_read()
        assert before.connected is True

        await printer.stop()
        with contextlib.suppress(ProtocolError):
            await adapter.async_read()

        await printer.restart()
        after = None
        for _ in range(4):
            try:
                result = await adapter.async_read()
            except ProtocolError:
                result = None
            if result is not None and result.connected:
                after = result
                break
            await asyncio.sleep(0.2)

        assert after is not None, "the adapter never reconnected on its own"
        assert after.hotend.current is not None, "reconnected but read no live reading"


async def test_a_power_cycle_leaves_no_stale_readings(
    hass: HomeAssistant, config_entry: MockConfigEntry, printer
) -> None:
    """Recovery must clear the offline marker, not just flip a flag."""
    async with loaded(hass, config_entry) as (runtime, coordinator):
        assert not isinstance(await poll(coordinator), Exception)

        await printer.stop()
        offline = await poll(coordinator)
        assert offline.connected is False
        assert offline.errors, "an offline printer reported no reason"
        assert runtime.last_error, "the runtime kept no last error"

        await printer.restart()
        for _ in range(4):
            result = await poll(coordinator)
            if not isinstance(result, Exception) and result.connected:
                break
            await asyncio.sleep(0.2)

        assert result.connected is True
        assert result.errors == (), f"stale errors survived recovery: {result.errors}"
        assert runtime.last_error is None
        assert result.print_state.value == "printing"
