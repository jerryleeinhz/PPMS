from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable, Protocol

from ppms_control.config import AppConfig


class DiagnosticError(RuntimeError):
    """Raised when a hardware-only diagnostic is requested in simulation mode."""


class VisaResource(Protocol):
    timeout: int

    def query(self, command: str) -> str: ...

    def close(self) -> None: ...


class VisaResourceManager(Protocol):
    def open_resource(self, address: str) -> VisaResource: ...

    def close(self) -> None: ...


class PPMSClient(Protocol):
    temperature: object
    field: object

    def is_server_running(self) -> bool: ...

    def open(self) -> object: ...

    def get_version(self) -> str: ...

    def get_temperature(self) -> tuple[float, str]: ...

    def get_field(self) -> tuple[float, str]: ...

    def get_chamber(self) -> str: ...

    def is_steady(self, bitmask: int = 0) -> bool: ...

    def close_client(self) -> None: ...


@dataclass(frozen=True)
class DiagnosticResult:
    component: str
    endpoint: str
    ok: bool
    details: dict[str, object]
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


VisaFactory = Callable[[str], VisaResourceManager]
PPMSFactory = Callable[[str, int], PPMSClient]


_VISA_PROBES: tuple[tuple[str, str, str, tuple[tuple[str, str], ...]], ...] = (
    (
        "sr830",
        "sr830_address",
        "SR830",
        (
            ("reference_frequency_hz", "FREQ?"),
            ("harmonic", "HARM?"),
            ("sine_amplitude_v", "SLVL?"),
            ("reference_source_code", "FMOD?"),
        ),
    ),
    (
        "sr865a",
        "sr865a_address",
        "SR865",
        (
            ("reference_frequency_hz", "FREQ?"),
            ("harmonic", "HARM?"),
            ("sine_amplitude_v", "SLVL?"),
            ("reference_source_code", "RSRC?"),
        ),
    ),
    (
        "gate_top",
        "gate_top_address",
        "2400",
        (
            ("output_state_code", ":OUTP:STAT?"),
            ("source_mode", ":SOUR:FUNC?"),
            ("source_voltage_v", ":SOUR:VOLT:LEV?"),
            ("current_compliance_a", ":SENS:CURR:PROT?"),
        ),
    ),
    (
        "gate_bottom",
        "gate_bottom_address",
        "2400",
        (
            ("output_state_code", ":OUTP:STAT?"),
            ("source_mode", ":SOUR:FUNC?"),
            ("source_voltage_v", ":SOUR:VOLT:LEV?"),
            ("current_compliance_a", ":SENS:CURR:PROT?"),
        ),
    ),
)


def _default_visa_factory(backend: str) -> VisaResourceManager:
    import pyvisa

    if backend == "default":
        return pyvisa.ResourceManager()
    return pyvisa.ResourceManager(backend)


def _default_ppms_factory(host: str, port: int) -> PPMSClient:
    try:
        import MultiPyVu
    except ImportError as exc:
        raise DiagnosticError(
            "MultiPyVu is required for PPMS diagnostics. Install the real-ppms extra."
        ) from exc
    return MultiPyVu.Client(host=host, port=port)


def _clean_reply(reply: str) -> str:
    return str(reply).strip()


def _probe_visa(
    manager: VisaResourceManager,
    *,
    component: str,
    endpoint: str,
    expected_identity: str,
    queries: tuple[tuple[str, str], ...],
    timeout_ms: int,
) -> DiagnosticResult:
    resource: VisaResource | None = None
    details: dict[str, object] = {}
    error: str | None = None
    try:
        resource = manager.open_resource(endpoint)
        resource.timeout = timeout_ms
        identity = _clean_reply(resource.query("*IDN?"))
        details["identity"] = identity
        if expected_identity.upper() not in identity.upper():
            raise DiagnosticError(
                f"Identity mismatch: expected {expected_identity!r}, received {identity!r}."
            )
        for label, command in queries:
            details[label] = _clean_reply(resource.query(command))
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if resource is not None:
            try:
                resource.close()
            except Exception as exc:
                if error is None:
                    error = f"close failed: {type(exc).__name__}: {exc}"
    return DiagnosticResult(component, endpoint, error is None, details, error)


def _failed_visa_results(config: AppConfig, error: str) -> list[DiagnosticResult]:
    return [
        DiagnosticResult(
            component,
            str(getattr(config.connections, address_field)),
            False,
            {},
            error,
        )
        for component, address_field, _, _ in _VISA_PROBES
    ]


def _probe_ppms(config: AppConfig, factory: PPMSFactory) -> DiagnosticResult:
    endpoint = f"{config.connections.ppms_host}:{config.connections.ppms_port}"
    details: dict[str, object] = {}
    error: str | None = None
    client: PPMSClient | None = None
    opened = False
    try:
        client = factory(config.connections.ppms_host, config.connections.ppms_port)
        if not client.is_server_running():
            raise DiagnosticError("MultiPyVu Server did not respond.")
        client.open()
        opened = True
        temperature_k, temperature_status = client.get_temperature()
        field_oe, field_status = client.get_field()
        bitmask = client.temperature.waitfor | client.field.waitfor
        try:
            sample_position_deg, position_status = client.get_position()
            position_details: dict[str, object] = {
                "sample_position_deg": float(sample_position_deg),
                "position_status": str(position_status),
                "rotator_available": True,
            }
        except Exception as exc:
            position_details = {
                "rotator_available": False,
                "rotator_query_error": f"{type(exc).__name__}: {exc}",
            }
        details = {
            "multipyvu_version": client.get_version(),
            "temperature_k": float(temperature_k),
            "temperature_status": str(temperature_status),
            "field_oe": float(field_oe),
            "field_t": float(field_oe) / 10000.0,
            "field_status": str(field_status),
            "chamber_status": str(client.get_chamber()),
            "temperature_and_field_steady": bool(client.is_steady(bitmask)),
            **position_details,
        }
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        if client is not None and opened:
            try:
                client.close_client()
            except Exception as exc:
                if error is None:
                    error = f"close failed: {type(exc).__name__}: {exc}"
    return DiagnosticResult("ppms", endpoint, error is None, details, error)


def diagnose_hardware(
    config: AppConfig,
    *,
    visa_factory: VisaFactory | None = None,
    ppms_factory: PPMSFactory | None = None,
) -> tuple[DiagnosticResult, ...]:
    """Run identification and state queries without sending instrument set commands."""
    if config.runtime.simulation:
        raise DiagnosticError("Hardware diagnostics require runtime.simulation = false.")

    visa_factory = visa_factory or _default_visa_factory
    ppms_factory = ppms_factory or _default_ppms_factory
    results: list[DiagnosticResult] = []
    manager: VisaResourceManager | None = None
    try:
        manager = visa_factory(config.connections.visa_backend)
        for component, address_field, expected_identity, queries in _VISA_PROBES:
            results.append(
                _probe_visa(
                    manager,
                    component=component,
                    endpoint=str(getattr(config.connections, address_field)),
                    expected_identity=expected_identity,
                    queries=queries,
                    timeout_ms=config.connections.visa_timeout_ms,
                )
            )
    except Exception as exc:
        results.extend(_failed_visa_results(config, f"{type(exc).__name__}: {exc}"))
    finally:
        if manager is not None:
            try:
                manager.close()
            except Exception:
                pass

    results.append(_probe_ppms(config, ppms_factory))
    return tuple(results)
