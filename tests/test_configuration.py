from __future__ import annotations

from pathlib import Path

import pytest

from sds200 import (
    APPLICATION_CONFIG_FILENAME,
    CONFIG_DIRECTORY_NAME,
    CONNECTION_PROFILE_FILENAME,
    DEFAULT_SYSTEM_CONFIG_DIR,
    LEGACY_CONFIG_DIRECTORY_NAME,
    REMOTE_AUDIO_PROFILE_FILENAME,
    ConfigurationPaths,
    LegacyConfigurationDiscovery,
    discover_legacy_configuration,
    resolve_configuration_paths,
)
from sds200.configuration import DAEMON_RECORDING_DIRECTORY_NAME
from sds200.profiles import default_profile_path
from sds200.remote_audio_profiles import default_remote_audio_profile_path


def test_configuration_paths_use_documented_defaults(tmp_path: Path) -> None:
    home = tmp_path / "home"

    paths = resolve_configuration_paths(environ={}, home=home)

    assert paths == ConfigurationPaths(
        system_config_dir=DEFAULT_SYSTEM_CONFIG_DIR,
        user_config_dir=home / ".config" / CONFIG_DIRECTORY_NAME,
        user_state_dir=home / ".local" / "state" / CONFIG_DIRECTORY_NAME,
        user_cache_dir=home / ".cache" / CONFIG_DIRECTORY_NAME,
        legacy_user_config_dir=home
        / ".config"
        / LEGACY_CONFIG_DIRECTORY_NAME,
    )
    assert paths.system_config_file == (
        DEFAULT_SYSTEM_CONFIG_DIR / APPLICATION_CONFIG_FILENAME
    )
    assert paths.user_config_file == (
        home / ".config" / CONFIG_DIRECTORY_NAME / APPLICATION_CONFIG_FILENAME
    )
    assert paths.daemon_recording_dir == (
        paths.user_state_dir / DAEMON_RECORDING_DIRECTORY_NAME
    )
    assert paths.legacy_connection_profiles_file.name == (
        CONNECTION_PROFILE_FILENAME
    )
    assert paths.legacy_remote_audio_profiles_file.name == (
        REMOTE_AUDIO_PROFILE_FILENAME
    )


def test_configuration_paths_honor_xdg_overrides(tmp_path: Path) -> None:
    config_home = tmp_path / "xdg-config"
    state_home = tmp_path / "xdg-state"
    cache_home = tmp_path / "xdg-cache"
    system_dir = tmp_path / "etc" / CONFIG_DIRECTORY_NAME

    paths = resolve_configuration_paths(
        environ={
            "XDG_CONFIG_HOME": str(config_home),
            "XDG_STATE_HOME": str(state_home),
            "XDG_CACHE_HOME": str(cache_home),
        },
        home=tmp_path / "unused-home",
        system_config_dir=system_dir,
    )

    assert paths.system_config_dir == system_dir
    assert paths.user_config_dir == config_home / CONFIG_DIRECTORY_NAME
    assert paths.user_state_dir == state_home / CONFIG_DIRECTORY_NAME
    assert paths.user_cache_dir == cache_home / CONFIG_DIRECTORY_NAME
    assert paths.legacy_user_config_dir == (
        config_home / LEGACY_CONFIG_DIRECTORY_NAME
    )


def test_empty_xdg_values_use_home_defaults(tmp_path: Path) -> None:
    home = tmp_path / "home"

    paths = resolve_configuration_paths(
        environ={
            "XDG_CONFIG_HOME": "",
            "XDG_STATE_HOME": "",
            "XDG_CACHE_HOME": "",
        },
        home=home,
    )

    assert paths.user_config_dir == home / ".config" / CONFIG_DIRECTORY_NAME
    assert paths.user_state_dir == (
        home / ".local" / "state" / CONFIG_DIRECTORY_NAME
    )
    assert paths.user_cache_dir == home / ".cache" / CONFIG_DIRECTORY_NAME


@pytest.mark.parametrize(
    "variable",
    ["XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"],
)
def test_configuration_paths_reject_relative_xdg_values(
    variable: str,
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match=variable):
        resolve_configuration_paths(
            environ={variable: "relative/path"},
            home=tmp_path,
        )


