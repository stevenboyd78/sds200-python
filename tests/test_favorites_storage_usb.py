from __future__ import annotations

import os
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from sds200.favorites_storage import FavoritesStorageSnapshot
from sds200.favorites_storage_local import (
    FavoritesCopiedTreeStorageSource,
)
from sds200.favorites_storage_usb import (
    DEFAULT_LINUX_MOUNTINFO_PATH,
    DEFAULT_LINUX_SYS_DEV_BLOCK_DIRECTORY,
    FavoritesUsbStorageCandidate,
    FavoritesUsbStorageQualification,
    FavoritesUsbStorageQualificationError,
    FavoritesUsbStorageQualificationReason,
    LinuxBlockDeviceError,
    LinuxBlockDeviceEvidence,
    LinuxMountInfoEntry,
    LinuxMountInfoError,
    _qualify_favorites_usb_storage_target_evidence,
    discover_favorites_usb_storage_candidates,
    qualify_favorites_usb_storage_path,
    read_linux_block_device_evidence,
    read_linux_mountinfo,
)


def _write_mountinfo(
    path: Path,
    *lines: str,
) -> None:
    path.write_text(
        "".join(
            f"{line}\n"
            for line in lines
        ),
        encoding="utf-8",
    )


def _entry() -> LinuxMountInfoEntry:
    return LinuxMountInfoEntry(
        mount_id=36,
        parent_id=25,
        device_major=8,
        device_minor=1,
        root="/",
        mount_point=Path("/media/scanner"),
        mount_options=(
            "rw",
            "nosuid",
            "nodev",
        ),
        optional_fields=(
            "shared:7",
        ),
        filesystem_type="vfat",
        mount_source="/dev/sdb1",
        super_options=(
            "rw",
            "uid=1000",
        ),
    )


def test_default_mountinfo_path_is_process_namespace() -> None:
    assert (
        Path("/proc/self/mountinfo")
        == DEFAULT_LINUX_MOUNTINFO_PATH
    )


def test_reads_linux_mountinfo_as_immutable_evidence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mountinfo"
    _write_mountinfo(
        path,
        (
            "36 25 8:1 / /media/scanner rw,nosuid,nodev "
            "shared:7 future:value - vfat /dev/sdb1 "
            "rw,uid=1000"
        ),
        (
            "37 25 8:2 / /media/archive ro,nosuid,nodev "
            "- exfat /dev/sdc1 ro"
        ),
    )

    entries = read_linux_mountinfo(
        path
    )

    assert len(entries) == 2

    scanner = entries[0]
    assert scanner.mount_id == 36
    assert scanner.parent_id == 25
    assert scanner.device_number == (8, 1)
    assert scanner.root == "/"
    assert scanner.mount_point == Path(
        "/media/scanner"
    )
    assert scanner.mount_options == (
        "rw",
        "nosuid",
        "nodev",
    )
    assert scanner.optional_fields == (
        "shared:7",
        "future:value",
    )
    assert scanner.filesystem_type == "vfat"
    assert scanner.mount_source == "/dev/sdb1"
    assert scanner.super_options == (
        "rw",
        "uid=1000",
    )
    assert scanner.is_writable is True
    assert scanner.is_read_only is False

    archive = entries[1]
    assert archive.device_number == (8, 2)
    assert archive.optional_fields == ()
    assert archive.is_read_only is True
    assert archive.is_writable is False


