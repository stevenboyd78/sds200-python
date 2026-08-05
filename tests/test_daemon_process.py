from __future__ import annotations

import logging
import signal
from collections.abc import Iterable

import pytest

from sds200.daemon_process import (
    DaemonProcess,
    DaemonSignalController,
)


class FakeRuntime:
    def __init__(
        self,
        order: list[str],
        *,
        start_error: BaseException | None = None,
        stop_error: BaseException | None = None,
    ) -> None:
        self.order = order
        self.start_error = start_error
        self.stop_error = stop_error
        self.start_calls = 0
        self.stop_calls = 0

    def start(self) -> None:
        self.order.append("runtime.start")
        self.start_calls += 1
        if self.start_error is not None:
            raise self.start_error

    def stop(self) -> None:
        self.order.append("runtime.stop")
        self.stop_calls += 1
        if self.stop_error is not None:
            raise self.stop_error


class FakeApiServer:
    def __init__(
        self,
        order: list[str],
        *,
        start_error: BaseException | None = None,
        stop_error: BaseException | None = None,
    ) -> None:
        self.order = order
        self.start_error = start_error
        self.stop_error = stop_error
        self.start_calls = 0
        self.stop_calls = 0

    def start(self) -> None:
        self.order.append("api.start")
        self.start_calls += 1
        if self.start_error is not None:
            raise self.start_error

    def stop(self) -> None:
        self.order.append("api.stop")
        self.stop_calls += 1
        if self.stop_error is not None:
            raise self.stop_error


class FakeEventServer:
    def __init__(
        self,
        order: list[str],
        *,
        start_error: BaseException | None = None,
        stop_error: BaseException | None = None,
    ) -> None:
        self.order = order
        self.start_error = start_error
        self.stop_error = stop_error
        self.start_calls = 0
        self.stop_calls = 0

    def start(self) -> None:
        self.order.append("events.start")
        self.start_calls += 1
        if self.start_error is not None:
            raise self.start_error

    def stop(self) -> None:
        self.order.append("events.stop")
        self.stop_calls += 1
        if self.stop_error is not None:
            raise self.stop_error


class FakeSignalController:
    def __init__(
        self,
        order: list[str],
        waits: Iterable[bool | BaseException],
        *,
        last_signal: int | None = None,
    ) -> None:
        self.order = order
        self.waits = iter(waits)
        self._last_signal = last_signal

    @property
    def last_signal(self) -> int | None:
        return self._last_signal

    def wait(self, timeout: float | None = None) -> bool:
        assert timeout == 0.25
        self.order.append("signals.wait")
        result = next(self.waits)
        if isinstance(result, BaseException):
            raise result
        return result

    def __enter__(self) -> FakeSignalController:
        self.order.append("signals.enter")
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object,
    ) -> None:
        del exception_type, exception, traceback
        self.order.append("signals.exit")


def test_signal_controller_installs_only_stop_signals_and_restores_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed: dict[int, object] = {}
    restored: list[tuple[int, object]] = []
    original = object()

    monkeypatch.setattr(signal, "getsignal", lambda signum: original)

    def install(signum: int, handler: object) -> object:
        if signum in installed:
            restored.append((signum, handler))
        else:
            installed[signum] = handler
        return original

    monkeypatch.setattr(signal, "signal", install)

    controller = DaemonSignalController()
    with controller:
        expected = {int(signal.SIGINT), int(signal.SIGTERM)}
        assert set(installed) == expected

        sighup = getattr(signal, "SIGHUP", None)
        if isinstance(sighup, int):
            assert int(sighup) not in installed

        term = int(signal.SIGTERM)
        handler = installed[term]
        assert callable(handler)
        handler(term, None)
        assert controller.wait(timeout=0)
        assert controller.last_signal == term

    assert len(restored) == len(installed)
    assert all(handler is original for _, handler in restored)


def test_signal_controller_request_stop_sets_wait_event() -> None:
    controller = DaemonSignalController()

    controller.request_stop()

    assert controller.wait(timeout=0)
    assert controller.last_signal is None


