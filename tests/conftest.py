from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from sds200 import cli
from sds200.configuration import (
    ENVIRONMENT_CONFIGURATION_VARIABLES,
    ApplicationConfiguration,
    ConfigurationPaths,
    ResolvedApplicationConfiguration,
    resolve_configuration_paths,
)


@pytest.fixture(autouse=True)
def isolate_cli_application_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep CLI tests independent of configuration installed on the host."""

    for _, variable in ENVIRONMENT_CONFIGURATION_VARIABLES:
        monkeypatch.delenv(variable, raising=False)

    isolated_paths = resolve_configuration_paths(
        environ={},
        home=tmp_path / "configuration-home",
        system_config_dir=tmp_path / "configuration-etc" / "sdsctl",
    )
    original_loader = cli.load_application_configuration

    def load_isolated_application_configuration(
        *,
        paths: ConfigurationPaths | None = None,
        environ: Mapping[str, str] | None = None,
        command_line_values: Mapping[str, object] | None = None,
        defaults: ApplicationConfiguration | None = None,
    ) -> ResolvedApplicationConfiguration:
        return original_loader(
            paths=isolated_paths if paths is None else paths,
            environ=environ,
            command_line_values=command_line_values,
            defaults=defaults,
        )

    monkeypatch.setattr(
        cli,
        "load_application_configuration",
        load_isolated_application_configuration,
    )
