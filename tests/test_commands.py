import pytest

from sds200.commands import (
    GetFavoritesQuickKeys,
    GetGltFavorites,
    GetScannerRecordingStatus,
    HoldSelection,
    NextSelection,
    PauseResumeAnalysis,
    PressKey,
    PreviousSelection,
    SetFavoritesQuickKeys,
    SetScannerRecordingStatus,
    SetSquelch,
    SetVolume,
    StartCurrentActivityAnalysis,
    StartLcnMonitorAnalysis,
    StartScannerInfoPush,
)
from sds200.exceptions import (
    CommandRejectedError,
    ProtocolError,
    ScannerRecordingControlError,
)
from sds200.models import (
    AnalysisMode,
    AnalysisResponse,
    FavoritesQuickKeys,
    FavoritesQuickKeyState,
    GltResponse,
    Packet,
    ScannerRecordingStatus,
    ScannerRecordingStatusResponse,
)


def test_analysis_modes_are_the_six_exact_apr_tokens() -> None:
    assert [(mode.name, mode.value) for mode in AnalysisMode] == [
        ("SYSTEM_STATUS", "SYSTEM_STATUS"),
        ("RF_POWER_PLOT", "RF_POWER_PLOT"),
        ("CURRENT_ACTIVITY", "CURRENT_ACTIVITY"),
        ("LCN_MONITOR", "LCN_MONITOR"),
        ("ACTIVITY_LOG", "ACTIVITY_LOG"),
        ("RAW_DATA_OUTPUT", "RAW_DATA_OUTPUT"),
    ]
    assert len(AnalysisMode.__members__) == 6


@pytest.mark.parametrize(
    ("command_type", "mode"),
    [
        (StartCurrentActivityAnalysis, "CURRENT_ACTIVITY"),
        (StartLcnMonitorAnalysis, "LCN_MONITOR"),
    ],
)
def test_analysis_start_commands_exact_contract(command_type: type, mode: str) -> None:
    command = command_type(123456789)
    response = AnalysisResponse.create(
        command="AST", root_attributes={}, records=(), raw_xml="<AST />"
    )
    assert command.wire == f"AST,{mode},123456789"
    assert command.response_command == "AST"
    assert command.parse_response(response) is response
    with pytest.raises(TypeError, match="AST did not return AnalysisResponse"):
        command.parse_response(Packet(command="AST", fields=(), raw="AST"))


@pytest.mark.parametrize("site_index", [True, "1", -1])
@pytest.mark.parametrize(
    "command_type", [StartCurrentActivityAnalysis, StartLcnMonitorAnalysis]
)
def test_analysis_start_rejects_invalid_site_index(
    command_type: type, site_index: object
) -> None:
    with pytest.raises(ValueError, match="non-negative integer"):
        command_type(site_index)


@pytest.mark.parametrize("mode", list(AnalysisMode))
def test_pause_resume_analysis_exact_wire_and_ack(mode: AnalysisMode) -> None:
    command = PauseResumeAnalysis(mode)
    assert command.wire == f"APR,{mode.value}"
    assert command.response_command == "APR"
    assert command.parse_response(
        Packet(command="APR", fields=("OK",), raw="APR,OK")
    ) is None


def test_pause_resume_analysis_requires_enum() -> None:
    with pytest.raises(ValueError, match="AnalysisMode"):
        PauseResumeAnalysis("CURRENT_ACTIVITY")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "response",
    [
        object(),
        Packet(command="OTHER", fields=("OK",), raw="OTHER,OK"),
        Packet(command="APR", fields=(), raw="APR"),
        Packet(command="APR", fields=("OK", "EXTRA"), raw="APR,OK,EXTRA"),
        Packet(command="APR", fields=(" OK",), raw="APR, OK"),
    ],
)
def test_pause_resume_analysis_rejects_malformed_ack(response: object) -> None:
    with pytest.raises(ProtocolError, match="APR"):
        PauseResumeAnalysis(AnalysisMode.CURRENT_ACTIVITY).parse_response(response)


@pytest.mark.parametrize("status", ["NG", "ERR", "ERROR"])
def test_pause_resume_analysis_rejects_negative_ack(status: str) -> None:
    with pytest.raises(CommandRejectedError, match="rejected APR"):
        PauseResumeAnalysis(AnalysisMode.CURRENT_ACTIVITY).parse_response(
            Packet(command="APR", fields=(status,), raw=f"APR,{status}")
        )


