from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4

from qcodes.instrument import Instrument
from qcodes.parameters import Parameter
from qcodes.station import Station

from ppms_control.config import AppConfig
from ppms_control.instruments import InstrumentBundle
from ppms_control.models import GateState, LockinReading, PPMSState


class QcodesLockinAdapter:
    """Adapt an SR830/SR865A QCoDeS driver to the measurement interface."""

    def __init__(self, driver: Any, *, safe_idle_voltage_v: float | None = None) -> None:
        self._driver = driver
        self._safe_idle_voltage_v = safe_idle_voltage_v
        self.name = str(driver.name)

    def _status_byte(self) -> int:
        return int(float(str(self._driver.ask("LIAS?")).strip()))

    def reference_status(self) -> tuple[bool, float]:
        status = self._status_byte()
        frequency_hz = float(self._driver.frequency.get())
        return not bool(status & 0b1000), frequency_hz

    def set_harmonic(self, harmonic: int) -> None:
        if harmonic not in {1, 2, 3}:
            raise ValueError("Harmonic must be 1, 2, or 3.")
        self._driver.harmonic.set(harmonic)

    def acquire(self, source_voltage_v: float) -> LockinReading:
        status = self._status_byte()
        return LockinReading(
            x_v=float(self._driver.X.get()),
            y_v=float(self._driver.Y.get()),
            frequency_hz=float(self._driver.frequency.get()),
            harmonic=int(self._driver.harmonic.get()),
            reference_locked=not bool(status & 0b1000),
            overload=bool(status & 0b0111),
        )

    def set_source_voltage(self, voltage_v: float) -> None:
        if self._safe_idle_voltage_v is None:
            raise RuntimeError(f"{self.name} is not configured as the excitation source.")
        self._driver.amplitude.set(float(voltage_v))

    def read_source_voltage(self) -> float:
        if self._safe_idle_voltage_v is None:
            raise RuntimeError(f"{self.name} is not configured as the excitation source.")
        return float(self._driver.amplitude.get())

    def set_source_frequency(self, frequency_hz: float) -> None:
        if self._safe_idle_voltage_v is None:
            raise RuntimeError(f"{self.name} is not configured as the excitation source.")
        self._driver.frequency.set(float(frequency_hz))

    def read_source_frequency(self) -> float:
        return float(self._driver.frequency.get())

    def retreat_to_safe_state(self) -> None:
        if self._safe_idle_voltage_v is None:
            raise RuntimeError(f"{self.name} is not configured as the excitation source.")
        self.set_source_voltage(self._safe_idle_voltage_v)

    def close(self) -> None:
        self._driver.close()


class QcodesKeithley2400Gate:
    """Adapt the QCoDeS Keithley 2400 driver to a voltage-gate interface."""

    def __init__(self, driver: Any) -> None:
        self._driver = driver
        self.name = str(driver.name)

    def set_compliance(self, compliance_a: float) -> None:
        self._driver.compliancei.set(float(compliance_a))

    def set_voltage(self, voltage_v: float) -> None:
        self._driver.volt.set(float(voltage_v))

    def set_output(self, enabled: bool) -> None:
        self._driver.output.set(bool(enabled))

    def measure_leakage(self) -> float:
        if not bool(self._driver.output.get()):
            return 0.0
        return float(self._driver.curr.get())

    def read_state(self) -> GateState:
        output_enabled = bool(self._driver.output.get())
        return GateState(
            source_voltage_v=float(self._driver.ask(":SOUR:VOLT:LEV?")),
            output_enabled=output_enabled,
            compliance_a=float(self._driver.compliancei.get()),
            measured_current_a=float(self._driver.curr.get()) if output_enabled else None,
        )

    def close(self) -> None:
        self._driver.close()


