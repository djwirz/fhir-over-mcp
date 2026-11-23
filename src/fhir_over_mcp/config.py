"""Configuration: a checked-in default file, overridden by environment variables. No secrets."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field

DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "default.toml"


class FhirSettings(BaseModel):
    base_url: str
    allowlist: list[str] = Field(min_length=1)
    max_pages: int = Field(default=5, ge=1, le=50)
    timeout_seconds: float = Field(default=15.0, gt=0, le=120)


def load_fhir_settings(
    path: Path = DEFAULT_CONFIG, env: dict[str, str] | None = None
) -> FhirSettings:
    environ = os.environ if env is None else env
    with path.open("rb") as f:
        raw = tomllib.load(f)["fhir"]
    if base := environ.get("FHIR_BASE_URL"):
        raw["base_url"] = base
    if allow := environ.get("FHIR_ALLOWLIST"):
        raw["allowlist"] = [u.strip() for u in allow.split(",") if u.strip()]
    return FhirSettings.model_validate(raw)