def test_configuration_paths_reject_relative_home() -> None:
    with pytest.raises(ValueError, match="Home directory"):
        resolve_configuration_paths(environ={}, home="relative-home")


def test_legacy_discovery_does_not_create_paths(tmp_path: Path) -> None:
    paths = resolve_configuration_paths(environ={}, home=tmp_path)

    discovery = discover_legacy_configuration(paths)

    assert discovery == LegacyConfigurationDiscovery(
        root=paths.legacy_user_config_dir,
        root_exists=False,
        connection_profiles=paths.legacy_connection_profiles_file,
        connection_profiles_exists=False,
        remote_audio_profiles=paths.legacy_remote_audio_profiles_file,
        remote_audio_profiles_exists=False,
    )
    assert discovery.found is False
    assert paths.legacy_user_config_dir.exists() is False


def test_legacy_discovery_reports_known_files(tmp_path: Path) -> None:
    paths = resolve_configuration_paths(environ={}, home=tmp_path)
    paths.legacy_user_config_dir.mkdir(parents=True)
    paths.legacy_connection_profiles_file.write_text(
        "version = 4\n",
        encoding="utf-8",
    )
    paths.legacy_remote_audio_profiles_file.write_text(
        "version = 1\n",
        encoding="utf-8",
    )

    discovery = discover_legacy_configuration(paths)

    assert discovery.found is True
    assert discovery.root_exists is True
    assert discovery.connection_profiles_exists is True
    assert discovery.remote_audio_profiles_exists is True


def test_existing_profile_defaults_remain_in_legacy_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    assert default_profile_path() == (
        tmp_path / LEGACY_CONFIG_DIRECTORY_NAME / CONNECTION_PROFILE_FILENAME
    )
    assert default_remote_audio_profile_path() == (
        tmp_path
        / LEGACY_CONFIG_DIRECTORY_NAME
        / REMOTE_AUDIO_PROFILE_FILENAME
    )


def test_application_configuration_defaults_include_provenance() -> None:
    from sds200 import (
        APPLICATION_CONFIGURATION_FIELDS,
        ApplicationConfiguration,
        resolve_application_configuration,
    )

    resolved = resolve_application_configuration()

    assert resolved.configuration == ApplicationConfiguration()
    assert all(
        resolved.source_for(field) == "default"
        for field in APPLICATION_CONFIGURATION_FIELDS
    )
    assert resolved.configuration.reconnect_policy.max_attempts is None


def test_application_configuration_uses_fixed_layer_precedence(
    tmp_path: Path,
) -> None:
    from sds200 import ConfigurationLayer, resolve_application_configuration

    resolved = resolve_application_configuration(
        (
            ConfigurationLayer(
                "system",
                {
                    "theme": "light",
                    "max_xml_retries": 4,
                    "reconnect_attempts": 3,
                },
                "/etc/sdsctl/config.toml",
            ),
            ConfigurationLayer(
                "user",
                {
                    "theme": "dark",
                    "health_history_limit": 250,
                },
                str(tmp_path / "config.toml"),
            ),
            ConfigurationLayer(
                "environment",
                {
                    "theme": "light",
                    "log_level": "info",
                },
                "environment",
            ),
            ConfigurationLayer(
                "command-line",
                {
                    "theme": "dark",
                    "reconnect_attempts": 0,
                },
                "command line",
            ),
        )
    )

    config = resolved.configuration
    assert config.theme == "dark"
    assert config.max_xml_retries == 4
    assert config.health_history_limit == 250
    assert config.log_level == "INFO"
    assert config.reconnect_attempts == 0
    assert config.reconnect_policy.max_attempts is None
    assert resolved.origin_for("theme").source == "command-line"
    assert resolved.origin_for("theme").location == "command line"
    assert resolved.source_for("health_history_limit") == "user"
    assert resolved.source_for("max_xml_retries") == "system"


def test_explicit_value_equal_to_default_retains_override_provenance() -> None:
    from sds200 import ConfigurationLayer, resolve_application_configuration

    resolved = resolve_application_configuration(
        (
            ConfigurationLayer(
                "environment",
                {"color": "auto"},
                "SDSCTL_COLOR",
            ),
        )
    )

    assert resolved.configuration.color == "auto"
    assert resolved.source_for("color") == "environment"
    assert resolved.origin_for("color").location == "SDSCTL_COLOR"