def test_signal_controller_attempts_all_restorations_and_propagates_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = object()
    installed: list[int] = []
    restoration_attempts: list[int] = []

    monkeypatch.setattr(signal, "getsignal", lambda signum: original)

    def install(signum: int, handler: object) -> object:
        if handler is original:
            restoration_attempts.append(signum)
            if len(restoration_attempts) == 1:
                raise OSError("secret restoration failure")
        else:
            installed.append(signum)
        return original

    monkeypatch.setattr(signal, "signal", install)

    with (
        pytest.raises(OSError, match="secret restoration"),
        DaemonSignalController(),
    ):
        pass

    assert set(restoration_attempts) == set(installed)


def test_signal_controller_preserves_body_error_when_restoration_fails(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    original = object()
    body_error = RuntimeError("secret process failure")

    monkeypatch.setattr(signal, "getsignal", lambda signum: original)

    def install(signum: int, handler: object) -> object:
        del signum
        if handler is original:
            raise OSError("secret restoration failure")
        return original

    monkeypatch.setattr(signal, "signal", install)

    with (
        caplog.at_level(logging.ERROR, logger="sds200.daemon_process"),
        pytest.raises(RuntimeError) as raised,
        DaemonSignalController(),
    ):
        raise body_error

    assert raised.value is body_error
    assert "process_error=RuntimeError" in caplog.text
    assert "restoration_error=OSError" in caplog.text
    assert "secret" not in caplog.text


def test_signal_controller_rolls_back_partial_installation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = object()
    calls: list[tuple[int, object]] = []
    attempts = 0

    monkeypatch.setattr(signal, "getsignal", lambda signum: original)

    def install(signum: int, handler: object) -> object:
        nonlocal attempts
        attempts += 1
        calls.append((signum, handler))
        if attempts == 2:
            raise OSError("secret install failure")
        return original

    monkeypatch.setattr(signal, "signal", install)

    with (
        pytest.raises(OSError, match="secret install"),
        DaemonSignalController(),
    ):
        raise AssertionError("unreachable")

    assert len(calls) == 3
    assert calls[-1][1] is original


def test_process_runs_until_requested_and_stops_before_restoring_signals() -> None:
    order: list[str] = []
    runtime = FakeRuntime(order)
    signals = FakeSignalController(
        order,
        (False, True),
        last_signal=int(signal.SIGTERM),
    )

    result = DaemonProcess(
        runtime,
        signals=signals,
        poll_interval=0.25,
    ).run()

    assert result.last_signal == int(signal.SIGTERM)
    assert order == [
        "signals.enter",
        "runtime.start",
        "signals.wait",
        "signals.wait",
        "runtime.stop",
        "signals.exit",
    ]
    assert runtime.start_calls == 1
    assert runtime.stop_calls == 1


def test_process_stops_after_startup_failure() -> None:
    order: list[str] = []
    startup_error = RuntimeError("secret startup failure")
    runtime = FakeRuntime(order, start_error=startup_error)
    signals = FakeSignalController(order, ())

    with pytest.raises(RuntimeError) as raised:
        DaemonProcess(
            runtime,
            signals=signals,
            poll_interval=0.25,
        ).run()

    assert raised.value is startup_error
    assert order == [
        "signals.enter",
        "runtime.start",
        "runtime.stop",
        "signals.exit",
    ]


def test_process_stops_after_wait_failure() -> None:
    order: list[str] = []
    wait_error = RuntimeError("secret wait failure")
    runtime = FakeRuntime(order)
    signals = FakeSignalController(order, (wait_error,))

    with pytest.raises(RuntimeError) as raised:
        DaemonProcess(
            runtime,
            signals=signals,
            poll_interval=0.25,
        ).run()

    assert raised.value is wait_error
    assert order == [
        "signals.enter",
        "runtime.start",
        "signals.wait",
        "runtime.stop",
        "signals.exit",
    ]


def test_process_preserves_primary_error_when_cleanup_also_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    order: list[str] = []
    wait_error = RuntimeError("secret wait failure")
    cleanup_error = RuntimeError("secret cleanup failure")
    runtime = FakeRuntime(order, stop_error=cleanup_error)
    signals = FakeSignalController(order, (wait_error,))

    with (
        caplog.at_level(logging.ERROR, logger="sds200.daemon_process"),
        pytest.raises(RuntimeError) as raised,
    ):
        DaemonProcess(
            runtime,
            signals=signals,
            poll_interval=0.25,
        ).run()

    assert raised.value is wait_error
    assert "process_error=RuntimeError" in caplog.text
    assert "cleanup_error=RuntimeError" in caplog.text
    assert "secret" not in caplog.text


def test_process_propagates_clean_shutdown_failure() -> None:
    order: list[str] = []
    cleanup_error = RuntimeError("shutdown failed")
    runtime = FakeRuntime(order, stop_error=cleanup_error)
    signals = FakeSignalController(order, (True,))

    with pytest.raises(RuntimeError) as raised:
        DaemonProcess(
            runtime,
            signals=signals,
            poll_interval=0.25,
        ).run()

    assert raised.value is cleanup_error
    assert order == [
        "signals.enter",
        "runtime.start",
        "signals.wait",
        "runtime.stop",
        "signals.exit",
    ]


@pytest.mark.parametrize("poll_interval", [0.0, -0.1])
def test_process_requires_positive_poll_interval(poll_interval: float) -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        DaemonProcess(FakeRuntime([]), poll_interval=poll_interval)


def test_process_starts_api_after_runtime_and_stops_it_first() -> None:
    order: list[str] = []
    runtime = FakeRuntime(order)
    api_server = FakeApiServer(order)
    signals = FakeSignalController(
        order,
        (True,),
        last_signal=int(signal.SIGTERM),
    )

    result = DaemonProcess(
        runtime,
        api_server=api_server,
        signals=signals,
        poll_interval=0.25,
    ).run()

    assert result.last_signal == int(signal.SIGTERM)
    assert order == [
        "signals.enter",
        "runtime.start",
        "api.start",
        "signals.wait",
        "api.stop",
        "runtime.stop",
        "signals.exit",
    ]
    assert api_server.start_calls == 1
    assert api_server.stop_calls == 1


def test_api_startup_failure_stops_api_then_runtime() -> None:
    order: list[str] = []
    startup_error = RuntimeError("secret API startup failure")
    runtime = FakeRuntime(order)
    api_server = FakeApiServer(order, start_error=startup_error)
    signals = FakeSignalController(order, ())

    with pytest.raises(RuntimeError) as raised:
        DaemonProcess(
            runtime,
            api_server=api_server,
            signals=signals,
            poll_interval=0.25,
        ).run()

    assert raised.value is startup_error
    assert order == [
        "signals.enter",
        "runtime.start",
        "api.start",
        "api.stop",
        "runtime.stop",
        "signals.exit",
    ]


def test_runtime_startup_failure_does_not_touch_api_server() -> None:
    order: list[str] = []
    startup_error = RuntimeError("secret runtime startup failure")
    runtime = FakeRuntime(order, start_error=startup_error)
    api_server = FakeApiServer(order)
    signals = FakeSignalController(order, ())

    with pytest.raises(RuntimeError) as raised:
        DaemonProcess(
            runtime,
            api_server=api_server,
            signals=signals,
            poll_interval=0.25,
        ).run()

    assert raised.value is startup_error
    assert order == [
        "signals.enter",
        "runtime.start",
        "runtime.stop",
        "signals.exit",
    ]
    assert api_server.start_calls == 0
    assert api_server.stop_calls == 0


def test_process_error_preserves_primary_when_all_cleanup_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    order: list[str] = []
    wait_error = RuntimeError("secret wait failure")
    api_error = RuntimeError("secret API cleanup failure")
    runtime_error = RuntimeError("secret runtime cleanup failure")
    runtime = FakeRuntime(order, stop_error=runtime_error)
    api_server = FakeApiServer(order, stop_error=api_error)
    signals = FakeSignalController(order, (wait_error,))

    with (
        caplog.at_level(logging.ERROR, logger="sds200.daemon_process"),
        pytest.raises(RuntimeError) as raised,
    ):
        DaemonProcess(
            runtime,
            api_server=api_server,
            signals=signals,
            poll_interval=0.25,
        ).run()

    assert raised.value is wait_error
    assert order == [
        "signals.enter",
        "runtime.start",
        "api.start",
        "signals.wait",
        "api.stop",
        "runtime.stop",
        "signals.exit",
    ]
    assert "process_error=RuntimeError" in caplog.text
    assert "cleanup_error=RuntimeError" in caplog.text
    assert "secret" not in caplog.text


def test_clean_api_shutdown_failure_still_stops_runtime() -> None:
    order: list[str] = []
    api_error = RuntimeError("API shutdown failed")
    runtime = FakeRuntime(order)
    api_server = FakeApiServer(order, stop_error=api_error)
    signals = FakeSignalController(order, (True,))

    with pytest.raises(RuntimeError) as raised:
        DaemonProcess(
            runtime,
            api_server=api_server,
            signals=signals,
            poll_interval=0.25,
        ).run()

    assert raised.value is api_error
    assert order == [
        "signals.enter",
        "runtime.start",
        "api.start",
        "signals.wait",
        "api.stop",
        "runtime.stop",
        "signals.exit",
    ]


def test_clean_shutdown_preserves_first_failure_and_logs_second(
    caplog: pytest.LogCaptureFixture,
) -> None:
    order: list[str] = []
    api_error = RuntimeError("secret API shutdown failure")
    runtime_error = RuntimeError("secret runtime shutdown failure")
    runtime = FakeRuntime(order, stop_error=runtime_error)
    api_server = FakeApiServer(order, stop_error=api_error)
    signals = FakeSignalController(order, (True,))

    with (
        caplog.at_level(logging.ERROR, logger="sds200.daemon_process"),
        pytest.raises(RuntimeError) as raised,
    ):
        DaemonProcess(
            runtime,
            api_server=api_server,
            signals=signals,
            poll_interval=0.25,
        ).run()

    assert raised.value is api_error
    assert "primary_error=RuntimeError" in caplog.text
    assert "cleanup_error=RuntimeError" in caplog.text
    assert "secret" not in caplog.text

def test_process_brackets_runtime_with_event_server() -> None:
    order: list[str] = []
    runtime = FakeRuntime(order)
    api_server = FakeApiServer(order)
    event_server = FakeEventServer(order)
    signals = FakeSignalController(
        order,
        (True,),
        last_signal=int(signal.SIGTERM),
    )

    result = DaemonProcess(
        runtime,
        api_server=api_server,
        event_server=event_server,
        signals=signals,
        poll_interval=0.25,
    ).run()

    assert result.last_signal == int(signal.SIGTERM)
    assert order == [
        "signals.enter",
        "events.start",
        "runtime.start",
        "api.start",
        "signals.wait",
        "api.stop",
        "runtime.stop",
        "events.stop",
        "signals.exit",
    ]
    assert event_server.start_calls == 1
    assert event_server.stop_calls == 1


def test_event_server_startup_failure_stops_only_attempted_event_server() -> None:
    order: list[str] = []
    startup_error = RuntimeError("secret event startup failure")
    runtime = FakeRuntime(order)
    api_server = FakeApiServer(order)
    event_server = FakeEventServer(
        order,
        start_error=startup_error,
    )
    signals = FakeSignalController(order, ())

    with pytest.raises(RuntimeError) as raised:
        DaemonProcess(
            runtime,
            api_server=api_server,
            event_server=event_server,
            signals=signals,
            poll_interval=0.25,
        ).run()

    assert raised.value is startup_error
    assert order == [
        "signals.enter",
        "events.start",
        "events.stop",
        "signals.exit",
    ]
    assert runtime.start_calls == 0
    assert runtime.stop_calls == 0
    assert api_server.start_calls == 0
    assert api_server.stop_calls == 0


def test_runtime_startup_failure_stops_runtime_then_event_server() -> None:
    order: list[str] = []
    startup_error = RuntimeError("secret runtime startup failure")
    runtime = FakeRuntime(order, start_error=startup_error)
    api_server = FakeApiServer(order)
    event_server = FakeEventServer(order)
    signals = FakeSignalController(order, ())

    with pytest.raises(RuntimeError) as raised:
        DaemonProcess(
            runtime,
            api_server=api_server,
            event_server=event_server,
            signals=signals,
            poll_interval=0.25,
        ).run()

    assert raised.value is startup_error
    assert order == [
        "signals.enter",
        "events.start",
        "runtime.start",
        "runtime.stop",
        "events.stop",
        "signals.exit",
    ]
    assert api_server.start_calls == 0
    assert api_server.stop_calls == 0


def test_api_startup_failure_keeps_event_stream_through_runtime_stop() -> None:
    order: list[str] = []
    startup_error = RuntimeError("secret API startup failure")
    runtime = FakeRuntime(order)
    api_server = FakeApiServer(
        order,
        start_error=startup_error,
    )
    event_server = FakeEventServer(order)
    signals = FakeSignalController(order, ())

    with pytest.raises(RuntimeError) as raised:
        DaemonProcess(
            runtime,
            api_server=api_server,
            event_server=event_server,
            signals=signals,
            poll_interval=0.25,
        ).run()

    assert raised.value is startup_error
    assert order == [
        "signals.enter",
        "events.start",
        "runtime.start",
        "api.start",
        "api.stop",
        "runtime.stop",
        "events.stop",
        "signals.exit",
    ]

def test_process_error_preserves_primary_when_event_cleanup_fails(
    caplog: pytest.LogCaptureFixture,
) -> None:
    order: list[str] = []
    wait_error = RuntimeError("secret process failure")
    event_error = OSError("secret event cleanup failure")
    runtime = FakeRuntime(order)
    event_server = FakeEventServer(
        order,
        stop_error=event_error,
    )
    signals = FakeSignalController(order, (wait_error,))

    with (
        caplog.at_level(logging.ERROR, logger="sds200.daemon_process"),
        pytest.raises(RuntimeError) as raised,
    ):
        DaemonProcess(
            runtime,
            event_server=event_server,
            signals=signals,
            poll_interval=0.25,
        ).run()

    assert raised.value is wait_error
    assert order == [
        "signals.enter",
        "events.start",
        "runtime.start",
        "signals.wait",
        "runtime.stop",
        "events.stop",
        "signals.exit",
    ]
    assert "process_error=RuntimeError" in caplog.text
    assert "cleanup_error=OSError" in caplog.text
    assert "secret" not in caplog.text


def test_clean_shutdown_attempts_event_stop_after_other_failures(
    caplog: pytest.LogCaptureFixture,
) -> None:
    order: list[str] = []
    api_error = RuntimeError("secret API shutdown failure")
    runtime_error = OSError("secret runtime shutdown failure")
    event_error = ValueError("secret event shutdown failure")
    runtime = FakeRuntime(order, stop_error=runtime_error)
    api_server = FakeApiServer(order, stop_error=api_error)
    event_server = FakeEventServer(
        order,
        stop_error=event_error,
    )
    signals = FakeSignalController(order, (True,))

    with (
        caplog.at_level(logging.ERROR, logger="sds200.daemon_process"),
        pytest.raises(RuntimeError) as raised,
    ):
        DaemonProcess(
            runtime,
            api_server=api_server,
            event_server=event_server,
            signals=signals,
            poll_interval=0.25,
        ).run()

    assert raised.value is api_error
    assert order == [
        "signals.enter",
        "events.start",
        "runtime.start",
        "api.start",
        "signals.wait",
        "api.stop",
        "runtime.stop",
        "events.stop",
        "signals.exit",
    ]
    assert "primary_error=RuntimeError" in caplog.text
    assert "cleanup_error=OSError" in caplog.text
    assert "secret" not in caplog.text
