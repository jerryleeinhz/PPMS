from __future__ import annotations

import time

from ppms_control.acquisition import MeasurementEngine
from ppms_control.config import (
    FieldSweepConfig,
    FrequencySweepConfig,
    GateSweepConfig,
    TemperatureFieldSweepConfig,
    VoltageSweepConfig,
)
from ppms_control.models import MeasurementCondition
from ppms_control.store import RunStore
from ppms_control.safety import SafeStation


def prepare_voltage_sweep(station: SafeStation, config: VoltageSweepConfig) -> None:
    station.set_temperature(
        config.target_temperature_k,
        config.temperature_rate_k_per_min,
    )
    station.set_field(config.target_field_t, config.field_rate_t_per_s)
    station.wait_for_environment(
        config.target_temperature_k,
        config.target_field_t,
        timeout_s=config.stabilization_timeout_s,
        poll_s=config.stability_poll_s,
    )


def voltage_sweep_conditions(
    config: VoltageSweepConfig,
    *,
    frequency_hz: float,
    series_resistance_ohm: float,
) -> tuple[MeasurementCondition, ...]:
    if config.points == 1:
        voltages = [config.start_voltage_v]
    else:
        step = (config.stop_voltage_v - config.start_voltage_v) / (config.points - 1)
        voltages = [config.start_voltage_v + index * step for index in range(config.points)]
    return tuple(
        MeasurementCondition(
            sequence_index=index,
            source_voltage_v=voltage,
            estimated_current_a=voltage / series_resistance_ohm,
            frequency_hz=frequency_hz,
            temperature_k=config.target_temperature_k,
            field_t=config.target_field_t,
        )
        for index, voltage in enumerate(voltages)
    )


def run_voltage_sweep(
    engine: MeasurementEngine,
    store: RunStore,
    run_id: str,
    config: VoltageSweepConfig,
    *,
    frequency_hz: float,
    series_resistance_ohm: float,
) -> int:
    accepted_ids = store.accepted_condition_ids(run_id)
    measured = 0
    for condition in voltage_sweep_conditions(
        config,
        frequency_hz=frequency_hz,
        series_resistance_ohm=series_resistance_ohm,
    ):
        if condition.condition_id in accepted_ids:
            continue
        engine.acquire(condition)
        measured += 1
    return measured


def prepare_frequency_sweep(station: SafeStation, config: FrequencySweepConfig) -> None:
    station.set_temperature(
        config.target_temperature_k,
        config.temperature_rate_k_per_min,
    )
    station.set_field(config.target_field_t, config.field_rate_t_per_s)
    station.wait_for_environment(
        config.target_temperature_k,
        config.target_field_t,
        timeout_s=config.stabilization_timeout_s,
        poll_s=config.stability_poll_s,
    )


def frequency_sweep_conditions(
    config: FrequencySweepConfig,
    *,
    series_resistance_ohm: float,
) -> tuple[MeasurementCondition, ...]:
    if config.points == 1:
        frequencies = [config.start_frequency_hz]
    else:
        step = (config.stop_frequency_hz - config.start_frequency_hz) / (config.points - 1)
        frequencies = [
            config.start_frequency_hz + index * step for index in range(config.points)
        ]
    return tuple(
        MeasurementCondition(
            sequence_index=index,
            source_voltage_v=config.source_voltage_v,
            estimated_current_a=config.source_voltage_v / series_resistance_ohm,
            frequency_hz=frequency_hz,
            temperature_k=config.target_temperature_k,
            field_t=config.target_field_t,
        )
        for index, frequency_hz in enumerate(frequencies)
    )


def run_frequency_sweep(
    engine: MeasurementEngine,
    store: RunStore,
    run_id: str,
    config: FrequencySweepConfig,
    *,
    series_resistance_ohm: float,
) -> int:
    accepted_ids = store.accepted_condition_ids(run_id)
    measured = 0
    for condition in frequency_sweep_conditions(
        config,
        series_resistance_ohm=series_resistance_ohm,
    ):
        if condition.condition_id in accepted_ids:
            continue
        engine.acquire(condition)
        measured += 1
    return measured


