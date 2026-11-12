"""Plant configuration (data/plant.yaml): parsed and validated before anything touches the DB."""

from datetime import date
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.db.models import MachineType, SensorKind


class _Strict(BaseModel):
    # extra="forbid": a typo'd key like `normal_mx` is an error, not a silently ignored field.
    # frozen: config is read-only once loaded.
    model_config = ConfigDict(extra="forbid", frozen=True)


class SensorSpec(_Strict):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)
    kind: SensorKind
    unit: str = Field(max_length=16)
    normal_min: float
    normal_max: float

    @model_validator(mode="after")
    def _range_is_valid(self) -> Self:
        if self.normal_min >= self.normal_max:
            raise ValueError(f"{self.name}: normal_min must be < normal_max")
        return self


class MachineSpec(_Strict):
    code: str = Field(pattern=r"^M-\d{2}$")
    name: str = Field(max_length=100)
    line: str = Field(max_length=32)
    type: MachineType
    install_date: date


class PlantConfig(_Strict):
    sensor_templates: dict[MachineType, list[SensorSpec]]
    machines: list[MachineSpec]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        codes = [m.code for m in self.machines]
        if dupes := {c for c in codes if codes.count(c) > 1}:
            raise ValueError(f"duplicate machine codes: {sorted(dupes)}")
        for mtype, sensors in self.sensor_templates.items():
            names = [s.name for s in sensors]
            if len(names) != len(set(names)):
                raise ValueError(f"duplicate sensor names in template {mtype}")
        if missing := {m.type for m in self.machines} - self.sensor_templates.keys():
            raise ValueError(f"machine types without a sensor template: {sorted(missing)}")
        return self

    def sensors_for(self, machine: MachineSpec) -> list[SensorSpec]:
        return self.sensor_templates[machine.type]


def load_plant(path: Path) -> PlantConfig:
    """Read and validate the plant YAML. Raises pydantic.ValidationError with the bad field."""
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)  # safe_load: never constructs arbitrary Python objects
    return PlantConfig.model_validate(raw)