def test_set_volume_wire() -> None:
    assert SetVolume(12).wire == "VOL,12"


def test_set_squelch_wire() -> None:
    assert SetSquelch(5).wire == "SQL,5"


@pytest.mark.parametrize("value", [-1, 30])
def test_volume_validation(value: int) -> None:
    with pytest.raises(ValueError):
        SetVolume(value)


def test_psi_command_wire_and_validation() -> None:
    assert StartScannerInfoPush(250).wire == "PSI,250"
    with pytest.raises(ValueError):
        StartScannerInfoPush(0)


def test_get_glt_favorites_contract() -> None:
    command = GetGltFavorites()
    response = GltResponse.create(
        command="GLT", root_attributes={}, records=(), raw_xml="<GLT />"
    )

    assert command.wire == "GLT,FL"
    assert command.response_command == "GLT"
    assert command.parse_response(response) is response
    with pytest.raises(TypeError, match="GLT did not return GltResponse"):
        command.parse_response(Packet(command="GLT", fields=(), raw="GLT"))


def test_get_favorites_quick_keys_exact_contract_and_values() -> None:
    fields = tuple(str(index % 3) for index in range(100))
    packet = Packet(command="FQK", fields=fields, raw="FQK," + ",".join(fields))
    command = GetFavoritesQuickKeys()

    response = command.parse_response(packet)

    assert command.wire == "FQK"
    assert command.response_command == "FQK"
    assert isinstance(response, FavoritesQuickKeys)
    assert response.packet is packet
    assert response.states[:3] == (
        FavoritesQuickKeyState.NONEXISTENT,
        FavoritesQuickKeyState.DISABLED,
        FavoritesQuickKeyState.ENABLED,
    )
    assert len(response.states) == 100


@pytest.mark.parametrize(
    "fields",
    [
        ("0",) * 99,
        ("0",) * 101,
        ("0",) * 99 + ("",),
        ("0",) * 99 + (" 0",),
        ("0",) * 99 + ("0 ",),
        ("0",) * 99 + ("3",),
    ],
)
def test_get_favorites_quick_keys_rejects_malformed_fields(
    fields: tuple[str, ...],
) -> None:
    with pytest.raises(ProtocolError, match="FQK read"):
        GetFavoritesQuickKeys().parse_response(
            Packet(command="FQK", fields=fields, raw="FQK," + ",".join(fields))
        )


@pytest.mark.parametrize(
    "response",
    [object(), Packet(command="OTHER", fields=("0",) * 100, raw="OTHER")],
)
def test_get_favorites_quick_keys_rejects_wrong_response(response: object) -> None:
    with pytest.raises(ProtocolError, match="unexpected response"):
        GetFavoritesQuickKeys().parse_response(response)


def test_set_favorites_quick_keys_exact_contract() -> None:
    states = [0, 1, FavoritesQuickKeyState.ENABLED] * 33 + [0]
    command = SetFavoritesQuickKeys(states)

    assert command.states[:3] == (
        FavoritesQuickKeyState.NONEXISTENT,
        FavoritesQuickKeyState.DISABLED,
        FavoritesQuickKeyState.ENABLED,
    )
    assert isinstance(command.states, tuple)
    assert command.wire == "FQK," + ",".join(str(index % 3) for index in range(100))
    assert command.response_command == "FQK"
    assert command.parse_response(
        Packet(command="FQK", fields=("OK",), raw="FQK,OK")
    ) is None


@pytest.mark.parametrize("count", [99, 101])
def test_set_favorites_quick_keys_rejects_wrong_count(count: int) -> None:
    with pytest.raises(ValueError, match="exactly 100"):
        SetFavoritesQuickKeys([0] * count)


@pytest.mark.parametrize("value", [True, -1, 3, "1", None, object()])
def test_set_favorites_quick_keys_rejects_invalid_state(value: object) -> None:
    states: list[object] = [0] * 100
    states[42] = value
    with pytest.raises(ValueError, match="integers 0, 1, or 2"):
        SetFavoritesQuickKeys(states)  # type: ignore[arg-type]


