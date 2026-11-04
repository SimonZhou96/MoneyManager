from __future__ import annotations

from datetime import date
from typing import Callable


class OptionCandidateReplayAdapter:
    def __init__(self, snapshot_loader: Callable[[str], dict | None]):
        self.snapshot_loader = snapshot_loader

    def replay(self, candidate_id: str, run_id: str, replay_date: date) -> dict:
        snapshot = self.snapshot_loader(run_id)
        if not snapshot:
            return {
                "candidate_id": candidate_id,
                "run_id": run_id,
                "replay_date": replay_date.isoformat(),
                "mode": "limited_option_replay",
                "warnings": ["missing_option_snapshot"],
                "signals": [],
            }
        return {
            "candidate_id": candidate_id,
            "run_id": run_id,
            "replay_date": replay_date.isoformat(),
            "mode": "limited_option_replay",
            "warnings": [],
            "snapshot": snapshot,
        }
