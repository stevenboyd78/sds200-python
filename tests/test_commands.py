import pytest

from sds200.commands import (
    GetFavoritesQuickKeys,
    GetGltFavorites,
    HoldSelection,
    NextSelection,
    PressKey,
    PreviousSelection,
    SetFavoritesQuickKeys,
    SetSquelch,
    SetVolume,
    StartScannerInfoPush,
)
from sds200.exceptions import CommandRejectedError, ProtocolError
from sds200.models import (
    FavoritesQuickKeys,
    FavoritesQuickKeyState,
    GltResponse,
    Packet,
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