@pytest.mark.parametrize("status", ["NG", "ERR", "ERROR"])
def test_set_favorites_quick_keys_rejects_negative_ack(status: str) -> None:
    with pytest.raises(CommandRejectedError, match="rejected FQK"):
        SetFavoritesQuickKeys([0] * 100).parse_response(
            Packet(command="FQK", fields=(status,), raw=f"FQK,{status}")
        )


@pytest.mark.parametrize(
    "response",
    [
        object(),
        Packet(command="OTHER", fields=("OK",), raw="OTHER,OK"),
        Packet(command="FQK", fields=(), raw="FQK"),
        Packet(command="FQK", fields=("OK", "EXTRA"), raw="FQK,OK,EXTRA"),
        Packet(command="FQK", fields=(" OK",), raw="FQK, OK"),
    ],
)
def test_set_favorites_quick_keys_rejects_malformed_ack(response: object) -> None:
    with pytest.raises(ProtocolError, match="FQK"):
        SetFavoritesQuickKeys([0] * 100).parse_response(response)


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("0", ScannerRecordingStatus.STOPPED),
        ("1", ScannerRecordingStatus.RECORDING),
    ],
)
def test_get_scanner_recording_status_exact_contract(
    field: str, expected: ScannerRecordingStatus
) -> None:
    packet = Packet(command="URC", fields=(field,), raw=f"URC,{field}")
    command = GetScannerRecordingStatus()

    response = command.parse_response(packet)

    assert command.wire == "URC"
    assert command.response_command == "URC"
    assert isinstance(response, ScannerRecordingStatusResponse)
    assert response.status is expected
    assert response.packet is packet


@pytest.mark.parametrize(
    "response",
    [
        object(),
        Packet(command="OTHER", fields=("0",), raw="OTHER,0"),
        Packet(command="URC", fields=(), raw="URC"),
        Packet(command="URC", fields=("",), raw="URC,"),
        Packet(command="URC", fields=(" 0",), raw="URC, 0"),
        Packet(command="URC", fields=("0 ",), raw="URC,0 "),
        Packet(command="URC", fields=("2",), raw="URC,2"),
        Packet(command="URC", fields=("0", "EXTRA"), raw="URC,0,EXTRA"),
    ],
)
def test_get_scanner_recording_status_rejects_malformed_response(
    response: object,
) -> None:
    with pytest.raises(ProtocolError, match="URC read"):
        GetScannerRecordingStatus().parse_response(response)


@pytest.mark.parametrize(
    ("value", "expected", "wire"),
    [
        (0, ScannerRecordingStatus.STOPPED, "URC,0"),
        (ScannerRecordingStatus.RECORDING, ScannerRecordingStatus.RECORDING, "URC,1"),
    ],
)
def test_set_scanner_recording_status_exact_contract(
    value: int | ScannerRecordingStatus,
    expected: ScannerRecordingStatus,
    wire: str,
) -> None:
    command = SetScannerRecordingStatus(value)

    assert command.status is expected
    assert command.wire == wire
    assert command.response_command == "URC"
    assert command.parse_response(
        Packet(command="URC", fields=("OK",), raw="URC,OK")
    ) is None


@pytest.mark.parametrize("value", [True, False, -1, 2, "1", None, object()])
def test_set_scanner_recording_status_rejects_invalid_value(value: object) -> None:
    with pytest.raises(ValueError, match="integer 0 or 1"):
        SetScannerRecordingStatus(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("0001", "FILE ACCESS"),
        ("0002", "LOW BATTERY"),
        ("0003", "SESSION OVER LIMIT"),
        ("0004", "RTC LOST"),
        ("9999", None),
    ],
)
@pytest.mark.parametrize("command", [GetScannerRecordingStatus(), SetScannerRecordingStatus(1)])
def test_scanner_recording_control_preserves_operation_error(
    command: GetScannerRecordingStatus | SetScannerRecordingStatus,
    code: str,
    reason: str | None,
) -> None:
    with pytest.raises(ScannerRecordingControlError) as caught:
        command.parse_response(
            Packet(command="URC", fields=("ERR", code), raw=f"URC,ERR,{code}")
        )

    assert caught.value.code == code
    assert caught.value.reason == reason
    assert str(caught.value) == (
        f"Scanner recording control failed with error code {code}"
        + (f": {reason}" if reason is not None else "")
        + "."
    )


