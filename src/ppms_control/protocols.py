from __future__ import annotations

from ppms_control.acquisition import MeasurementEngine
from ppms_control.config import CurrentSweepConfig
from ppms_control.models import MeasurementCondition
from ppms_control.store import RunStore


def current_sweep_conditions(config: CurrentSweepConfig) -> tuple[MeasurementCondition, ...]:
    if config.points == 1:
        currents = [config.start_current_a]
    else:
        step = (config.stop_current_a - config.start_current_a) / (config.points - 1)
        currents = [config.start_current_a + index * step for index in range(config.points)]
    return tuple(
        MeasurementCondition(
            sequence_index=index,
            current_a=current,
            temperature_k=config.target_temperature_k,
            field_t=config.target_field_t,
        )
        for index, current in enumerate(currents)
    )


def run_current_sweep(
    engine: MeasurementEngine,
    store: RunStore,
    run_id: str,
    config: CurrentSweepConfig,
) -> int:
    accepted_ids = store.accepted_condition_ids(run_id)
    measured = 0
    for condition in current_sweep_conditions(config):
        if condition.condition_id in accepted_ids:
            continue
        engine.acquire(condition)
        measured += 1
    return measured