def test_configuration_layers_reject_duplicate_or_out_of_order_sources() -> None:
    from sds200 import (
        ConfigurationError,
        ConfigurationLayer,
        resolve_application_configuration,
    )

    with pytest.raises(ConfigurationError, match="more than once"):
        resolve_application_configuration(
            (
                ConfigurationLayer("user", {"theme": "light"}),
                ConfigurationLayer("user", {"theme": "dark"}),
            )
        )

    with pytest.raises(ConfigurationError, match="precedence order"):
        resolve_application_configuration(
            (
                ConfigurationLayer("environment", {"theme": "light"}),
                ConfigurationLayer("system", {"theme": "dark"}),
            )
        )


def test_configuration_layer_rejects_default_source() -> None:
    from sds200 import ConfigurationLayer

    with pytest.raises(ValueError, match="Built-in defaults"):
        ConfigurationLayer("default", {})


def test_configuration_reports_unknown_fields_with_source_location() -> None:
    from sds200 import (
        ConfigurationError,
        ConfigurationLayer,
        resolve_application_configuration,
    )

    with pytest.raises(
        ConfigurationError,
        match=r"user configuration at .*config\.toml.*unsupported field",
    ):
        resolve_application_configuration(
            (
                ConfigurationLayer(
                    "user",
                    {"future_setting": True},
                    "/home/example/.config/sdsctl/config.toml",
                ),
            )
        )


def test_configuration_reports_invalid_values_with_source_location() -> None:
    from sds200 import (
        ConfigurationError,
        ConfigurationLayer,
        resolve_application_configuration,
    )

    with pytest.raises(
        ConfigurationError,
        match=r"Invalid system configuration at /etc/sdsctl/config\.toml: "
        r"Health history limit",
    ):
        resolve_application_configuration(
            (
                ConfigurationLayer(
                    "system",
                    {"health_history_limit": 0},
                    "/etc/sdsctl/config.toml",
                ),
            )
        )


def test_application_configuration_normalizes_operational_values(
    tmp_path: Path,
) -> None:
    from sds200 import ApplicationConfiguration

    config = ApplicationConfiguration(
        reconnect_initial_delay=2,
        reconnect_multiplier=1,
        reconnect_max_delay=8,
        reconnect_attempts=5,
        color=" ALWAYS ",
        theme=" LIGHT ",
        log_level=" debug ",
        log_file=tmp_path / "sdsctl.log",
    )

    assert config.reconnect_initial_delay == 2.0
    assert config.reconnect_multiplier == 1.0
    assert config.reconnect_max_delay == 8.0
    assert config.reconnect_policy.max_attempts == 5
    assert config.color == "always"
    assert config.theme == "light"
    assert config.log_level == "DEBUG"
    assert config.log_file == tmp_path / "sdsctl.log"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"max_xml_retries": True}, "Maximum XML retries must be an integer"),
        ({"reconnect_attempts": -1}, "Reconnect attempts must be at least 0"),
        (
            {"reconnect_initial_delay": 0},
            "Reconnect initial delay must be greater than 0",
        ),
        (
            {"reconnect_multiplier": 0.5},
            "Reconnect multiplier must be at least 1",
        ),
        (
            {"reconnect_initial_delay": 5, "reconnect_max_delay": 4},
            "Reconnect maximum delay must be at least the initial delay",
        ),
        ({"color": "sometimes"}, "Color mode must be one of"),
        ({"theme": "blue"}, "Theme name must be one of"),
        ({"log_level": "TRACE"}, "Log level must be one of"),
    ],
)
def test_application_configuration_rejects_invalid_values(
    overrides: dict[str, object],
    message: str,
) -> None:
    from sds200 import ApplicationConfiguration

    with pytest.raises((TypeError, ValueError), match=message):
        ApplicationConfiguration(**overrides)


def test_configuration_layer_and_provenance_are_immutable() -> None:
    from sds200 import ConfigurationLayer, resolve_application_configuration

    values = {"theme": "light"}
    layer = ConfigurationLayer("user", values, "user config")
    values["theme"] = "dark"

    assert layer.values["theme"] == "light"

    resolved = resolve_application_configuration((layer,))
    assert resolved.configuration.theme == "light"

    with pytest.raises(TypeError):
        layer.values["theme"] = "dark"  # type: ignore[index]

    with pytest.raises(TypeError):
        resolved.origins["theme"] = resolved.origin_for("theme")  # type: ignore[index]


