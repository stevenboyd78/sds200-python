from __future__ import annotations

import os
import socket
import stat
from pathlib import Path

import pytest

from sds200 import (
    DAEMON_SOCKET_DIRECTORY_MODE,
    DAEMON_SOCKET_MODE,
    DaemonIpcError,
    DaemonSocketListener,
    DaemonSocketLocation,
    DaemonSocketSource,
)


def location(
    path: Path,
    source: DaemonSocketSource = DaemonSocketSource.EXPLICIT,
) -> DaemonSocketLocation:
    return DaemonSocketLocation(path, source)


def test_listener_requires_valid_limits(tmp_path: Path) -> None:
    target = location(tmp_path / "daemon.sock")

    with pytest.raises(TypeError, match="backlog"):
        DaemonSocketListener(target, backlog=True)
    with pytest.raises(ValueError, match="backlog"):
        DaemonSocketListener(target, backlog=0)
    with pytest.raises(TypeError, match="probe timeout"):
        DaemonSocketListener(target, probe_timeout=True)
    with pytest.raises(ValueError, match="probe timeout"):
        DaemonSocketListener(target, probe_timeout=0)


def test_explicit_parent_must_already_exist(tmp_path: Path) -> None:
    target = location(tmp_path / "missing" / "daemon.sock")
    listener = DaemonSocketListener(target)

    with pytest.raises(DaemonIpcError, match="does not exist"):
        listener.start()

    assert target.path.exists() is False


def test_explicit_parent_is_not_repermissioned(tmp_path: Path) -> None:
    parent = tmp_path / "explicit"
    parent.mkdir(mode=0o755)
    parent.chmod(0o755)
    target = location(parent / "daemon.sock")

    listener = DaemonSocketListener(target)
    listener.start()
    try:
        assert stat.S_IMODE(parent.stat().st_mode) == 0o755
        assert stat.S_IMODE(target.path.stat().st_mode) == DAEMON_SOCKET_MODE
    finally:
        listener.stop()


def test_managed_parent_is_created_and_private(tmp_path: Path) -> None:
    parent = tmp_path / "runtime" / "sdsctl"
    target = location(
        parent / "daemon.sock",
        DaemonSocketSource.XDG_RUNTIME,
    )

    listener = DaemonSocketListener(target)
    listener.start()
    try:
        assert stat.S_IMODE(parent.stat().st_mode) == (
            DAEMON_SOCKET_DIRECTORY_MODE
        )
        assert stat.S_IMODE(target.path.stat().st_mode) == DAEMON_SOCKET_MODE
    finally:
        listener.stop()

    assert target.path.exists() is False


def test_managed_parent_existing_mode_is_made_private(tmp_path: Path) -> None:
    parent = tmp_path / "state" / "sdsctl"
    parent.mkdir(parents=True, mode=0o755)
    parent.chmod(0o755)
    target = location(
        parent / "daemon.sock",
        DaemonSocketSource.USER_STATE,
    )

    listener = DaemonSocketListener(target)
    listener.start()
    try:
        assert stat.S_IMODE(parent.stat().st_mode) == (
            DAEMON_SOCKET_DIRECTORY_MODE
        )
    finally:
        listener.stop()


def test_parent_symlink_is_refused(tmp_path: Path) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(actual, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")

    target = location(linked / "daemon.sock")
    listener = DaemonSocketListener(target)

    with pytest.raises(DaemonIpcError, match="must not be a symlink"):
        listener.start()


def test_non_directory_parent_is_refused(tmp_path: Path) -> None:
    parent = tmp_path / "not-a-directory"
    parent.write_text("occupied", encoding="utf-8")
    target = location(parent / "daemon.sock")

    with pytest.raises(DaemonIpcError, match="not a directory"):
        DaemonSocketListener(target).start()


def test_non_socket_target_is_never_removed(tmp_path: Path) -> None:
    target = location(tmp_path / "daemon.sock")
    target.path.write_text("important", encoding="utf-8")

    with pytest.raises(DaemonIpcError, match="non-socket"):
        DaemonSocketListener(target).start()

    assert target.path.read_text(encoding="utf-8") == "important"


def test_socket_symlink_is_never_removed(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.write_text("important", encoding="utf-8")
    target = location(tmp_path / "daemon.sock")
    try:
        target.path.symlink_to(real)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")

    with pytest.raises(DaemonIpcError, match="must not be a symlink"):
        DaemonSocketListener(target).start()

    assert target.path.is_symlink()
    assert real.read_text(encoding="utf-8") == "important"


def test_active_socket_is_rejected_without_removal(tmp_path: Path) -> None:
    target = location(tmp_path / "daemon.sock")
    active = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    active.bind(os.fspath(target.path))
    active.listen(1)

    try:
        with pytest.raises(DaemonIpcError, match="already active"):
            DaemonSocketListener(target).start()
        assert target.path.exists()
    finally:
        active.close()
        target.path.unlink(missing_ok=True)


def test_stale_socket_is_replaced(tmp_path: Path) -> None:
    target = location(tmp_path / "daemon.sock")
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(os.fspath(target.path))
    stale.close()

    listener = DaemonSocketListener(target)
    listener.start()
    try:
        assert listener.active is True
        assert stat.S_ISSOCK(target.path.stat().st_mode)
        assert listener.socket.getsockname() == os.fspath(target.path)

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(1.0)
            client.connect(os.fspath(target.path))
    finally:
        listener.stop()


def test_start_is_idempotent_while_active(tmp_path: Path) -> None:
    target = location(tmp_path / "daemon.sock")
    listener = DaemonSocketListener(target)

    first = listener.start()
    second = listener.start()
    try:
        assert first is second
        assert listener.socket is first
    finally:
        listener.stop()


def test_stop_is_idempotent_and_listener_cannot_restart(tmp_path: Path) -> None:
    target = location(tmp_path / "daemon.sock")
    listener = DaemonSocketListener(target)

    listener.start()
    listener.stop()
    listener.stop()

    assert listener.active is False
    assert target.path.exists() is False
    with pytest.raises(RuntimeError, match="cannot be restarted"):
        listener.start()
    with pytest.raises(RuntimeError, match="not active"):
        _ = listener.socket


def test_context_manager_removes_owned_socket(tmp_path: Path) -> None:
    target = location(tmp_path / "daemon.sock")

    with DaemonSocketListener(target) as listener:
        assert listener.active is True
        assert target.path.exists()

    assert target.path.exists() is False


def test_shutdown_does_not_remove_replacement_entry(tmp_path: Path) -> None:
    target = location(tmp_path / "daemon.sock")
    listener = DaemonSocketListener(target)
    listener.start()

    target.path.unlink()
    target.path.write_text("replacement", encoding="utf-8")

    listener.stop()

    assert target.path.read_text(encoding="utf-8") == "replacement"


def test_startup_failure_removes_only_owned_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = location(tmp_path / "daemon.sock")
    original_chmod = os.chmod

    def fail_socket_chmod(path: os.PathLike[str] | str, mode: int) -> None:
        if Path(path) == target.path:
            raise PermissionError("simulated chmod failure")
        original_chmod(path, mode)

    monkeypatch.setattr(os, "chmod", fail_socket_chmod)

    with pytest.raises(PermissionError, match="simulated"):
        DaemonSocketListener(target).start()

    assert target.path.exists() is False
