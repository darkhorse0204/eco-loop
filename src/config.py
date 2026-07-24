"""small typed wrapper around config.yaml."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_raw(path: str | Path | None = None) -> dict[str, Any]:
    path = Path(path) if path else REPO_ROOT / "config.yaml"
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@dataclass
class Config:
    raw: dict[str, Any]

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        return cls(load_raw(path))

    def __getitem__(self, k: str) -> Any:
        return self.raw[k]

    # handy resolved paths
    @property
    def idf_path(self) -> Path:
        return (REPO_ROOT / self.raw["simulation"]["idf"]).resolve()

    @property
    def weather_path(self) -> Path:
        return (REPO_ROOT / self.raw["simulation"]["weather"]).resolve()

    @property
    def output_dir(self) -> Path:
        d = (REPO_ROOT / self.raw["output"]["dir"]).resolve()
        d.mkdir(parents=True, exist_ok=True)
        return d