def test_missing_configuration_sources_preserve_defaults_without_writes(
    tmp_path: Path,
) -> None:
    from sds200 import (
        ApplicationConfiguration,
        load_application_configuration,
        resolve_configuration_paths,
    )

    paths = resolve_configuration_paths(
        environ={},
        home=tmp_path / "home",
        system_config_dir=tmp_path / "etc" / "sdsctl",
    )

    resolved = load_application_configuration(paths=paths, environ={})

    assert resolved.configuration == ApplicationConfiguration()
    assert paths.system_config_file.exists() is False
    assert paths.user_config_file.exists() is False


def test_load_application_configuration_uses_all_sources(
    tmp_path: Path,
) -> None:
    from sds200 import (
        load_application_configuration,
        resolve_configuration_paths,
    )

    paths = resolve_configuration_paths(
        environ={},
        home=tmp_path / "home",
        system_config_dir=tmp_path / "etc" / "sdsctl",
    )
    paths.system_config_dir.mkdir(parents=True)
    paths.user_config_dir.mkdir(parents=True)
    paths.system_config_file.write_text(
        "version = 1\n\n"
        "[application]\n"
        "max_xml_retries = 4\n"
        "reconnect_attempts = 3\n"
        'theme = "light"\n',
        encoding="utf-8",
    )
    paths.user_config_file.write_text(
        "version = 1\n\n"
        "[application]\n"
        "health_history_limit = 250\n"
        'theme = "dark"\n',
        encoding="utf-8",
    )

    resolved = load_application_configuration(
        paths=paths,
        environ={
            "SDSCTL_THEME": "light",
            "SDSCTL_LOG_LEVEL": "debug",
        },
        command_line_values={
            "theme": "dark",
            "reconnect_attempts": 0,
        },
    )

    config = resolved.configuration
    assert config.max_xml_retries == 4
    assert config.health_history_limit == 250
    assert config.log_level == "DEBUG"
    assert config.theme == "dark"
    assert config.reconnect_policy.max_attempts is None
    assert resolved.origin_for("max_xml_retries").location == str(
        paths.system_config_file
    )
    assert resolved.origin_for("health_history_limit").location == str(
        paths.user_config_file
    )
    assert resolved.origin_for("log_level").location == "environment"
    assert resolved.origin_for("theme").location == "command line"


def test_environment_configuration_parses_supported_types(
    tmp_path: Path,
) -> None:
    from sds200 import (
        load_environment_configuration,
        resolve_application_configuration,
    )

    layer = load_environment_configuration(
        {
            "SDSCTL_MAX_XML_RETRIES": "4",
            "SDSCTL_RECONNECT_ATTEMPTS": "6",
            "SDSCTL_RECONNECT_INITIAL_DELAY": "0.5",
            "SDSCTL_RECONNECT_MULTIPLIER": "1.5",
            "SDSCTL_RECONNECT_MAX_DELAY": "12",
            "SDSCTL_HEALTH_HISTORY_LIMIT": "250",
            "SDSCTL_COLOR": "always",
            "SDSCTL_THEME": "light",
            "SDSCTL_LOG_LEVEL": "info",
            "SDSCTL_LOG_FILE": str(tmp_path / "sdsctl.log"),
            "UNRELATED_SETTING": "ignored",
        }
    )

    assert layer is not None
    resolved = resolve_application_configuration((layer,))
    config = resolved.configuration

    assert config.max_xml_retries == 4
    assert config.reconnect_attempts == 6
    assert config.reconnect_initial_delay == 0.5
    assert config.reconnect_multiplier == 1.5
    assert config.reconnect_max_delay == 12.0
    assert config.health_history_limit == 250
    assert config.color == "always"
    assert config.theme == "light"
    assert config.log_level == "INFO"
    assert config.log_file == tmp_path / "sdsctl.log"


def test_environment_configuration_returns_none_when_absent() -> None:
    from sds200 import load_environment_configuration

    assert load_environment_configuration({}) is None
    assert load_environment_configuration({"UNRELATED": "value"}) is None


