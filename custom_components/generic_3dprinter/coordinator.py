"""Polling coordinator for one printer.

The coordinator owns the poll cadence and the one shared snapshot every entity
reads, so a printer is asked for its state once per interval rather than once per
entity. It also owns the command path, so an automation and a dashboard button go
through the same guards.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN
from .models import FileEntry, PrinterSnapshot
from .protocols import (
    AuthError,
    CommandRejectedError,
    ProtocolError,
    UnreachableError,
    UnsupportedCommandError,
    UnsafeCommandError,
)
from .const import Command
from .runtime import PrinterRuntime

_LOGGER = logging.getLogger(__name__)


class PrinterError(HomeAssistantError):
    """Raised to an automation or a service call when a printer command fails."""


class PrinterCoordinator(DataUpdateCoordinator[PrinterSnapshot]):
    """Poll one printer and expose its snapshot to every entity."""

    def __init__(self, hass: HomeAssistant, runtime: PrinterRuntime, entry: ConfigEntry) -> None:
        """Create the coordinator for one config entry."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}:{runtime.config.name}",
            update_interval=timedelta(seconds=runtime.config.scan_interval),
            config_entry=entry,
        )
        self.runtime = runtime

    async def _async_update_data(self) -> PrinterSnapshot:
        """Read one snapshot, reconnecting when the printer dropped the session.

        Every failure returns an offline snapshot rather than raising. A printer that
        has been switched off is an expected state, not an error condition, and
        raising here would leave the entities unavailable with no reading of why.
        """
        runtime = self.runtime
        try:
            snapshot = await runtime.adapter.async_read()
        except AuthError as err:
            runtime.last_error = f"authentication failed: {err}"
            self.async_set_update_error(err)
            return self._offline_snapshot(runtime.last_error)
        except UnreachableError as err:
            _LOGGER.debug("%s: read failed (%s), reconnecting", runtime.config.name, err)
            return await self._async_reconnect(err)
        except ProtocolError as err:
            runtime.last_error = str(err)
            self.async_set_update_error(err)
            return self._offline_snapshot(str(err))

        return self._store(snapshot)

    async def _async_reconnect(self, cause: Exception) -> PrinterSnapshot:
        """Rebuild the session once and read again, reporting offline if it fails.

        The cause is reported on the snapshot, so "offline" says whether the printer
        is unreachable or whether it answered with something unusable. A printer that
        is simply off must not leave the coordinator raising forever.
        """
        runtime = self.runtime
        try:
            await runtime.adapter.async_teardown()
            await runtime.adapter.async_setup()
            snapshot = await runtime.adapter.async_read()
        except (AuthError, ProtocolError) as err:
            runtime.last_error = str(err)
            self.async_set_update_error(err)
            return self._offline_snapshot(str(err))
        return self._store(snapshot)

    def _store(self, snapshot: PrinterSnapshot) -> PrinterSnapshot:
        """Record a good reading and clear the previous error."""
        runtime = self.runtime
        runtime.snapshot = snapshot
        runtime.last_error = None
        runtime.last_seen = self.hass.loop.time()
        return snapshot

    def _offline_snapshot(self, error: str) -> PrinterSnapshot:
        """Return a snapshot that says "offline" without inventing any reading."""
        runtime = self.runtime
        runtime.snapshot = PrinterSnapshot(
            protocol=runtime.config.protocol,
            connected=False,
            capabilities=runtime.adapter.capabilities,
            model=runtime.snapshot.model,
            firmware=runtime.snapshot.firmware,
            serial=runtime.snapshot.serial,
            errors=(error,),
        )
        return runtime.snapshot

    async def async_send_command(self, command: Command, **params: Any) -> None:
        """Send one command, translating every failure into Home Assistant wording."""
        try:
            await self.runtime.adapter.async_send(command, **params)
        except UnsafeCommandError as err:
            raise PrinterError(str(err)) from err
        except UnsupportedCommandError as err:
            raise PrinterError(str(err)) from err
        except CommandRejectedError as err:
            raise PrinterError(str(err)) from err
        except UnreachableError as err:
            raise PrinterError(f"the printer is unreachable: {err}") from err
        except ProtocolError as err:
            raise PrinterError(str(err)) from err
        # Read the printer again, so the card and the entities show what the
        # command changed now rather than at the next poll. The refresh is
        # debounced, so a burst of commands still reads the printer once.
        await self.async_request_refresh()

    async def async_list_files(self) -> list[FileEntry]:
        """Refresh and return the printer's stored files."""
        try:
            files = await self.runtime.adapter.async_list_files()
        except ProtocolError as err:
            raise PrinterError(str(err)) from err
        self.runtime.files = list(files)
        return self.runtime.files

    async def async_upload_file(self, name: str, data: bytes) -> FileEntry:
        """Upload one file from a service call's bytes."""

        async def chunks():
            yield data

        try:
            entry = await self.runtime.adapter.async_upload_file(
                name, chunks(), size=len(data)
            )
        except ProtocolError as err:
            raise PrinterError(str(err)) from err
        return entry
