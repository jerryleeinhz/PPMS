from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ppms_control.eto_data import (
    EtoDataFollower,
    EtoFollowCheckpoint,
    eto_transport_readings,
)
from ppms_control.store import RunStore


@dataclass(frozen=True)
class EtoFollowBatch:
    source_path: Path
    new_records: int
    new_transport_readings: int
    total_records: int
    checkpoint: EtoFollowCheckpoint


def ingest_eto_increment(
    store: RunStore,
    run_id: str,
    source_path: str | Path,
    channel_roles: Mapping[int, str],
    *,
    final: bool = False,
) -> EtoFollowBatch:
    """Ingest one available ETO file increment and persist its cursor atomically."""

    source = Path(source_path).resolve()
    saved = store.load_eto_follow_checkpoint(run_id, source)
    checkpoint = None if saved is None else EtoFollowCheckpoint.from_dict(saved)
    follower = EtoDataFollower(source, checkpoint=checkpoint)
    data = follower.poll(final=final)
    readings = eto_transport_readings(data, channel_roles)
    store.record_eto_follow_batch(
        run_id,
        source,
        readings,
        follower.checkpoint.as_dict(),
    )
    return EtoFollowBatch(
        source_path=source,
        new_records=len(data.records),
        new_transport_readings=len(readings),
        total_records=follower.checkpoint.records_read,
        checkpoint=follower.checkpoint,
    )