@pytest.mark.parametrize(
    "fields",
    [
        ("ERR",),
        ("ERR", ""),
        ("ERR", "001"),
        ("ERR", "00001"),
        ("ERR", "ABCD"),
        ("ERR", " 0001"),
        ("ERR", "0001 "),
        (" ERR", "0001"),
        ("ERR", "0001", "EXTRA"),
    ],
)
def test_scanner_recording_control_rejects_malformed_operation_error(
    fields: tuple[str, ...],
) -> None:
    with pytest.raises(ProtocolError, match="URC"):
        SetScannerRecordingStatus(1).parse_response(
            Packet(command="URC", fields=fields, raw="URC," + ",".join(fields))
        )


@pytest.mark.parametrize(
    "response",
    [
        object(),
        Packet(command="OTHER", fields=("OK",), raw="OTHER,OK"),
        Packet(command="URC", fields=(), raw="URC"),
        Packet(command="URC", fields=(" OK",), raw="URC, OK"),
        Packet(command="URC", fields=("OK ",), raw="URC,OK "),
        Packet(command="URC", fields=(" NG",), raw="URC, NG"),
        Packet(command="URC", fields=("OK", "EXTRA"), raw="URC,OK,EXTRA"),
    ],
)
def test_set_scanner_recording_status_rejects_malformed_ack(response: object) -> None:
    with pytest.raises(ProtocolError, match="URC"):
        SetScannerRecordingStatus(1).parse_response(response)


def test_psi_command_accepts_acknowledgement() -> None:
    packet = Packet(command="PSI", fields=("OK",), raw="PSI,OK")
    assert StartScannerInfoPush().parse_response(packet) is None


def test_psi_command_rejects_negative_acknowledgement() -> None:
    packet = Packet(command="PSI", fields=("NG",), raw="PSI,NG")
    with pytest.raises(ProtocolError, match="rejected PSI"):
        StartScannerInfoPush().parse_response(packet)


def test_handheld_volume_and_squelch_limits() -> None:
    assert SetVolume(15, maximum=15).wire == "VOL,15"
    assert SetSquelch(15, maximum=15).wire == "SQL,15"
    with pytest.raises(ValueError, match="between 0 and 15"):
        SetVolume(16, maximum=15)
    with pytest.raises(ValueError, match="between 0 and 15"):
        SetSquelch(16, maximum=15)


def test_hold_related_key_press_wire() -> None:
    assert PressKey("A").wire == "KEY,A,P"
    assert PressKey("b").wire == "KEY,B,P"
    assert PressKey(" F ").wire == "KEY,F,P"


@pytest.mark.parametrize("value", ["", "M", "1", "A,P"])
def test_hold_related_key_press_rejects_other_keys(value: str) -> None:
    with pytest.raises(ValueError, match="Hold-related key code"):
        PressKey(value)


def test_hold_related_key_press_acknowledgement() -> None:
    command = PressKey("C")
    command.parse_response(Packet(command="KEY", fields=("OK",), raw="KEY,OK"))
    with pytest.raises(CommandRejectedError, match="rejected KEY"):
        command.parse_response(
            Packet(command="KEY", fields=("NG",), raw="KEY,NG")
        )


def test_navigation_command_wires() -> None:
    assert HoldSelection("SYS", 42).wire == "HLD,SYS,42,"
    assert NextSelection("DEPT", 7, 42, count=3).wire == "NXT,DEPT,7,42,3"
    assert PreviousSelection("TGID", 99, count=2).wire == "PRV,TGID,99,,2"


def test_navigation_commands_validate_target_and_count() -> None:
    with pytest.raises(ValueError, match="Navigation target"):
        NextSelection("INVALID")
    with pytest.raises(ValueError, match="between 1 and 8"):
        NextSelection("SYS", 1, count=9)
    with pytest.raises(ValueError, match="commas or line breaks"):
        _ = NextSelection("SYS", "1,2").wire


def test_navigation_acknowledgement() -> None:
    command = HoldSelection("SYS", 42)
    command.parse_response(Packet(command="HLD", fields=("OK",), raw="HLD,OK"))
    with pytest.raises(CommandRejectedError, match="rejected HLD"):
        command.parse_response(
            Packet(command="HLD", fields=("NG",), raw="HLD,NG")
        )