class MultiPyVuPPMS(Instrument):
    """Adapt an open MultiPyVu Client to PPMS temperature and field control."""

    def __init__(self, name: str, client: Any) -> None:
        super().__init__(name)
        self._client = client
        self.temperature_k: Parameter = self.add_parameter(
            "temperature_k",
            label="PPMS temperature",
            unit="K",
            get_cmd=self._read_temperature,
        )
        self.field_t: Parameter = self.add_parameter(
            "field_t",
            label="PPMS magnetic field",
            unit="T",
            get_cmd=self._read_field,
        )
        self.stable: Parameter = self.add_parameter(
            "stable",
            label="PPMS temperature and field steady",
            get_cmd=self._read_stable,
        )

    @classmethod
    def connect(
        cls,
        host: str,
        port: int,
        *,
        name: str = "ppms",
        client_factory: Callable[[str, int], Any] | None = None,
    ) -> "MultiPyVuPPMS":
        if client_factory is None:
            import MultiPyVu

            client_factory = lambda client_host, client_port: MultiPyVu.Client(
                host=client_host,
                port=client_port,
            )
        client = client_factory(host, port)
        client.open()
        return cls(name, client)

    def get_idn(self) -> dict[str, str | None]:
        return {
            "vendor": "Quantum Design",
            "model": "MultiPyVu PPMS",
            "serial": None,
            "firmware": str(self._client.get_version()),
        }

    def _read_temperature(self) -> float:
        value, _ = self._client.get_temperature()
        return float(value)

    def _read_field(self) -> float:
        value_oe, _ = self._client.get_field()
        return float(value_oe) / 10000.0

    def _read_stable(self) -> bool:
        bitmask = self._client.temperature.waitfor | self._client.field.waitfor
        return bool(self._client.is_steady(bitmask))

    def read_temperature(self) -> float:
        return float(self.temperature_k.get())

    def read_field(self) -> float:
        return float(self.field_t.get())

    def is_stable(self) -> bool:
        return bool(self.stable.get())

    def read_state(self) -> PPMSState:
        temperature_k, temperature_status = self._client.get_temperature()
        field_oe, field_status = self._client.get_field()
        try:
            sample_position_deg, position_status = self._client.get_position()
        except Exception:
            sample_position_deg = None
            position_status = None
        return PPMSState(
            temperature_k=float(temperature_k),
            temperature_status=str(temperature_status),
            field_t=float(field_oe) / 10000.0,
            field_status=str(field_status),
            chamber_status=str(self._client.get_chamber()),
            sample_position_deg=(
                float(sample_position_deg) if sample_position_deg is not None else None
            ),
            position_status=(str(position_status) if position_status is not None else None),
            stable=self._read_stable(),
        )

    def set_temperature(self, target_k: float, rate_k_per_min: float) -> None:
        self._client.set_temperature(
            float(target_k),
            float(rate_k_per_min),
            self._client.temperature.approach_mode.no_overshoot,
        )

    def set_field(self, target_t: float, rate_t_per_s: float) -> None:
        self._client.set_field(
            float(target_t) * 10000.0,
            float(rate_t_per_s) * 10000.0,
            self._client.field.approach_mode.linear,
            self._client.field.driven_mode.driven,
        )

    def close(self) -> None:
        if not Instrument.is_valid(self):
            return
        try:
            self._client.close_client()
        finally:
            super().close()


@dataclass(frozen=True)
class RealDriverFactories:
    sr830: Callable[..., Any]
    sr865a: Callable[..., Any]
    keithley2400: Callable[..., Any]
    station: Callable[..., Station]


def _default_driver_factories() -> RealDriverFactories:
    from qcodes.instrument_drivers.Keithley.Keithley_2400 import Keithley2400
    from qcodes.instrument_drivers.stanford_research.SR830 import SR830
    from qcodes.instrument_drivers.stanford_research.SR865A import SR865A

    return RealDriverFactories(
        sr830=SR830,
        sr865a=SR865A,
        keithley2400=Keithley2400,
        station=lambda *components: Station(*components, default=False),
    )


