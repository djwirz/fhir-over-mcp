from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from fhir_over_mcp.config import load_fhir_settings
from fhir_over_mcp.fhir.client import is_allowed


def test_default_config_base_is_on_its_own_allowlist() -> None:
    settings = load_fhir_settings(env={})
    assert is_allowed(settings.base_url, settings.allowlist)


def test_env_overrides(tmp_path: Path) -> None:
    settings = load_fhir_settings(
        env={"FHIR_BASE_URL": "http://localhost:9999/fhir", "FHIR_ALLOWLIST": " a , b ,"}
    )
    assert settings.base_url == "http://localhost:9999/fhir"
    assert settings.allowlist == ["a", "b"]


def test_empty_allowlist_is_rejected(tmp_path: Path) -> None:
    cfg = tmp_path / "c.toml"
    cfg.write_text('[fhir]\nbase_url = "http://x"\nallowlist = []\n')
    with pytest.raises(ValidationError):
        load_fhir_settings(cfg, env={})