@pytest.mark.parametrize(
    ("variable", "value", "expected"),
    [
        (
            "SDSCTL_MAX_XML_RETRIES",
            "not-an-integer-secret",
            "expected an integer",
        ),
        (
            "SDSCTL_RECONNECT_INITIAL_DELAY",
            "not-a-number-secret",
            "expected a number",
        ),
        (
            "SDSCTL_LOG_FILE",
            "   ",
            "path must not be empty",
        ),
    ],
)
def test_environment_configuration_errors_name_variable_without_value(
    variable: str,
    value: str,
    expected: str,
) -> None:
    from sds200 import ConfigurationError, load_environment_configuration

    with pytest.raises(ConfigurationError) as exc_info:
        load_environment_configuration({variable: value})

    message = str(exc_info.value)
    assert variable in message
    assert expected in message
    assert value not in message


@pytest.mark.parametrize(
    "document",
    [
        "",
        "version = 2\n",
        "version = true\n",
        "version = 1.0\n",
    ],
)
def test_configuration_file_rejects_unsupported_version(
    tmp_path: Path,
    document: str,
) -> None:
    from sds200 import ConfigurationError, load_configuration_file

    path = tmp_path / "config.toml"
    path.write_text(document, encoding="utf-8")

    with pytest.raises(ConfigurationError, match="version must be 1"):
        load_configuration_file(path, source="user")


def test_configuration_file_rejects_malformed_toml(tmp_path: Path) -> None:
    from sds200 import ConfigurationError, load_configuration_file

    path = tmp_path / "config.toml"
    path.write_text("version = [", encoding="utf-8")

    with pytest.raises(
        ConfigurationError,
        match=r"Could not read system configuration file .*config\.toml",
    ):
        load_configuration_file(path, source="system")


def test_configuration_file_rejects_unknown_top_level_field(
    tmp_path: Path,
) -> None:
    from sds200 import ConfigurationError, load_configuration_file

    path = tmp_path / "config.toml"
    path.write_text(
        "version = 1\nfuture = true\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ConfigurationError,
        match="unsupported top-level field",
    ):
        load_configuration_file(path, source="user")


def test_configuration_file_requires_application_table(tmp_path: Path) -> None:
    from sds200 import ConfigurationError, load_configuration_file

    path = tmp_path / "config.toml"
    path.write_text(
        'version = 1\napplication = "invalid"\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match=r"\[application\] table"):
        load_configuration_file(path, source="system")


def test_unknown_file_field_diagnostic_does_not_include_value(
    tmp_path: Path,
) -> None:
    from sds200 import (
        ConfigurationError,
        load_application_configuration,
        resolve_configuration_paths,
    )

    secret = "resolved-production-password"
    paths = resolve_configuration_paths(
        environ={},
        home=tmp_path / "home",
        system_config_dir=tmp_path / "etc" / "sdsctl",
    )
    paths.user_config_dir.mkdir(parents=True)
    paths.user_config_file.write_text(
        "version = 1\n\n"
        "[application]\n"
        f'password = "{secret}"\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError) as exc_info:
        load_application_configuration(paths=paths, environ={})

    message = str(exc_info.value)
    assert "password" in message
    assert str(paths.user_config_file) in message
    assert secret not in message


def test_nonfinite_configuration_number_is_rejected_with_source(
    tmp_path: Path,
) -> None:
    from sds200 import (
        ConfigurationError,
        load_application_configuration,
        resolve_configuration_paths,
    )

    paths = resolve_configuration_paths(
        environ={},
        home=tmp_path / "home",
        system_config_dir=tmp_path / "etc" / "sdsctl",
    )
    paths.system_config_dir.mkdir(parents=True)
    paths.system_config_file.write_text(
        "version = 1\n\n"
        "[application]\n"
        "reconnect_multiplier = nan\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ConfigurationError,
        match=r"Invalid system configuration at .*config\.toml: "
        r"Reconnect multiplier must be finite",
    ):
        load_application_configuration(paths=paths, environ={})


def test_configuration_layer_rejects_non_string_field_names() -> None:
    from sds200 import ConfigurationLayer

    with pytest.raises(TypeError, match="field names must be strings"):
        ConfigurationLayer("user", {1: "invalid"})  # type: ignore[dict-item]