def _retreat_gate_driver(driver: Any) -> list[str]:
    failures: list[str] = []
    try:
        driver.volt.set(0.0)
    except Exception as exc:
        failures.append(f"voltage zero failed: {exc}")
    try:
        driver.output.set(False)
    except Exception as exc:
        failures.append(f"output disable failed: {exc}")
    return failures


def build_real_bundle(
    config: AppConfig,
    *,
    factories: RealDriverFactories | None = None,
    ppms_client_factory: Callable[[str, int], Any] | None = None,
) -> InstrumentBundle:
    """Build drivers with the SR830 sine output at its configured safe idle."""
    if config.runtime.simulation:
        raise ValueError("Real instruments require runtime.simulation = false.")

    factories = factories or _default_driver_factories()
    suffix = uuid4().hex[:8]
    created_drivers: list[Any] = []
    gate_drivers: list[Any] = []
    ppms: MultiPyVuPPMS | None = None
    sr830_adapter: QcodesLockinAdapter | None = None
    try:
        visa_kwargs = {
            "timeout": config.connections.visa_timeout_ms / 1000.0,
            "device_clear": False,
            "visalib": (
                None
                if config.connections.visa_backend == "default"
                else config.connections.visa_backend
            ),
        }
        sr830_driver = factories.sr830(
            f"sr830_{suffix}",
            config.connections.sr830_address,
            **visa_kwargs,
        )
        created_drivers.append(sr830_driver)
        sr830_adapter = QcodesLockinAdapter(
            sr830_driver,
            safe_idle_voltage_v=config.safety.source_safe_idle_voltage_v,
        )
        sr830_driver.reference_source.set("internal")
        sr830_driver.frequency.set(config.instruments.reference_frequency_hz)
        sr830_driver.harmonic.set(1)
        sr830_adapter.retreat_to_safe_state()

        sr865a_driver = factories.sr865a(
            f"sr865a_{suffix}",
            config.connections.sr865a_address,
            reset=False,
            **visa_kwargs,
        )
        created_drivers.append(sr865a_driver)
        sr865a_driver.reference_source.set("EXT")
        sr865a_driver.harmonic.set(1)
        gate_top_driver = factories.keithley2400(
            f"gate_top_{suffix}",
            config.connections.gate_top_address,
            **visa_kwargs,
        )
        created_drivers.append(gate_top_driver)
        gate_drivers.append(gate_top_driver)
        gate_bottom_driver = factories.keithley2400(
            f"gate_bottom_{suffix}",
            config.connections.gate_bottom_address,
            **visa_kwargs,
        )
        created_drivers.append(gate_bottom_driver)
        gate_drivers.append(gate_bottom_driver)

        for gate_driver in gate_drivers:
            gate_driver.output.set(False)
            gate_driver.mode.set("VOLT")
            gate_driver.sense.set("CURR")
            gate_driver.compliancei.set(config.safety.gate_compliance_limit_a)
            gate_driver.volt.set(0.0)

        ppms = MultiPyVuPPMS.connect(
            config.connections.ppms_host,
            config.connections.ppms_port,
            name=f"ppms_{suffix}",
            client_factory=ppms_client_factory,
        )
        station = factories.station(
            sr830_driver,
            sr865a_driver,
            gate_top_driver,
            gate_bottom_driver,
            ppms,
        )
        return InstrumentBundle(
            qcodes_station=station,
            sr830=sr830_adapter,
            sr865a=QcodesLockinAdapter(sr865a_driver),
            gate_top=QcodesKeithley2400Gate(gate_top_driver),
            gate_bottom=QcodesKeithley2400Gate(gate_bottom_driver),
            ppms=ppms,
            excitation=sr830_adapter,
        )
    except BaseException:
        if sr830_adapter is not None:
            try:
                sr830_adapter.retreat_to_safe_state()
            except Exception:
                pass
        for gate_driver in gate_drivers:
            _retreat_gate_driver(gate_driver)
        if ppms is not None:
            try:
                ppms.close()
            except Exception:
                pass
        for driver in reversed(created_drivers):
            try:
                driver.close()
            except Exception:
                pass
        raise