def prepare_field_sweep(station: SafeStation, config: FieldSweepConfig) -> None:
    station.set_temperature(
        config.target_temperature_k,
        config.temperature_rate_k_per_min,
    )
    station.set_field(config.start_field_t, config.field_rate_t_per_s)
    station.wait_for_environment(
        config.target_temperature_k,
        config.start_field_t,
        timeout_s=config.stabilization_timeout_s,
        poll_s=config.stability_poll_s,
    )


def field_sweep_conditions(
    config: FieldSweepConfig,
    *,
    series_resistance_ohm: float,
) -> tuple[MeasurementCondition, ...]:
    if config.points == 1:
        fields = [config.start_field_t]
    else:
        step = (config.stop_field_t - config.start_field_t) / (config.points - 1)
        fields = [config.start_field_t + index * step for index in range(config.points)]
    return tuple(
        MeasurementCondition(
            sequence_index=index,
            source_voltage_v=config.source_voltage_v,
            estimated_current_a=config.source_voltage_v / series_resistance_ohm,
            frequency_hz=config.frequency_hz,
            temperature_k=config.target_temperature_k,
            field_t=field_t,
        )
        for index, field_t in enumerate(fields)
    )


def run_field_sweep(
    engine: MeasurementEngine,
    station: SafeStation,
    store: RunStore,
    run_id: str,
    config: FieldSweepConfig,
    *,
    series_resistance_ohm: float,
) -> int:
    accepted_ids = store.accepted_condition_ids(run_id)
    measured = 0
    for condition in field_sweep_conditions(
        config,
        series_resistance_ohm=series_resistance_ohm,
    ):
        if condition.condition_id in accepted_ids:
            continue
        station.set_field(condition.field_t, config.field_rate_t_per_s)
        station.wait_for_environment(
            condition.temperature_k,
            condition.field_t,
            timeout_s=config.stabilization_timeout_s,
            poll_s=config.stability_poll_s,
        )
        engine.acquire(condition)
        measured += 1
    return measured


def prepare_temperature_field_sweep(
    station: SafeStation,
    config: TemperatureFieldSweepConfig,
) -> None:
    station.set_temperature(
        config.start_temperature_k,
        config.temperature_rate_k_per_min,
    )
    station.set_field(config.start_field_t, config.field_rate_t_per_s)
    station.wait_for_environment(
        config.start_temperature_k,
        config.start_field_t,
        timeout_s=config.stabilization_timeout_s,
        poll_s=config.stability_poll_s,
    )


def temperature_field_sweep_conditions(
    config: TemperatureFieldSweepConfig,
    *,
    series_resistance_ohm: float,
) -> tuple[MeasurementCondition, ...]:
    if config.temperature_points == 1:
        temperatures = [config.start_temperature_k]
    else:
        temperature_step = (
            config.stop_temperature_k - config.start_temperature_k
        ) / (config.temperature_points - 1)
        temperatures = [
            config.start_temperature_k + index * temperature_step
            for index in range(config.temperature_points)
        ]
    if config.field_points == 1:
        fields = [config.start_field_t]
    else:
        field_step = (config.stop_field_t - config.start_field_t) / (
            config.field_points - 1
        )
        fields = [
            config.start_field_t + index * field_step
            for index in range(config.field_points)
        ]

    return tuple(
        MeasurementCondition(
            sequence_index=temperature_index * len(fields) + field_index,
            source_voltage_v=config.source_voltage_v,
            estimated_current_a=config.source_voltage_v / series_resistance_ohm,
            frequency_hz=config.frequency_hz,
            temperature_k=temperature_k,
            field_t=field_t,
        )
        for temperature_index, temperature_k in enumerate(temperatures)
        for field_index, field_t in enumerate(fields)
    )


