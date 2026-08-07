from __future__ import annotations

from dataclasses import dataclass

from ppms_control.config import AppConfig
from ppms_control.store import RunStore


REAL_CONTROL_CONFIRMATION = "I CONFIRM REAL HARDWARE CONTROL"


class AuthorizationError(RuntimeError):
    """Raised before driver creation when real-control authorization is invalid."""


@dataclass(frozen=True)
class HardwareControlAuthorization:
    diagnostic_run_id: str


def authorize_real_control(
    config: AppConfig,
    store: RunStore,
    *,
    confirmation: str,
    diagnostic_run_id: str,
) -> HardwareControlAuthorization:
    if config.runtime.simulation:
        raise AuthorizationError("Real control requires runtime.simulation = false.")
    if confirmation != REAL_CONTROL_CONFIRMATION:
        raise AuthorizationError("The exact real-hardware confirmation phrase is required.")
    store.require_completed_diagnostic(diagnostic_run_id, config.canonical_json())
    return HardwareControlAuthorization(diagnostic_run_id=diagnostic_run_id)
