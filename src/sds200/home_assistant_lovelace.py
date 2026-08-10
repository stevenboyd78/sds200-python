from __future__ import annotations

import os
from importlib.resources import files
from pathlib import Path
from tempfile import NamedTemporaryFile

from .exceptions import SDS200Error

HOME_ASSISTANT_LOVELACE_CARD_FILENAME = "sds200-card.js"
HOME_ASSISTANT_LOVELACE_CARD_DIRECTORY = Path("/homeassistant/www/sds200")
HOME_ASSISTANT_LOVELACE_CARD_PATH = (
    HOME_ASSISTANT_LOVELACE_CARD_DIRECTORY / HOME_ASSISTANT_LOVELACE_CARD_FILENAME
)
HOME_ASSISTANT_LOVELACE_CARD_RESOURCE_URL = "/local/sds200/sds200-card.js"
_HOME_ASSISTANT_LOVELACE_CARD_MODE = 0o644
_WEB_ASSET_PACKAGE = "sds200.web_assets"


def _card_bytes() -> bytes:
    return files(_WEB_ASSET_PACKAGE).joinpath(HOME_ASSISTANT_LOVELACE_CARD_FILENAME).read_bytes()


def install_home_assistant_lovelace_card(
    destination: str | Path = HOME_ASSISTANT_LOVELACE_CARD_PATH,
) -> Path:
    """Atomically install the packaged read-only card into Home Assistant www."""

    target = Path(destination)

    if not target.is_absolute():
        raise ValueError("Home Assistant Lovelace card destination must be absolute.")
    if target.name != HOME_ASSISTANT_LOVELACE_CARD_FILENAME:
        raise ValueError(
            "Home Assistant Lovelace card destination must use "
            f"{HOME_ASSISTANT_LOVELACE_CARD_FILENAME!r}."
        )

    parent = target.parent
    www = parent.parent

    for path in (www, parent, target):
        if path.is_symlink():
            raise SDS200Error(f"Home Assistant Lovelace card installation refuses symlinks: {path}")

    if www.exists() and not www.is_dir():
        raise SDS200Error(f"Home Assistant www path is not a directory: {www}")
    if parent.exists() and not parent.is_dir():
        raise SDS200Error(f"Home Assistant SDS200 card path is not a directory: {parent}")
    if target.exists() and not target.is_file():
        raise SDS200Error(f"Home Assistant SDS200 card target is not a file: {target}")

    parent.mkdir(parents=True, exist_ok=True)

    payload = _card_bytes()

    if target.exists() and target.read_bytes() == payload:
        target.chmod(_HOME_ASSISTANT_LOVELACE_CARD_MODE)
        return target

    temporary: Path | None = None

    try:
        with NamedTemporaryFile(
            mode="wb",
            dir=parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

        assert temporary is not None
        temporary.chmod(_HOME_ASSISTANT_LOVELACE_CARD_MODE)
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

    target.chmod(_HOME_ASSISTANT_LOVELACE_CARD_MODE)

    if target.read_bytes() != payload:
        raise SDS200Error("Home Assistant Lovelace card installation verification failed.")

    return target


__all__ = [
    "HOME_ASSISTANT_LOVELACE_CARD_DIRECTORY",
    "HOME_ASSISTANT_LOVELACE_CARD_FILENAME",
    "HOME_ASSISTANT_LOVELACE_CARD_PATH",
    "HOME_ASSISTANT_LOVELACE_CARD_RESOURCE_URL",
    "install_home_assistant_lovelace_card",
]