def run_temperature_field_sweep(
    engine: MeasurementEngine,
    station: SafeStation,
    store: RunStore,
    run_id: str,
    config: TemperatureFieldSweepConfig,
    *,
    series_resistance_ohm: float,
) -> int:
    accepted_ids = store.accepted_condition_ids(run_id)
    measured = 0
    current_temperature_k: float | None = None
    current_field_t: float | None = None
    for condition in temperature_field_sweep_conditions(
        config,
        series_resistance_ohm=series_resistance_ohm,
    ):
        if condition.condition_id in accepted_ids:
            continue
        if condition.temperature_k != current_temperature_k:
            station.set_temperature(
                condition.temperature_k,
                config.temperature_rate_k_per_min,
            )
            current_temperature_k = condition.temperature_k
        if condition.field_t != current_field_t:
            station.set_field(condition.field_t, config.field_rate_t_per_s)
            current_field_t = condition.field_t
        station.wait_for_environment(
            condition.temperature_k,
            condition.field_t,
            timeout_s=config.stabilization_timeout_s,
            poll_s=config.stability_poll_s,
        )
        engine.acquire(condition)
        measured += 1
    return measured


def prepare_gate_sweep(station: SafeStation, config: GateSweepConfig) -> None:
    station.set_gates(0.0, 0.0)
    station.set_temperature(
        config.target_temperature_k,
        config.temperature_rate_k_per_min,
    )
    station.set_field(config.target_field_t, config.field_rate_t_per_s)
    station.wait_for_environment(
        config.target_temperature_k,
        config.target_field_t,
        timeout_s=config.stabilization_timeout_s,
        poll_s=config.stability_poll_s,
    )


def gate_sweep_conditions(
    config: GateSweepConfig,
    *,
    series_resistance_ohm: float,
) -> tuple[MeasurementCondition, ...]:
    if config.top_gate_points == 1:
        top_gate_voltages = [config.start_top_gate_v]
    else:
        top_step = (config.stop_top_gate_v - config.start_top_gate_v) / (
            config.top_gate_points - 1
        )
        top_gate_voltages = [
            config.start_top_gate_v + index * top_step
            for index in range(config.top_gate_points)
        ]
    if config.bottom_gate_points == 1:
        bottom_gate_voltages = [config.start_bottom_gate_v]
    else:
        bottom_step = (config.stop_bottom_gate_v - config.start_bottom_gate_v) / (
            config.bottom_gate_points - 1
        )
        bottom_gate_voltages = [
            config.start_bottom_gate_v + index * bottom_step
            for index in range(config.bottom_gate_points)
        ]

    conditions: list[MeasurementCondition] = []
    for top_index, top_gate_v in enumerate(top_gate_voltages):
        bottom_row = (
            bottom_gate_voltages
            if top_index % 2 == 0
            else list(reversed(bottom_gate_voltages))
        )
        for bottom_gate_v in bottom_row:
            conditions.append(
                MeasurementCondition(
                    sequence_index=len(conditions),
                    source_voltage_v=config.source_voltage_v,
                    estimated_current_a=config.source_voltage_v / series_resistance_ohm,
                    frequency_hz=config.frequency_hz,
                    temperature_k=config.target_temperature_k,
                    field_t=config.target_field_t,
                    gate_top_voltage_v=top_gate_v,
                    gate_bottom_voltage_v=bottom_gate_v,
                )
            )
    return tuple(conditions)


def run_gate_sweep(
    engine: MeasurementEngine,
    station: SafeStation,
    store: RunStore,
    run_id: str,
    config: GateSweepConfig,
    *,
    series_resistance_ohm: float,
) -> int:
    accepted_ids = store.accepted_condition_ids(run_id)
    measured = 0
    for condition in gate_sweep_conditions(
        config,
        series_resistance_ohm=series_resistance_ohm,
    ):
        if condition.condition_id in accepted_ids:
            continue
        station.ramp_gates(
            condition.gate_top_voltage_v,
            condition.gate_bottom_voltage_v,
            max_step_v=config.gate_ramp_step_v,
            step_delay_s=config.gate_ramp_step_delay_s,
        )
        if config.gate_settle_s:
            time.sleep(config.gate_settle_s)
        engine.acquire(condition)
        measured += 1

    station.ramp_gates(
        0.0,
        0.0,
        max_step_v=config.gate_ramp_step_v,
        step_delay_s=config.gate_ramp_step_delay_s,
    )
    return measured