def test_mountinfo_decodes_kernel_path_escapes_once(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mountinfo"
    _write_mountinfo(
        path,
        (
            r"41 25 8:3 /folder\134040 "
            r"/media/My\040Scanner rw - vfat "
            r"/dev/disk/by-label/SDS\040CARD rw"
        ),
    )

    entry = read_linux_mountinfo(
        path
    )[0]

    assert entry.root == r"/folder\040"
    assert entry.mount_point == Path(
        "/media/My Scanner"
    )
    assert entry.mount_source == (
        "/dev/disk/by-label/SDS CARD"
    )


def test_mountinfo_preserves_non_path_namespace_root(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mountinfo"
    _write_mountinfo(
        path,
        (
            "3036 441 0:5 mnt:[4026533261] "
            "/run/snapd/ns/example.mnt rw - nsfs nsfs rw"
        ),
    )

    entry = read_linux_mountinfo(
        path
    )[0]

    assert entry.root == "mnt:[4026533261]"
    assert entry.mount_point == Path(
        "/run/snapd/ns/example.mnt"
    )
    assert entry.filesystem_type == "nsfs"


def test_unknown_optional_fields_do_not_break_parsing(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mountinfo"
    _write_mountinfo(
        path,
        (
            "44 25 8:4 / /media/scanner rw "
            "future:alpha another - vfat /dev/sdd1 rw"
        ),
    )

    entry = read_linux_mountinfo(
        path
    )[0]

    assert entry.optional_fields == (
        "future:alpha",
        "another",
    )


@pytest.mark.parametrize(
    ("line", "message"),
    [
        (
            "36 25 8:1 / /media/scanner rw vfat /dev/sdb1 rw",
            "separator",
        ),
        (
            "36 25 8:x / /media/scanner rw - vfat /dev/sdb1 rw",
            "device minor",
        ),
        (
            "36 25 8:1 / /media/scanner nodev - vfat /dev/sdb1 rw",
            "exactly one",
        ),
        (
            "36 25 8:1 / /media/scanner ro,rw - vfat /dev/sdb1 rw",
            "exactly one",
        ),
        (
            "36 25 8:1 / relative rw - vfat /dev/sdb1 rw",
            "mount point must be absolute",
        ),
    ],
)
def test_malformed_mountinfo_fails_closed(
    tmp_path: Path,
    line: str,
    message: str,
) -> None:
    path = tmp_path / "mountinfo"
    _write_mountinfo(
        path,
        line,
    )

    with pytest.raises(
        LinuxMountInfoError,
        match=message,
    ) as captured:
        read_linux_mountinfo(
            path
        )

    assert captured.value.path == path
    assert captured.value.line_number == 1


def test_duplicate_mount_id_is_rejected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mountinfo"
    _write_mountinfo(
        path,
        "36 25 8:1 / /media/one rw - vfat /dev/sdb1 rw",
        "36 25 8:2 / /media/two rw - vfat /dev/sdc1 rw",
    )

    with pytest.raises(
        LinuxMountInfoError,
        match="duplicate mount ID 36",
    ) as captured:
        read_linux_mountinfo(
            path
        )

    assert captured.value.line_number == 2


def test_empty_record_is_rejected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mountinfo"
    path.write_text(
        (
            "36 25 8:1 / /media/one rw - vfat /dev/sdb1 rw\n"
            "\n"
            "37 25 8:2 / /media/two rw - vfat /dev/sdc1 rw\n"
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        LinuxMountInfoError,
        match="empty record",
    ) as captured:
        read_linux_mountinfo(
            path
        )

    assert captured.value.line_number == 2


def test_missing_mountinfo_is_reported(
    tmp_path: Path,
) -> None:
    path = tmp_path / "missing"

    with pytest.raises(
        LinuxMountInfoError,
        match="Could not read mountinfo",
    ) as captured:
        read_linux_mountinfo(
            path
        )

    assert captured.value.path == path
    assert captured.value.line_number is None


def test_mountinfo_rejects_nul_bytes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "mountinfo"
    path.write_bytes(
        b"36 25 8:1 / /media/one rw - vfat /dev/sdb1 rw\x00\n"
    )

    with pytest.raises(
        LinuxMountInfoError,
        match="NUL",
    ):
        read_linux_mountinfo(
            path
        )


def test_mountinfo_path_requires_path_object() -> None:
    with pytest.raises(
        TypeError,
        match="pathlib.Path",
    ):
        read_linux_mountinfo(  # type: ignore[arg-type]
            "/proc/self/mountinfo"
        )


def test_mountinfo_entry_is_frozen_and_slot_backed() -> None:
    entry = _entry()

    assert not hasattr(
        entry,
        "__dict__",
    )

    with pytest.raises(
        FrozenInstanceError,
    ):
        entry.mount_id = 99  # type: ignore[misc]


@pytest.mark.parametrize(
    ("changes", "error_type", "message"),
    [
        (
            {"mount_id": 0},
            ValueError,
            "mount ID must be positive",
        ),
        (
            {"device_minor": -1},
            ValueError,
            "device minor must be non-negative",
        ),
        (
            {"root": 123},
            TypeError,
            "root must be a string",
        ),
        (
            {"root": ""},
            ValueError,
            "root must not be empty",
        ),
        (
            {"mount_options": ("nodev",)},
            ValueError,
            "exactly one",
        ),
        (
            {"filesystem_type": ""},
            ValueError,
            "filesystem type must not be empty",
        ),
        (
            {"mount_source": ""},
            ValueError,
            "mount source must not be empty",
        ),
    ],
)
def test_mountinfo_entry_validates_constructor_contract(
    changes: dict[str, object],
    error_type: type[Exception],
    message: str,
) -> None:
    values: dict[str, object] = {
        "mount_id": 36,
        "parent_id": 25,
        "device_major": 8,
        "device_minor": 1,
        "root": "/",
        "mount_point": Path("/media/scanner"),
        "mount_options": ("rw",),
        "optional_fields": (),
        "filesystem_type": "vfat",
        "mount_source": "/dev/sdb1",
        "super_options": ("rw",),
    }
    values.update(
        changes
    )

    with pytest.raises(
        error_type,
        match=message,
    ):
        LinuxMountInfoEntry(
            **values,  # type: ignore[arg-type]
        )


def test_default_sys_dev_block_directory_is_linux_sysfs() -> None:
    assert (
        Path("/sys/dev/block")
        == DEFAULT_LINUX_SYS_DEV_BLOCK_DIRECTORY
    )


def _sysfs_tree(
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path]:
    sysfs = tmp_path / "sys"
    dev_block = sysfs / "dev" / "block"
    usb_subsystem = sysfs / "bus" / "usb"
    block_subsystem = sysfs / "class" / "block"

    dev_block.mkdir(
        parents=True
    )
    usb_subsystem.mkdir(
        parents=True
    )
    block_subsystem.mkdir(
        parents=True
    )

    return (
        sysfs,
        dev_block,
        usb_subsystem,
        block_subsystem,
    )


def _symlink_or_skip(
    link: Path,
    target: Path,
) -> None:
    try:
        link.symlink_to(
            target,
            target_is_directory=target.is_dir(),
        )
    except OSError as error:
        pytest.skip(
            f"symbolic links unavailable: {error}"
        )


def test_reads_usb_block_device_evidence_from_sysfs(
    tmp_path: Path,
) -> None:
    (
        sysfs,
        dev_block,
        usb_subsystem,
        block_subsystem,
    ) = _sysfs_tree(
        tmp_path
    )

    usb_device = (
        sysfs
        / "devices"
        / "pci0000:00"
        / "usb1"
        / "1-1"
    )
    disk = usb_device / "block" / "sdb"
    partition = disk / "sdb1"
    partition.mkdir(
        parents=True
    )

    _symlink_or_skip(
        usb_device / "subsystem",
        usb_subsystem,
    )
    _symlink_or_skip(
        disk / "subsystem",
        block_subsystem,
    )
    _symlink_or_skip(
        partition / "subsystem",
        block_subsystem,
    )

    (disk / "removable").write_text(
        "1\n",
        encoding="ascii",
    )

    _symlink_or_skip(
        dev_block / "8:1",
        partition,
    )

    evidence = read_linux_block_device_evidence(
        8,
        1,
        sys_dev_block_directory=dev_block,
    )

    assert evidence.device_number == (8, 1)
    assert evidence.sysfs_path == partition
    assert evidence.device_name == "sdb1"
    assert evidence.usb_ancestor_path == usb_device
    assert evidence.is_usb is True
    assert evidence.removable is True


def test_non_usb_block_device_does_not_gain_usb_identity(
    tmp_path: Path,
) -> None:
    (
        sysfs,
        dev_block,
        _,
        block_subsystem,
    ) = _sysfs_tree(
        tmp_path
    )

    disk = (
        sysfs
        / "devices"
        / "pci0000:00"
        / "nvme"
        / "nvme0"
        / "nvme0n1"
    )
    partition = disk / "nvme0n1p1"
    partition.mkdir(
        parents=True
    )

    _symlink_or_skip(
        disk / "subsystem",
        block_subsystem,
    )
    _symlink_or_skip(
        partition / "subsystem",
        block_subsystem,
    )

    (disk / "removable").write_text(
        "0\n",
        encoding="ascii",
    )

    _symlink_or_skip(
        dev_block / "259:1",
        partition,
    )

    evidence = read_linux_block_device_evidence(
        259,
        1,
        sys_dev_block_directory=dev_block,
    )

    assert evidence.is_usb is False
    assert evidence.usb_ancestor_path is None
    assert evidence.removable is False


def test_usb_identity_does_not_require_removable_flag(
    tmp_path: Path,
) -> None:
    (
        sysfs,
        dev_block,
        usb_subsystem,
        _,
    ) = _sysfs_tree(
        tmp_path
    )

    usb_device = (
        sysfs
        / "devices"
        / "usb2"
        / "2-1"
    )
    partition = (
        usb_device
        / "block"
        / "sdc"
        / "sdc1"
    )
    partition.mkdir(
        parents=True
    )

    _symlink_or_skip(
        usb_device / "subsystem",
        usb_subsystem,
    )
    _symlink_or_skip(
        dev_block / "8:17",
        partition,
    )

    evidence = read_linux_block_device_evidence(
        8,
        17,
        sys_dev_block_directory=dev_block,
    )

    assert evidence.is_usb is True
    assert evidence.removable is None


def test_missing_sysfs_device_mapping_is_rejected(
    tmp_path: Path,
) -> None:
    _, dev_block, _, _ = _sysfs_tree(
        tmp_path
    )

    with pytest.raises(
        LinuxBlockDeviceError,
        match="No sysfs block-device mapping",
    ) as captured:
        read_linux_block_device_evidence(
            8,
            1,
            sys_dev_block_directory=dev_block,
        )

    assert captured.value.path == (
        dev_block / "8:1"
    )


def test_sysfs_device_mapping_must_be_symlink(
    tmp_path: Path,
) -> None:
    _, dev_block, _, _ = _sysfs_tree(
        tmp_path
    )
    mapping = dev_block / "8:1"
    mapping.mkdir()

    with pytest.raises(
        LinuxBlockDeviceError,
        match="must be a symbolic link",
    ):
        read_linux_block_device_evidence(
            8,
            1,
            sys_dev_block_directory=dev_block,
        )


def test_sysfs_device_mapping_cannot_escape_sysfs_root(
    tmp_path: Path,
) -> None:
    _, dev_block, _, _ = _sysfs_tree(
        tmp_path
    )
    outside = tmp_path / "outside"
    outside.mkdir()

    _symlink_or_skip(
        dev_block / "8:1",
        outside,
    )

    with pytest.raises(
        LinuxBlockDeviceError,
        match="outside the sysfs root",
    ):
        read_linux_block_device_evidence(
            8,
            1,
            sys_dev_block_directory=dev_block,
        )


def test_malformed_removable_state_is_rejected(
    tmp_path: Path,
) -> None:
    (
        sysfs,
        dev_block,
        _,
        _,
    ) = _sysfs_tree(
        tmp_path
    )
    disk = (
        sysfs
        / "devices"
        / "block"
        / "sdd"
    )
    partition = disk / "sdd1"
    partition.mkdir(
        parents=True
    )
    (disk / "removable").write_text(
        "yes\n",
        encoding="ascii",
    )

    _symlink_or_skip(
        dev_block / "8:49",
        partition,
    )

    with pytest.raises(
        LinuxBlockDeviceError,
        match="exactly 0 or 1",
    ):
        read_linux_block_device_evidence(
            8,
            49,
            sys_dev_block_directory=dev_block,
        )


def test_linux_block_device_evidence_is_frozen_and_slot_backed() -> None:
    evidence = LinuxBlockDeviceEvidence(
        device_major=8,
        device_minor=1,
        sysfs_path=Path("/sys/devices/test/sdb1"),
        device_name="sdb1",
        usb_ancestor_path=Path("/sys/devices/test/usb1"),
        removable=True,
    )

    assert not hasattr(
        evidence,
        "__dict__",
    )
    assert evidence.is_usb is True

    with pytest.raises(
        FrozenInstanceError,
    ):
        evidence.device_minor = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    ("changes", "error_type", "message"),
    [
        (
            {"device_major": -1},
            ValueError,
            "device major must be non-negative",
        ),
        (
            {"sysfs_path": Path("relative")},
            ValueError,
            "sysfs path must be absolute",
        ),
        (
            {"device_name": ""},
            ValueError,
            "name must not be empty",
        ),
        (
            {"usb_ancestor_path": Path("relative")},
            ValueError,
            "USB ancestor must be absolute",
        ),
        (
            {"removable": "yes"},
            TypeError,
            "removable state must be bool or None",
        ),
    ],
)
def test_linux_block_device_evidence_validates_constructor_contract(
    changes: dict[str, object],
    error_type: type[Exception],
    message: str,
) -> None:
    values: dict[str, object] = {
        "device_major": 8,
        "device_minor": 1,
        "sysfs_path": Path("/sys/devices/test/sdb1"),
        "device_name": "sdb1",
        "usb_ancestor_path": None,
        "removable": None,
    }
    values.update(
        changes
    )

    with pytest.raises(
        error_type,
        match=message,
    ):
        LinuxBlockDeviceEvidence(
            **values,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("major", "minor", "error_type", "message"),
    [
        (
            -1,
            1,
            ValueError,
            "device major must be non-negative",
        ),
        (
            8,
            -1,
            ValueError,
            "device minor must be non-negative",
        ),
        (
            True,
            1,
            TypeError,
            "device major must be an integer",
        ),
    ],
)
def test_block_device_reader_validates_device_number(
    tmp_path: Path,
    major: object,
    minor: object,
    error_type: type[Exception],
    message: str,
) -> None:
    _, dev_block, _, _ = _sysfs_tree(
        tmp_path
    )

    with pytest.raises(
        error_type,
        match=message,
    ):
        read_linux_block_device_evidence(
            major,  # type: ignore[arg-type]
            minor,  # type: ignore[arg-type]
            sys_dev_block_directory=dev_block,
        )


def _usb_qualification_fixture(
    tmp_path: Path,
    *,
    writable: bool = True,
    usb: bool = True,
) -> tuple[
    LinuxMountInfoEntry,
    LinuxBlockDeviceEvidence,
    Path,
]:
    mount_directory = (
        tmp_path
        / "mounted-scanner"
    )
    favorites_directory = (
        mount_directory
        / "BCDx36HP"
        / "favorites_lists"
    )
    favorites_directory.mkdir(
        parents=True
    )
    (
        favorites_directory
        / "f_list.cfg"
    ).write_bytes(
        b""
    )

    status = mount_directory.stat()
    major = os.major(
        status.st_dev
    )
    minor = os.minor(
        status.st_dev
    )

    mount = LinuxMountInfoEntry(
        mount_id=500,
        parent_id=1,
        device_major=major,
        device_minor=minor,
        root="/",
        mount_point=mount_directory,
        mount_options=(
            "rw" if writable else "ro",
        ),
        optional_fields=(),
        filesystem_type="vfat",
        mount_source="/dev/test-scanner",
        super_options=(
            "rw" if writable else "ro",
        ),
    )

    block_device = LinuxBlockDeviceEvidence(
        device_major=major,
        device_minor=minor,
        sysfs_path=Path(
            "/sys/devices/test/block/sdz/sdz1"
        ),
        device_name="sdz1",
        usb_ancestor_path=(
            Path(
                "/sys/devices/test/usb9/9-1"
            )
            if usb
            else None
        ),
        removable=True,
    )

    return (
        mount,
        block_device,
        favorites_directory,
    )


def test_qualifies_writable_usb_favorites_target_read_only(
    tmp_path: Path,
) -> None:
    (
        mount,
        block_device,
        favorites_directory,
    ) = _usb_qualification_fixture(
        tmp_path
    )

    qualification = _qualify_favorites_usb_storage_target_evidence(
        mount,
        block_device,
    )

    assert qualification.mount is mount
    assert qualification.block_device is block_device
    assert qualification.mount_directory == mount.mount_point
    assert qualification.favorites_directory == favorites_directory
    assert qualification.snapshot.catalog_bytes == b""
    assert qualification.snapshot.documents == ()

    assert tuple(
        path.name
        for path in tmp_path.iterdir()
    ) == (
        "mounted-scanner",
    )


def test_read_only_usb_mount_is_not_write_target_qualified(
    tmp_path: Path,
) -> None:
    mount, block_device, _ = _usb_qualification_fixture(
        tmp_path,
        writable=False,
    )

    with pytest.raises(
        FavoritesUsbStorageQualificationError,
    ) as captured:
        _qualify_favorites_usb_storage_target_evidence(
            mount,
            block_device,
        )

    assert (
        captured.value.reason
        is FavoritesUsbStorageQualificationReason.READ_ONLY_MOUNT
    )
    assert captured.value.path == mount.mount_point


def test_non_usb_mount_is_not_qualified(
    tmp_path: Path,
) -> None:
    mount, block_device, _ = _usb_qualification_fixture(
        tmp_path,
        usb=False,
    )

    with pytest.raises(
        FavoritesUsbStorageQualificationError,
    ) as captured:
        _qualify_favorites_usb_storage_target_evidence(
            mount,
            block_device,
        )

    assert (
        captured.value.reason
        is FavoritesUsbStorageQualificationReason.NOT_USB
    )


def test_mount_and_block_device_identity_must_match(
    tmp_path: Path,
) -> None:
    mount, block_device, _ = _usb_qualification_fixture(
        tmp_path
    )

    mismatched = LinuxBlockDeviceEvidence(
        device_major=block_device.device_major,
        device_minor=block_device.device_minor + 1,
        sysfs_path=block_device.sysfs_path,
        device_name=block_device.device_name,
        usb_ancestor_path=block_device.usb_ancestor_path,
        removable=block_device.removable,
    )

    with pytest.raises(
        FavoritesUsbStorageQualificationError,
    ) as captured:
        _qualify_favorites_usb_storage_target_evidence(
            mount,
            mismatched,
        )

    assert (
        captured.value.reason
        is FavoritesUsbStorageQualificationReason.DEVICE_IDENTITY_MISMATCH
    )


def test_missing_bcd_directory_is_not_qualified(
    tmp_path: Path,
) -> None:
    mount, block_device, favorites_directory = (
        _usb_qualification_fixture(
            tmp_path
        )
    )
    favorites_directory.joinpath(
        "f_list.cfg"
    ).unlink()
    favorites_directory.rmdir()
    favorites_directory.parent.rmdir()

    with pytest.raises(
        FavoritesUsbStorageQualificationError,
    ) as captured:
        _qualify_favorites_usb_storage_target_evidence(
            mount,
            block_device,
        )

    assert (
        captured.value.reason
        is FavoritesUsbStorageQualificationReason.TARGET_UNAVAILABLE
    )


def test_symlinked_bcd_directory_is_rejected(
    tmp_path: Path,
) -> None:
    mount, block_device, favorites_directory = (
        _usb_qualification_fixture(
            tmp_path
        )
    )
    bcd_directory = favorites_directory.parent

    favorites_directory.joinpath(
        "f_list.cfg"
    ).unlink()
    favorites_directory.rmdir()
    bcd_directory.rmdir()

    outside = tmp_path / "outside-bcd"
    (
        outside
        / "favorites_lists"
    ).mkdir(
        parents=True
    )
    (
        outside
        / "favorites_lists"
        / "f_list.cfg"
    ).write_bytes(
        b""
    )

    _symlink_or_skip(
        bcd_directory,
        outside,
    )

    with pytest.raises(
        FavoritesUsbStorageQualificationError,
    ) as captured:
        _qualify_favorites_usb_storage_target_evidence(
            mount,
            block_device,
        )

    assert (
        captured.value.reason
        is FavoritesUsbStorageQualificationReason.TARGET_UNSAFE
    )
    assert "symbolic link" in captured.value.message


def test_symlinked_favorites_directory_is_rejected(
    tmp_path: Path,
) -> None:
    mount, block_device, favorites_directory = (
        _usb_qualification_fixture(
            tmp_path
        )
    )

    favorites_directory.joinpath(
        "f_list.cfg"
    ).unlink()
    favorites_directory.rmdir()

    outside = tmp_path / "outside-favorites"
    outside.mkdir()
    (
        outside
        / "f_list.cfg"
    ).write_bytes(
        b""
    )

    _symlink_or_skip(
        favorites_directory,
        outside,
    )

    with pytest.raises(
        FavoritesUsbStorageQualificationError,
    ) as captured:
        _qualify_favorites_usb_storage_target_evidence(
            mount,
            block_device,
        )

    assert (
        captured.value.reason
        is FavoritesUsbStorageQualificationReason.TARGET_UNSAFE
    )


def test_invalid_copied_tree_storage_is_not_qualified(
    tmp_path: Path,
) -> None:
    mount, block_device, favorites_directory = (
        _usb_qualification_fixture(
            tmp_path
        )
    )

    (
        favorites_directory
        / "f_list.cfg"
    ).unlink()
    (
        favorites_directory
        / "f_list.cfg"
    ).mkdir()

    with pytest.raises(
        FavoritesUsbStorageQualificationError,
    ) as captured:
        _qualify_favorites_usb_storage_target_evidence(
            mount,
            block_device,
        )

    assert (
        captured.value.reason
        is FavoritesUsbStorageQualificationReason.TARGET_STORAGE_INVALID
    )


def test_usb_storage_qualification_is_frozen_and_slot_backed(
    tmp_path: Path,
) -> None:
    mount, block_device, _ = _usb_qualification_fixture(
        tmp_path
    )
    qualification = _qualify_favorites_usb_storage_target_evidence(
        mount,
        block_device,
    )

    assert not hasattr(
        qualification,
        "__dict__",
    )

    with pytest.raises(
        FrozenInstanceError,
    ):
        qualification.mount_directory = Path("/")  # type: ignore[misc]


def test_qualification_model_rejects_inconsistent_identity(
    tmp_path: Path,
) -> None:
    mount, block_device, favorites_directory = (
        _usb_qualification_fixture(
            tmp_path
        )
    )
    snapshot = FavoritesCopiedTreeStorageSource(
        favorites_directory
    ).read_snapshot()

    mismatched = LinuxBlockDeviceEvidence(
        device_major=block_device.device_major,
        device_minor=block_device.device_minor + 1,
        sysfs_path=block_device.sysfs_path,
        device_name=block_device.device_name,
        usb_ancestor_path=block_device.usb_ancestor_path,
        removable=block_device.removable,
    )

    with pytest.raises(
        ValueError,
        match="identities must match",
    ):
        FavoritesUsbStorageQualification(
            mount=mount,
            block_device=mismatched,
            mount_directory=mount.mount_point,
            favorites_directory=favorites_directory,
            snapshot=snapshot,
        )


def _write_usb_discovery_mountinfo(
    path: Path,
    *,
    mount_id: int,
    mount_directory: Path,
    device_major: int,
    device_minor: int,
    writable: bool,
) -> None:
    mode = (
        "rw"
        if writable
        else "ro"
    )
    _write_mountinfo(
        path,
        (
            f"{mount_id} 1 {device_major}:{device_minor} / "
            f"{mount_directory} {mode} - vfat "
            f"/dev/test-{mount_id} {mode}"
        ),
    )


def _add_usb_sysfs_mapping(
    sysfs: Path,
    dev_block: Path,
    usb_subsystem: Path,
    *,
    device_major: int,
    device_minor: int,
    name: str,
) -> None:
    usb_device = (
        sysfs
        / "devices"
        / "pci0000:00"
        / "usb9"
        / "9-1"
    )
    partition = (
        usb_device
        / "block"
        / name.removesuffix("1")
        / name
    )
    partition.mkdir(
        parents=True,
        exist_ok=True,
    )

    subsystem = (
        usb_device
        / "subsystem"
    )
    if not subsystem.exists():
        _symlink_or_skip(
            subsystem,
            usb_subsystem,
        )

    mapping = (
        dev_block
        / f"{device_major}:{device_minor}"
    )
    _symlink_or_skip(
        mapping,
        partition,
    )


def test_discovers_writable_usb_favorites_candidate(
    tmp_path: Path,
) -> None:
    (
        sysfs,
        dev_block,
        usb_subsystem,
        _,
    ) = _sysfs_tree(
        tmp_path
    )
    mount_directory = (
        tmp_path
        / "scanner"
    )
    favorites_directory = (
        mount_directory
        / "BCDx36HP"
        / "favorites_lists"
    )
    favorites_directory.mkdir(
        parents=True
    )
    (
        favorites_directory
        / "f_list.cfg"
    ).write_bytes(
        b""
    )

    status = mount_directory.stat()
    major = os.major(
        status.st_dev
    )
    minor = os.minor(
        status.st_dev
    )

    mountinfo = (
        tmp_path
        / "mountinfo"
    )
    _write_usb_discovery_mountinfo(
        mountinfo,
        mount_id=700,
        mount_directory=mount_directory,
        device_major=major,
        device_minor=minor,
        writable=True,
    )
    _add_usb_sysfs_mapping(
        sysfs,
        dev_block,
        usb_subsystem,
        device_major=major,
        device_minor=minor,
        name="sdz1",
    )

    candidates = discover_favorites_usb_storage_candidates(
        mountinfo,
        sys_dev_block_directory=dev_block,
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.mount_directory == mount_directory
    assert candidate.favorites_directory == favorites_directory
    assert candidate.is_writable is True
    assert candidate.is_read_only is False
    assert candidate.snapshot.catalog_bytes == b""


def test_discovers_read_only_usb_favorites_candidate(
    tmp_path: Path,
) -> None:
    (
        sysfs,
        dev_block,
        usb_subsystem,
        _,
    ) = _sysfs_tree(
        tmp_path
    )
    mount_directory = (
        tmp_path
        / "scanner-ro"
    )
    favorites_directory = (
        mount_directory
        / "BCDx36HP"
        / "favorites_lists"
    )
    favorites_directory.mkdir(
        parents=True
    )
    (
        favorites_directory
        / "f_list.cfg"
    ).write_bytes(
        b""
    )

    status = mount_directory.stat()
    major = os.major(
        status.st_dev
    )
    minor = os.minor(
        status.st_dev
    )

    mountinfo = (
        tmp_path
        / "mountinfo"
    )
    _write_usb_discovery_mountinfo(
        mountinfo,
        mount_id=701,
        mount_directory=mount_directory,
        device_major=major,
        device_minor=minor,
        writable=False,
    )
    _add_usb_sysfs_mapping(
        sysfs,
        dev_block,
        usb_subsystem,
        device_major=major,
        device_minor=minor,
        name="sdy1",
    )

    candidates = discover_favorites_usb_storage_candidates(
        mountinfo,
        sys_dev_block_directory=dev_block,
    )

    assert len(candidates) == 1
    assert candidates[0].is_read_only is True
    assert candidates[0].is_writable is False


def test_discovery_ignores_mount_without_sysfs_block_mapping(
    tmp_path: Path,
) -> None:
    _, dev_block, _, _ = _sysfs_tree(
        tmp_path
    )
    mount_directory = (
        tmp_path
        / "not-block-backed"
    )
    favorites_directory = (
        mount_directory
        / "BCDx36HP"
        / "favorites_lists"
    )
    favorites_directory.mkdir(
        parents=True
    )
    (
        favorites_directory
        / "f_list.cfg"
    ).write_bytes(
        b""
    )

    status = mount_directory.stat()
    mountinfo = (
        tmp_path
        / "mountinfo"
    )
    _write_usb_discovery_mountinfo(
        mountinfo,
        mount_id=702,
        mount_directory=mount_directory,
        device_major=os.major(
            status.st_dev
        ),
        device_minor=os.minor(
            status.st_dev
        ),
        writable=True,
    )

    assert discover_favorites_usb_storage_candidates(
        mountinfo,
        sys_dev_block_directory=dev_block,
    ) == ()


def test_discovery_ignores_usb_mount_without_scanner_tree(
    tmp_path: Path,
) -> None:
    (
        sysfs,
        dev_block,
        usb_subsystem,
        _,
    ) = _sysfs_tree(
        tmp_path
    )
    mount_directory = (
        tmp_path
        / "ordinary-usb"
    )
    mount_directory.mkdir()

    status = mount_directory.stat()
    major = os.major(
        status.st_dev
    )
    minor = os.minor(
        status.st_dev
    )

    mountinfo = (
        tmp_path
        / "mountinfo"
    )
    _write_usb_discovery_mountinfo(
        mountinfo,
        mount_id=703,
        mount_directory=mount_directory,
        device_major=major,
        device_minor=minor,
        writable=True,
    )
    _add_usb_sysfs_mapping(
        sysfs,
        dev_block,
        usb_subsystem,
        device_major=major,
        device_minor=minor,
        name="sdx1",
    )

    assert discover_favorites_usb_storage_candidates(
        mountinfo,
        sys_dev_block_directory=dev_block,
    ) == ()


def test_discovery_rejects_scanner_like_usb_tree_with_missing_favorites(
    tmp_path: Path,
) -> None:
    (
        sysfs,
        dev_block,
        usb_subsystem,
        _,
    ) = _sysfs_tree(
        tmp_path
    )
    mount_directory = (
        tmp_path
        / "scanner-incomplete"
    )
    (
        mount_directory
        / "BCDx36HP"
    ).mkdir(
        parents=True
    )

    status = mount_directory.stat()
    major = os.major(
        status.st_dev
    )
    minor = os.minor(
        status.st_dev
    )

    mountinfo = (
        tmp_path
        / "mountinfo"
    )
    _write_usb_discovery_mountinfo(
        mountinfo,
        mount_id=704,
        mount_directory=mount_directory,
        device_major=major,
        device_minor=minor,
        writable=True,
    )
    _add_usb_sysfs_mapping(
        sysfs,
        dev_block,
        usb_subsystem,
        device_major=major,
        device_minor=minor,
        name="sdw1",
    )

    with pytest.raises(
        FavoritesUsbStorageQualificationError,
    ) as captured:
        discover_favorites_usb_storage_candidates(
            mountinfo,
            sys_dev_block_directory=dev_block,
        )

    assert (
        captured.value.reason
        is FavoritesUsbStorageQualificationReason.TARGET_UNAVAILABLE
    )


def test_usb_storage_candidate_is_frozen_and_slot_backed(
    tmp_path: Path,
) -> None:
    mount, block_device, favorites_directory = (
        _usb_qualification_fixture(
            tmp_path,
            writable=False,
        )
    )
    snapshot = FavoritesCopiedTreeStorageSource(
        favorites_directory
    ).read_snapshot()

    candidate = FavoritesUsbStorageCandidate(
        mount=mount,
        block_device=block_device,
        mount_directory=mount.mount_point,
        favorites_directory=favorites_directory,
        snapshot=snapshot,
    )

    assert candidate.is_read_only is True
    assert not hasattr(
        candidate,
        "__dict__",
    )

    with pytest.raises(
        FrozenInstanceError,
    ):
        candidate.mount_directory = Path("/")  # type: ignore[misc]


def _explicit_usb_path_fixture(
    tmp_path: Path,
    *,
    writable: bool = True,
) -> tuple[
    Path,
    Path,
    Path,
    Path,
]:
    (
        sysfs,
        dev_block,
        usb_subsystem,
        _,
    ) = _sysfs_tree(
        tmp_path
    )

    mount_directory = (
        tmp_path
        / "explicit-scanner"
    )
    favorites_directory = (
        mount_directory
        / "BCDx36HP"
        / "favorites_lists"
    )
    favorites_directory.mkdir(
        parents=True
    )
    (
        favorites_directory
        / "f_list.cfg"
    ).write_bytes(
        b""
    )

    status = mount_directory.stat()
    major = os.major(
        status.st_dev
    )
    minor = os.minor(
        status.st_dev
    )

    mountinfo = (
        tmp_path
        / "mountinfo-explicit"
    )
    _write_usb_discovery_mountinfo(
        mountinfo,
        mount_id=800,
        mount_directory=mount_directory,
        device_major=major,
        device_minor=minor,
        writable=writable,
    )

    _add_usb_sysfs_mapping(
        sysfs,
        dev_block,
        usb_subsystem,
        device_major=major,
        device_minor=minor,
        name="sdq1",
    )

    return (
        mountinfo,
        dev_block,
        mount_directory,
        favorites_directory,
    )


def test_explicit_mount_path_qualifies_current_usb_target(
    tmp_path: Path,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        favorites_directory,
    ) = _explicit_usb_path_fixture(
        tmp_path
    )

    qualification = qualify_favorites_usb_storage_path(
        mount_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )

    assert qualification.mount_directory == mount_directory
    assert qualification.favorites_directory == favorites_directory
    assert qualification.snapshot.catalog_bytes == b""


def test_explicit_favorites_path_qualifies_same_usb_target(
    tmp_path: Path,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        favorites_directory,
    ) = _explicit_usb_path_fixture(
        tmp_path
    )

    qualification = qualify_favorites_usb_storage_path(
        favorites_directory,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )

    assert qualification.mount_directory == mount_directory
    assert qualification.favorites_directory == favorites_directory


def test_explicit_read_only_path_fails_closed(
    tmp_path: Path,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        _,
    ) = _explicit_usb_path_fixture(
        tmp_path,
        writable=False,
    )

    with pytest.raises(
        FavoritesUsbStorageQualificationError,
    ) as captured:
        qualify_favorites_usb_storage_path(
            mount_directory,
            mountinfo,
            sys_dev_block_directory=dev_block,
        )

    assert (
        captured.value.reason
        is FavoritesUsbStorageQualificationReason.READ_ONLY_MOUNT
    )


def test_explicit_path_must_be_absolute() -> None:
    with pytest.raises(
        ValueError,
        match="must be absolute",
    ):
        qualify_favorites_usb_storage_path(
            Path("scanner")
        )


def test_explicit_symlink_path_is_rejected(
    tmp_path: Path,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        _,
    ) = _explicit_usb_path_fixture(
        tmp_path
    )
    alias = (
        tmp_path
        / "scanner-alias"
    )
    _symlink_or_skip(
        alias,
        mount_directory,
    )

    with pytest.raises(
        FavoritesUsbStorageQualificationError,
    ) as captured:
        qualify_favorites_usb_storage_path(
            alias,
            mountinfo,
            sys_dev_block_directory=dev_block,
        )

    assert (
        captured.value.reason
        is FavoritesUsbStorageQualificationReason.TARGET_UNSAFE
    )


def test_explicit_path_must_exactly_match_mount_or_favorites(
    tmp_path: Path,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        _,
    ) = _explicit_usb_path_fixture(
        tmp_path
    )
    child = (
        mount_directory
        / "BCDx36HP"
    )

    with pytest.raises(
        FavoritesUsbStorageQualificationError,
    ) as captured:
        qualify_favorites_usb_storage_path(
            child,
            mountinfo,
            sys_dev_block_directory=dev_block,
        )

    assert (
        captured.value.reason
        is FavoritesUsbStorageQualificationReason.TARGET_UNAVAILABLE
    )


def test_explicit_path_rejects_ambiguous_mountinfo_match(
    tmp_path: Path,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        _,
    ) = _explicit_usb_path_fixture(
        tmp_path
    )

    original = mountinfo.read_text(
        encoding="utf-8"
    ).rstrip(
        "\n"
    )
    fields = original.split()
    fields[0] = "801"
    duplicate = " ".join(
        fields
    )

    mountinfo.write_text(
        f"{original}\n{duplicate}\n",
        encoding="utf-8",
    )

    with pytest.raises(
        FavoritesUsbStorageQualificationError,
    ) as captured:
        qualify_favorites_usb_storage_path(
            mount_directory,
            mountinfo,
            sys_dev_block_directory=dev_block,
        )

    assert (
        captured.value.reason
        is FavoritesUsbStorageQualificationReason.AMBIGUOUS_TARGET
    )


def test_explicit_non_block_mount_is_not_treated_as_usb(
    tmp_path: Path,
) -> None:
    _, dev_block, _, _ = _sysfs_tree(
        tmp_path
    )

    mount_directory = (
        tmp_path
        / "not-usb"
    )
    favorites_directory = (
        mount_directory
        / "BCDx36HP"
        / "favorites_lists"
    )
    favorites_directory.mkdir(
        parents=True
    )
    (
        favorites_directory
        / "f_list.cfg"
    ).write_bytes(
        b""
    )

    status = mount_directory.stat()
    mountinfo = (
        tmp_path
        / "mountinfo-not-usb"
    )
    _write_usb_discovery_mountinfo(
        mountinfo,
        mount_id=802,
        mount_directory=mount_directory,
        device_major=os.major(
            status.st_dev
        ),
        device_minor=os.minor(
            status.st_dev
        ),
        writable=True,
    )

    with pytest.raises(
        FavoritesUsbStorageQualificationError,
    ) as captured:
        qualify_favorites_usb_storage_path(
            mount_directory,
            mountinfo,
            sys_dev_block_directory=dev_block,
        )

    assert (
        captured.value.reason
        is FavoritesUsbStorageQualificationReason.NOT_USB
    )


def test_mount_state_fails_closed_when_superblock_is_read_only() -> None:
    entry = LinuxMountInfoEntry(
        mount_id=900,
        parent_id=1,
        device_major=8,
        device_minor=1,
        root="/",
        mount_point=Path("/media/scanner"),
        mount_options=("rw",),
        optional_fields=(),
        filesystem_type="vfat",
        mount_source="/dev/sdb1",
        super_options=("ro",),
    )

    assert entry.is_read_only is True
    assert entry.is_writable is False


def test_discovery_rejects_mount_that_changes_during_readback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        sysfs,
        dev_block,
        usb_subsystem,
        _,
    ) = _sysfs_tree(
        tmp_path
    )
    mount_directory = (
        tmp_path
        / "stale-scanner"
    )
    favorites_directory = (
        mount_directory
        / "BCDx36HP"
        / "favorites_lists"
    )
    favorites_directory.mkdir(
        parents=True
    )
    (
        favorites_directory
        / "f_list.cfg"
    ).write_bytes(
        b""
    )

    status = mount_directory.stat()
    major = os.major(
        status.st_dev
    )
    minor = os.minor(
        status.st_dev
    )
    mountinfo = (
        tmp_path
        / "mountinfo-stale"
    )
    _write_usb_discovery_mountinfo(
        mountinfo,
        mount_id=901,
        mount_directory=mount_directory,
        device_major=major,
        device_minor=minor,
        writable=True,
    )
    _add_usb_sysfs_mapping(
        sysfs,
        dev_block,
        usb_subsystem,
        device_major=major,
        device_minor=minor,
        name="sdt1",
    )

    original = (
        FavoritesCopiedTreeStorageSource.read_snapshot
    )

    def read_and_remount(
        source: FavoritesCopiedTreeStorageSource,
    ) -> FavoritesStorageSnapshot:
        snapshot = original(
            source
        )
        current = mountinfo.read_text(
            encoding="utf-8"
        )
        mountinfo.write_text(
            current.replace(
                "901 1 ",
                "902 1 ",
                1,
            ),
            encoding="utf-8",
        )
        return snapshot

    monkeypatch.setattr(
        FavoritesCopiedTreeStorageSource,
        "read_snapshot",
        read_and_remount,
    )

    with pytest.raises(
        FavoritesUsbStorageQualificationError,
    ) as captured:
        discover_favorites_usb_storage_candidates(
            mountinfo,
            sys_dev_block_directory=dev_block,
        )

    assert (
        captured.value.reason
        is FavoritesUsbStorageQualificationReason.STALE_MOUNT
    )


def test_discovery_rejects_block_mapping_that_disappears_during_readback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        sysfs,
        dev_block,
        usb_subsystem,
        _,
    ) = _sysfs_tree(
        tmp_path
    )
    mount_directory = (
        tmp_path
        / "stale-device-scanner"
    )
    favorites_directory = (
        mount_directory
        / "BCDx36HP"
        / "favorites_lists"
    )
    favorites_directory.mkdir(
        parents=True
    )
    (
        favorites_directory
        / "f_list.cfg"
    ).write_bytes(
        b""
    )

    status = mount_directory.stat()
    major = os.major(
        status.st_dev
    )
    minor = os.minor(
        status.st_dev
    )
    mountinfo = (
        tmp_path
        / "mountinfo-stale-device"
    )
    _write_usb_discovery_mountinfo(
        mountinfo,
        mount_id=903,
        mount_directory=mount_directory,
        device_major=major,
        device_minor=minor,
        writable=True,
    )
    _add_usb_sysfs_mapping(
        sysfs,
        dev_block,
        usb_subsystem,
        device_major=major,
        device_minor=minor,
        name="sdu1",
    )

    mapping = (
        dev_block
        / f"{major}:{minor}"
    )
    original = (
        FavoritesCopiedTreeStorageSource.read_snapshot
    )

    def read_and_disconnect(
        source: FavoritesCopiedTreeStorageSource,
    ) -> FavoritesStorageSnapshot:
        snapshot = original(
            source
        )
        mapping.unlink()
        return snapshot

    monkeypatch.setattr(
        FavoritesCopiedTreeStorageSource,
        "read_snapshot",
        read_and_disconnect,
    )

    with pytest.raises(
        FavoritesUsbStorageQualificationError,
    ) as captured:
        discover_favorites_usb_storage_candidates(
            mountinfo,
            sys_dev_block_directory=dev_block,
        )

    assert (
        captured.value.reason
        is FavoritesUsbStorageQualificationReason.STALE_DEVICE
    )


def test_discovery_rejects_duplicate_mounts_for_same_favorites_target(
    tmp_path: Path,
) -> None:
    (
        sysfs,
        dev_block,
        usb_subsystem,
        _,
    ) = _sysfs_tree(
        tmp_path
    )
    mount_directory = (
        tmp_path
        / "ambiguous-scanner"
    )
    favorites_directory = (
        mount_directory
        / "BCDx36HP"
        / "favorites_lists"
    )
    favorites_directory.mkdir(
        parents=True
    )
    (
        favorites_directory
        / "f_list.cfg"
    ).write_bytes(
        b""
    )

    status = mount_directory.stat()
    major = os.major(
        status.st_dev
    )
    minor = os.minor(
        status.st_dev
    )
    mountinfo = (
        tmp_path
        / "mountinfo-ambiguous"
    )
    _write_usb_discovery_mountinfo(
        mountinfo,
        mount_id=904,
        mount_directory=mount_directory,
        device_major=major,
        device_minor=minor,
        writable=True,
    )
    first = mountinfo.read_text(
        encoding="utf-8"
    ).rstrip(
        "\n"
    )
    second = first.replace(
        "904 1 ",
        "905 1 ",
        1,
    )
    mountinfo.write_text(
        f"{first}\n{second}\n",
        encoding="utf-8",
    )

    _add_usb_sysfs_mapping(
        sysfs,
        dev_block,
        usb_subsystem,
        device_major=major,
        device_minor=minor,
        name="sdv1",
    )

    with pytest.raises(
        FavoritesUsbStorageQualificationError,
    ) as captured:
        discover_favorites_usb_storage_candidates(
            mountinfo,
            sys_dev_block_directory=dev_block,
        )

    assert (
        captured.value.reason
        is FavoritesUsbStorageQualificationReason.AMBIGUOUS_TARGET
    )


def test_explicit_path_rejects_mount_that_changes_during_readback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        mountinfo,
        dev_block,
        mount_directory,
        _,
    ) = _explicit_usb_path_fixture(
        tmp_path
    )

    original = (
        FavoritesCopiedTreeStorageSource.read_snapshot
    )

    def read_and_change_mount(
        source: FavoritesCopiedTreeStorageSource,
    ) -> FavoritesStorageSnapshot:
        snapshot = original(
            source
        )
        current = mountinfo.read_text(
            encoding="utf-8"
        )
        mountinfo.write_text(
            current.replace(
                "800 1 ",
                "806 1 ",
                1,
            ),
            encoding="utf-8",
        )
        return snapshot

    monkeypatch.setattr(
        FavoritesCopiedTreeStorageSource,
        "read_snapshot",
        read_and_change_mount,
    )

    with pytest.raises(
        FavoritesUsbStorageQualificationError,
    ) as captured:
        qualify_favorites_usb_storage_path(
            mount_directory,
            mountinfo,
            sys_dev_block_directory=dev_block,
        )

    assert (
        captured.value.reason
        is FavoritesUsbStorageQualificationReason.STALE_MOUNT
    )


def test_usb_storage_public_package_exports() -> None:
    import sds200
    import sds200.favorites_storage_usb as usb

    expected = tuple(
        usb.__all__
    )

    for name in expected:
        assert name in sds200.__all__
        assert getattr(
            sds200,
            name,
        ) is getattr(
            usb,
            name,
        )


def test_discovery_returns_canonical_storage_paths(
    tmp_path: Path,
) -> None:
    (
        sysfs,
        dev_block,
        usb_subsystem,
        _,
    ) = _sysfs_tree(
        tmp_path
    )

    real_parent = tmp_path / "real-mount-parent"
    real_parent.mkdir()
    alias_parent = tmp_path / "alias-mount-parent"
    _symlink_or_skip(
        alias_parent,
        real_parent,
    )

    real_mount = real_parent / "scanner"
    favorites_directory = (
        real_mount
        / "BCDx36HP"
        / "favorites_lists"
    )
    favorites_directory.mkdir(
        parents=True
    )
    (
        favorites_directory
        / "f_list.cfg"
    ).write_bytes(
        b""
    )

    alias_mount = alias_parent / "scanner"
    status = real_mount.stat()
    major = os.major(status.st_dev)
    minor = os.minor(status.st_dev)

    mountinfo = tmp_path / "mountinfo-canonical"
    _write_usb_discovery_mountinfo(
        mountinfo,
        mount_id=910,
        mount_directory=alias_mount,
        device_major=major,
        device_minor=minor,
        writable=True,
    )
    _add_usb_sysfs_mapping(
        sysfs,
        dev_block,
        usb_subsystem,
        device_major=major,
        device_minor=minor,
        name="sdx1",
    )

    candidates = discover_favorites_usb_storage_candidates(
        mountinfo,
        sys_dev_block_directory=dev_block,
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.mount.mount_point == alias_mount
    assert candidate.mount_directory == real_mount
    assert candidate.favorites_directory == favorites_directory
    assert (
        candidate.mount_directory
        == candidate.mount_directory.resolve(
            strict=True
        )
    )
    assert (
        candidate.favorites_directory
        == candidate.favorites_directory.resolve(
            strict=True
        )
    )


def test_explicit_path_qualification_returns_canonical_storage_paths(
    tmp_path: Path,
) -> None:
    (
        sysfs,
        dev_block,
        usb_subsystem,
        _,
    ) = _sysfs_tree(
        tmp_path
    )

    real_parent = tmp_path / "real-explicit-parent"
    real_parent.mkdir()
    alias_parent = tmp_path / "alias-explicit-parent"
    _symlink_or_skip(
        alias_parent,
        real_parent,
    )

    real_mount = real_parent / "scanner"
    favorites_directory = (
        real_mount
        / "BCDx36HP"
        / "favorites_lists"
    )
    favorites_directory.mkdir(
        parents=True
    )
    (
        favorites_directory
        / "f_list.cfg"
    ).write_bytes(
        b""
    )

    alias_mount = alias_parent / "scanner"
    status = real_mount.stat()
    major = os.major(status.st_dev)
    minor = os.minor(status.st_dev)

    mountinfo = (
        tmp_path
        / "mountinfo-explicit-canonical"
    )
    _write_usb_discovery_mountinfo(
        mountinfo,
        mount_id=911,
        mount_directory=alias_mount,
        device_major=major,
        device_minor=minor,
        writable=True,
    )
    _add_usb_sysfs_mapping(
        sysfs,
        dev_block,
        usb_subsystem,
        device_major=major,
        device_minor=minor,
        name="sdy1",
    )

    qualification = qualify_favorites_usb_storage_path(
        real_mount,
        mountinfo,
        sys_dev_block_directory=dev_block,
    )

    assert qualification.mount.mount_point == alias_mount
    assert qualification.mount_directory == real_mount
    assert qualification.favorites_directory == favorites_directory
    assert (
        qualification.mount_directory
        == qualification.mount_directory.resolve(
            strict=True
        )
    )
    assert (
        qualification.favorites_directory
        == qualification.favorites_directory.resolve(
            strict=True
        )
    )


def test_evidence_only_qualifier_is_not_public_package_api() -> None:
    import sds200
    import sds200.favorites_storage_usb as usb

    assert (
        "qualify_favorites_usb_storage_target"
        not in usb.__all__
    )
    assert (
        "qualify_favorites_usb_storage_target"
        not in sds200.__all__
    )
    assert not hasattr(
        sds200,
        "qualify_favorites_usb_storage_target",
    )


def test_discovery_revalidates_earlier_candidates_before_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        sysfs,
        dev_block,
        usb_subsystem,
        _,
    ) = _sysfs_tree(
        tmp_path
    )

    first_mount = tmp_path / "first-scanner"
    first_favorites = (
        first_mount
        / "BCDx36HP"
        / "favorites_lists"
    )
    first_favorites.mkdir(
        parents=True
    )
    (
        first_favorites
        / "f_list.cfg"
    ).write_bytes(
        b""
    )

    second_mount = tmp_path / "second-scanner"
    second_favorites = (
        second_mount
        / "BCDx36HP"
        / "favorites_lists"
    )
    second_favorites.mkdir(
        parents=True
    )
    (
        second_favorites
        / "f_list.cfg"
    ).write_bytes(
        b""
    )

    status = first_mount.stat()
    major = os.major(
        status.st_dev
    )
    minor = os.minor(
        status.st_dev
    )

    assert (
        second_mount.stat().st_dev
        == status.st_dev
    )

    mountinfo = (
        tmp_path
        / "mountinfo-final-revalidation"
    )
    _write_mountinfo(
        mountinfo,
        (
            f"920 1 {major}:{minor} / "
            f"{first_mount} rw - vfat "
            "/dev/test-920 rw"
        ),
        (
            f"921 1 {major}:{minor} / "
            f"{second_mount} rw - vfat "
            "/dev/test-921 rw"
        ),
    )

    _add_usb_sysfs_mapping(
        sysfs,
        dev_block,
        usb_subsystem,
        device_major=major,
        device_minor=minor,
        name="sdj1",
    )

    original = (
        FavoritesCopiedTreeStorageSource.read_snapshot
    )
    read_count = 0

    def read_and_stale_first_candidate(
        source: FavoritesCopiedTreeStorageSource,
    ) -> FavoritesStorageSnapshot:
        nonlocal read_count

        snapshot = original(
            source
        )
        read_count += 1

        if read_count == 2:
            current = mountinfo.read_text(
                encoding="utf-8"
            )
            mountinfo.write_text(
                current.replace(
                    "920 1 ",
                    "922 1 ",
                    1,
                ),
                encoding="utf-8",
            )

        return snapshot

    monkeypatch.setattr(
        FavoritesCopiedTreeStorageSource,
        "read_snapshot",
        read_and_stale_first_candidate,
    )

    with pytest.raises(
        FavoritesUsbStorageQualificationError,
    ) as captured:
        discover_favorites_usb_storage_candidates(
            mountinfo,
            sys_dev_block_directory=dev_block,
        )

    assert read_count == 2
    assert (
        captured.value.reason
        is FavoritesUsbStorageQualificationReason.STALE_MOUNT
    )
