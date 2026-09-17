"""The adapter registry.

Adding a protocol means adding one module under :mod:`adapters` and one entry in
:data:`ADAPTERS`. Nothing else in the integration names a protocol, so the
coordinator, the entity platforms and the Lovelace card need no edit, because all
three read the capability set instead.

Each registration records what is *verified* and what is merely *inferred*, in
its ``evidence`` mapping, and declares any hazard that needs an explicit opt-in.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Final

import aiohttp

from .const import Capability, ProtocolId, UnsafeFeature
from .protocols import PrinterConfig, Protocol

_LOGGER = logging.getLogger(__name__)

#: Every adapter module that ships with the integration.
ADAPTER_MODULES: Final[tuple[str, ...]] = (
    "sdcp",
    "moonraker",
    "octoprint",
    "prusalink",
    "duet",
    "web_only",
)


def _load_adapters() -> dict[str, Any]:
    """Import each adapter module on its own.

    One module at a time, so an adapter that fails to import disables only its own
    protocol. Importing them as a group would let a single broken module silently
    remove every protocol from the registry, which is the failure this function
    exists to prevent.
    """
    modules: dict[str, Any] = {}
    for name in ADAPTER_MODULES:
        try:
            modules[name] = importlib.import_module(f".adapters.{name}", __package__)
        except ImportError as err:  # pragma: no cover - only on a broken install
            _LOGGER.warning("the %s adapter is unavailable: %s", name, err)
            modules[name] = None
    return modules


_ADAPTER_MODULES = _load_adapters()

_UNSAFE_SDCP_START_PRINT: Final = UnsafeFeature(
    id="sdcp_start_print",
    label="Allow starting a print over the network",
    reason=(
        "The SDCP start-print command carries a six-field payload, and this "
        "integration builds that payload itself. On Centauri Carbon firmware an "
        "unexpected payload shape has been reported to crash the printer's app "
        "daemon, which is the whole host firmware including the motion stack. If "
        "that happens the running print is destroyed and the printer needs a power "
        "cycle. Enable this only if you accept that risk."
    ),
    gates=frozenset({Capability.START_PRINT}),
    evidence="bjan/pycentauri docs/PROTOCOL.md, unknown-Cmd and payload-mismatch crash mode",
)


@dataclass(frozen=True, slots=True)
class AdapterRegistration:
    """Everything the rest of the integration needs to know about one protocol."""

    id: ProtocolId
    label: str
    adapter: type[Protocol]
    #: Capabilities the wire protocol can express, before any gate is applied.
    capabilities: frozenset[Capability]
    #: Config keys this protocol needs, beyond name, host and protocol.
    fields: tuple[str, ...] = ()
    #: Credential keys this protocol needs.
    credentials: tuple[str, ...] = ()
    #: Default TCP ports, in the order the config flow should try them.
    ports: tuple[int, ...] = ()
    #: Commands and payload shapes that need an explicit opt-in.
    unsafe: tuple[UnsafeFeature, ...] = ()
    #: What is verified and what is inferred. Shown in diagnostics.
    evidence: Mapping[str, str] = field(default_factory=dict)
    #: Whether a discovery probe can identify this protocol from a host alone.
    detectable: bool = True


def _resolve(module: Any, name: str) -> type[Protocol] | None:
    """Return the adapter class from a module, or ``None`` when it is not present."""
    return getattr(module, name, None) if module is not None else None


def _all_registrations() -> dict[ProtocolId, AdapterRegistration]:
    """Build the registry, dropping any protocol whose module is not installed."""
    candidates: dict[ProtocolId, AdapterRegistration] = {
        ProtocolId.SDCP_CC1: AdapterRegistration(
            id=ProtocolId.SDCP_CC1,
            label="Elegoo SDCP (Centauri Carbon)",
            adapter=_resolve(_ADAPTER_MODULES["sdcp"], "SdcpProtocol"),  # type: ignore[arg-type]
            # START_PRINT is declared because the printer's firmware documents it, and
            # withheld by the hazard below until the user opts in. Omitting it from
            # this set would make the opt-in a no-op, which is worse than either
            # allowing it or refusing it outright: the user would answer a question
            # that changes nothing.
            capabilities=frozenset(
                {
                    Capability.START_PRINT,
                    Capability.PAUSE,
                    Capability.RESUME,
                    Capability.STOP,
                    Capability.SET_HOTEND_TEMP,
                    Capability.SET_BED_TEMP,
                    Capability.SET_CHAMBER_TEMP,
                    Capability.SET_FAN_SPEED,
                    Capability.SET_SPEED,
                    Capability.SET_LIGHT,
                    Capability.FILE_LIST,
                    Capability.FILE_UPLOAD,
                    Capability.FILE_DELETE,
                    Capability.CAMERA,
                    Capability.WEB_UI,
                }
            ),
            fields=("port", "camera_port"),
            ports=(3030,),
            unsafe=(_UNSAFE_SDCP_START_PRINT,),
            evidence={
                "verified": (
                    "commands 0, 1, 258, 320 answered a live Centauri Carbon on "
                    "firmware V1.4.49; the camera streamed multipart JPEG from port 3031"
                ),
                "inferred": (
                    "commands 128, 129, 130, 131, 259, 386 and the four 403 payload "
                    "variants come from the pycentauri field notes and Elegoo's SDK, "
                    "and were not sent to hardware by this project"
                ),
            },
        ),
        ProtocolId.MOONRAKER: AdapterRegistration(
            id=ProtocolId.MOONRAKER,
            label="Klipper via Moonraker",
            adapter=_resolve(_ADAPTER_MODULES["moonraker"], "MoonrakerProtocol"),  # type: ignore[arg-type]
            capabilities=frozenset(
                {
                    Capability.START_PRINT,
                    Capability.PAUSE,
                    Capability.RESUME,
                    Capability.STOP,
                    Capability.SET_HOTEND_TEMP,
                    Capability.SET_BED_TEMP,
                    Capability.SET_CHAMBER_TEMP,
                    Capability.SET_FAN_SPEED,
                    Capability.SET_SPEED,
                    Capability.SET_FLOW,
                    Capability.HOME,
                    Capability.JOG,
                    Capability.FILE_LIST,
                    Capability.FILE_UPLOAD,
                    Capability.FILE_DELETE,
                    Capability.CAMERA,
                }
            ),
            fields=("port", "tls", "web_url"),
            credentials=("api_key",),
            ports=(7125,),
            evidence={
                "inferred": (
                    "endpoints and field names read from Moonraker's own documentation "
                    "and from marcolivierarsenault/moonraker-home-assistant; no Moonraker "
                    "printer was present for this project to probe"
                ),
            },
        ),
        ProtocolId.OCTOPRINT: AdapterRegistration(
            id=ProtocolId.OCTOPRINT,
            label="OctoPrint",
            adapter=_resolve(_ADAPTER_MODULES["octoprint"], "OctoPrintProtocol"),  # type: ignore[arg-type]
            capabilities=frozenset(
                {
                    Capability.START_PRINT,
                    Capability.PAUSE,
                    Capability.RESUME,
                    Capability.STOP,
                    Capability.SET_HOTEND_TEMP,
                    Capability.SET_BED_TEMP,
                    Capability.SET_FAN_SPEED,
                    Capability.SET_SPEED,
                    Capability.SET_FLOW,
                    Capability.HOME,
                    Capability.JOG,
                    Capability.FILE_LIST,
                    Capability.FILE_UPLOAD,
                    Capability.FILE_DELETE,
                    Capability.CAMERA,
                    Capability.WEB_UI,
                }
            ),
            fields=("port", "tls", "web_url", "camera_port"),
            credentials=("api_key",),
            ports=(5000, 80),
            evidence={
                "inferred": (
                    "endpoints from the OctoPrint REST API documentation and the Home "
                    "Assistant octoprint integration; the default port is not consistent "
                    "across installations, so the config flow probes it"
                ),
                "verified": "no layer count is exposed by the REST API, so it stays None",
            },
        ),
        ProtocolId.PRUSALINK: AdapterRegistration(
            id=ProtocolId.PRUSALINK,
            label="PrusaLink",
            adapter=_resolve(_ADAPTER_MODULES["prusalink"], "PrusaLinkProtocol"),  # type: ignore[arg-type]
            capabilities=frozenset(
                {
                    Capability.PAUSE,
                    Capability.RESUME,
                    Capability.STOP,
                    Capability.FILE_LIST,
                    Capability.FILE_UPLOAD,
                    Capability.WEB_UI,
                }
            ),
            fields=("port", "web_url"),
            credentials=("username", "password", "api_key"),
            ports=(80, 443),
            evidence={
                "inferred": (
                    "PrusaLink has no start-print and no set-temperature operation at all, "
                    "which is why START_PRINT and the temperature capabilities are absent "
                    "here rather than faked; state and job fields come from the PrusaLink "
                    "OpenAPI description"
                ),
            },
        ),
        ProtocolId.DUET: AdapterRegistration(
            id=ProtocolId.DUET,
            label="Duet (RepRapFirmware)",
            adapter=_resolve(_ADAPTER_MODULES["duet"], "DuetProtocol"),  # type: ignore[arg-type]
            capabilities=frozenset(
                {
                    Capability.START_PRINT,
                    Capability.PAUSE,
                    Capability.RESUME,
                    Capability.STOP,
                    Capability.SET_HOTEND_TEMP,
                    Capability.SET_BED_TEMP,
                    Capability.SET_CHAMBER_TEMP,
                    Capability.SET_FAN_SPEED,
                    Capability.SET_SPEED,
                    Capability.HOME,
                    Capability.JOG,
                    Capability.FILE_LIST,
                    Capability.FILE_UPLOAD,
                    Capability.FILE_DELETE,
                    Capability.WEB_UI,
                }
            ),
            fields=("port", "tls", "web_url"),
            credentials=("password",),
            ports=(80, 443),
            evidence={
                "inferred": (
                    "the RepRapFirmware object model. Cancel needs M25 followed by M0, and "
                    "G91 covers only X, Y and Z, so the adapter sends M83 with it. No Duet "
                    "was present for this project to probe"
                ),
            },
        ),
        ProtocolId.WEB_ONLY: AdapterRegistration(
            id=ProtocolId.WEB_ONLY,
            label="Web page only, no control API",
            adapter=_resolve(_ADAPTER_MODULES["web_only"], "WebOnlyProtocol"),  # type: ignore[arg-type]
            capabilities=frozenset({Capability.WEB_UI}),
            fields=("web_url", "port", "tls"),
            credentials=("username", "password"),
            ports=(80, 443),
            evidence={
                "design": (
                    "a design choice, not a finding. A printer with no reachable API is "
                    "still worth a dashboard entry, so this adapter reports reachability "
                    "and reverse-proxies the printer's own page"
                ),
            },
        ),
    }
    return {
        protocol: registration
        for protocol, registration in candidates.items()
        if registration.adapter is not None
    }


ADAPTERS: Final[Mapping[ProtocolId, AdapterRegistration]] = MappingProxyType(
    _all_registrations()
)


def get_registration(protocol: ProtocolId) -> AdapterRegistration:
    """Return the registration for a protocol, raising for one that is absent."""
    try:
        return ADAPTERS[protocol]
    except KeyError as err:
        raise KeyError(f"protocol {protocol.value} is not installed") from err


def withheld_features(
    registration: AdapterRegistration, unsafe_enabled: frozenset[str]
) -> tuple[UnsafeFeature, ...]:
    """Return the declared hazards whose opt-in this entry did not grant."""
    return tuple(
        feature for feature in registration.unsafe if feature.id not in unsafe_enabled
    )


def granted_capabilities(
    registration: AdapterRegistration, unsafe_enabled: frozenset[str]
) -> frozenset[Capability]:
    """Return the capabilities this printer grants after every gate is applied."""
    granted = set(registration.capabilities)
    for feature in withheld_features(registration, unsafe_enabled):
        granted -= feature.gates
    return frozenset(granted)


def build_adapter(config: PrinterConfig, session: aiohttp.ClientSession) -> Protocol:
    """Construct the adapter for a parsed config. The only construction site."""
    registration = get_registration(config.protocol)
    return registration.adapter(
        config,
        session,
        granted=granted_capabilities(registration, config.unsafe_enabled),
        unsafe=withheld_features(registration, config.unsafe_enabled),
    )


def registration_fields(registration: AdapterRegistration) -> Mapping[str, Any]:
    """Return the config keys a protocol declares, for the config flow schema."""
    return {
        "fields": registration.fields,
        "credentials": registration.credentials,
        "ports": registration.ports,
        "unsafe": tuple(
            {
                "id": feature.id,
                "label": feature.label,
                "reason": feature.reason,
            }
            for feature in registration.unsafe
        ),
    }
