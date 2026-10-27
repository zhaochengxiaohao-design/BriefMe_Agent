"""Data models for accuracy report."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List

from .dict import BINS


@dataclass
class SampleRow:
    time: datetime
    values: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"time": self.time, **{b: self.values[b] for b in BINS if b in self.values}}


@dataclass
class AccuracyRow:
    time: datetime
    n_visual: int
    manual: dict
    visual: dict
    errors: dict
    mae: float

    def to_dict(self) -> dict:
        out = {"time": self.time, "n_visual": self.n_visual, "mae": self.mae}
        for b in BINS:
            out[f"manual_{b}"] = self.manual[b]
            out[f"visual_{b}"] = self.visual[b]
            out[f"err_{b}"] = self.errors[b]
        return out
