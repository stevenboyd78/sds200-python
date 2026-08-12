"""Linux mount evidence for future Favorites USB mass-storage discovery."""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from .favorites_storage import FavoritesStorageSnapshot
from .favorites_storage_local import (
    FavoritesCopiedTreeStorageError,
    FavoritesCopiedTreeStorageSource,
)

DEFAULT_LINUX_MOUNTINFO_PATH = Path("/proc/self/mountinfo")

_MOUNTINFO_ESCAPE_PATTERN = re.compile(r"\\(011|012|040|134)")
_MOUNTINFO_ESCAPES = {
    "011": "\t",
    "012": "\n",
    "040": " ",
    "134": "\\",
}


class LinuxMountInfoError(RuntimeError):
    """Report malformed or unavailable Linux mountinfo evidence."""

    def __init__(
        self,
        path: Path,
        message: str,
        *,
        line_number: int | None = None,
    ) -> None:
        self.path = path
        self.line_number = line_number
        self.message = message

        location = (
            f"{path}:{line_number}"
            if line_number is not None
            else str(path)
        )
        super().__init__(
            f"Linux mountinfo error at {location}: {message}"
        )


def _require_string_tuple(
    value: tuple[str, ...],
    *,
    description: str,
    allow_empty: bool = False,
) -> None:
    if not isinstance(value, tuple):
        raise TypeError(
            f"{description} must be a tuple of strings."
        )
    if not allow_empty and not value:
        raise ValueError(
            f"{description} must not be empty."
        )
    for item in value:
        if not isinstance(item, str):
            raise TypeError(
                f"{description} must contain only strings."
            )
        if not item:
            raise ValueError(
                f"{description} must not contain empty strings."
            )


@dataclass(frozen=True, slots=True)
class LinuxMountInfoEntry:
    """Immutable evidence for one mount in a Linux mount namespace."""

    mount_id: int
    parent_id: int
    device_major: int
    device_minor: int
    root: str
    mount_point: Path
    mount_options: tuple[str, ...]
    optional_fields: tuple[str, ...]
    filesystem_type: str
    mount_source: str
    super_options: tuple[str, ...]

    def __post_init__(self) -> None:
        for name, value in (
            ("mount ID", self.mount_id),
            ("parent ID", self.parent_id),
        ):
            if type(value) is not int:
                raise TypeError(
                    f"Linux mountinfo {name} must be an integer."
                )
            if value <= 0:
                raise ValueError(
                    f"Linux mountinfo {name} must be positive."
                )

        for name, value in (
            ("device major", self.device_major),
            ("device minor", self.device_minor),
        ):
            if type(value) is not int:
                raise TypeError(
                    f"Linux mountinfo {name} must be an integer."
                )
            if value < 0:
                raise ValueError(
                    f"Linux mountinfo {name} must be non-negative."
                )

        if not isinstance(
            self.root,
            str,
        ):
            raise TypeError(
                "Linux mountinfo root must be a string."
            )
        if not self.root:
            raise ValueError(
                "Linux mountinfo root must not be empty."
            )

        if not isinstance(
            self.mount_point,
            Path,
        ):
            raise TypeError(
                "Linux mountinfo mount point must be pathlib.Path."
            )
        if not self.mount_point.is_absolute():
            raise ValueError(
                "Linux mountinfo mount point must be absolute."
            )

        _require_string_tuple(
            self.mount_options,
            description="Linux mountinfo mount options",
        )
        _require_string_tuple(
            self.optional_fields,
            description="Linux mountinfo optional fields",
            allow_empty=True,
        )
        _require_string_tuple(
            self.super_options,
            description="Linux mountinfo super options",
        )

        access_modes = frozenset(
            self.mount_options
        ) & {"ro", "rw"}
        if len(access_modes) != 1:
            raise ValueError(
                "Linux mountinfo mount options must contain exactly "
                "one of 'ro' or 'rw'."
            )

        if not isinstance(self.filesystem_type, str):
            raise TypeError(
                "Linux mountinfo filesystem type must be a string."
            )
        if not self.filesystem_type:
            raise ValueError(
                "Linux mountinfo filesystem type must not be empty."
            )

        if not isinstance(self.mount_source, str):
            raise TypeError(
                "Linux mountinfo mount source must be a string."
            )
        if not self.mount_source:
            raise ValueError(
                "Linux mountinfo mount source must not be empty."
            )

    @property
    def device_number(self) -> tuple[int, int]:
        """Return the kernel major/minor device identity."""

        return (
            self.device_major,
            self.device_minor,
        )

    @property
    def is_read_only(self) -> bool:
        """Return whether mount or superblock evidence is read-only."""

        return (
            "ro" in self.mount_options
            or "ro" in self.super_options
        )

    @property
    def is_writable(self) -> bool:
        """Return whether both mount and superblock evidence permit writes."""

        return (
            "rw" in self.mount_options
            and "rw" in self.super_options
        )


def _decode_mountinfo_field(value: str) -> str:
    return _MOUNTINFO_ESCAPE_PATTERN.sub(
        lambda match: _MOUNTINFO_ESCAPES[
            match.group(1)
        ],
        value,
    )


def _parse_positive_decimal(
    value: str,
    *,
    description: str,
) -> int:
    if (
        not value
        or not value.isascii()
        or not value.isdecimal()
    ):
        raise ValueError(
            f"{description} must be a positive decimal integer."
        )
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(
            f"{description} must be positive."
        )
    return parsed


def _parse_nonnegative_decimal(
    value: str,
    *,
    description: str,
) -> int:
    if (
        not value
        or not value.isascii()
        or not value.isdecimal()
    ):
        raise ValueError(
            f"{description} must be a non-negative decimal integer."
        )
    return int(value)


def _parse_device_number(
    value: str,
) -> tuple[int, int]:
    major_text, separator, minor_text = value.partition(
        ":"
    )
    if not separator or ":" in minor_text:
        raise ValueError(
            "Linux mountinfo device number must use MAJOR:MINOR."
        )

    return (
        _parse_nonnegative_decimal(
            major_text,
            description="Linux mountinfo device major",
        ),
        _parse_nonnegative_decimal(
            minor_text,
            description="Linux mountinfo device minor",
        ),
    )


def _parse_options(
    value: str,
    *,
    description: str,
) -> tuple[str, ...]:
    options = tuple(
        value.split(",")
    )
    if not options or any(
        not option
        for option in options
    ):
        raise ValueError(
            f"{description} must contain non-empty comma-separated options."
        )
    return options


def _parse_mountinfo_line(
    line: str,
) -> LinuxMountInfoEntry:
    left, separator, right = line.partition(
        " - "
    )
    if not separator:
        raise ValueError(
            "Linux mountinfo record is missing the ' - ' separator."
        )

    left_fields = left.split()
    right_fields = right.split()

    if len(left_fields) < 6:
        raise ValueError(
            "Linux mountinfo record has fewer than six fields "
            "before the separator."
        )
    if len(right_fields) != 3:
        raise ValueError(
            "Linux mountinfo record must have exactly three fields "
            "after the separator."
        )

    mount_id = _parse_positive_decimal(
        left_fields[0],
        description="Linux mountinfo mount ID",
    )
    parent_id = _parse_positive_decimal(
        left_fields[1],
        description="Linux mountinfo parent ID",
    )
    device_major, device_minor = _parse_device_number(
        left_fields[2]
    )

    root = _decode_mountinfo_field(
        left_fields[3]
    )
    mount_point = Path(
        _decode_mountinfo_field(
            left_fields[4]
        )
    )
    mount_options = _parse_options(
        left_fields[5],
        description="Linux mountinfo mount options",
    )
    optional_fields = tuple(
        _decode_mountinfo_field(field)
        for field in left_fields[6:]
    )

    filesystem_type = _decode_mountinfo_field(
        right_fields[0]
    )
    mount_source = _decode_mountinfo_field(
        right_fields[1]
    )
    super_options = _parse_options(
        right_fields[2],
        description="Linux mountinfo super options",
    )

    return LinuxMountInfoEntry(
        mount_id=mount_id,
        parent_id=parent_id,
        device_major=device_major,
        device_minor=device_minor,
        root=root,
        mount_point=mount_point,
        mount_options=mount_options,
        optional_fields=optional_fields,
        filesystem_type=filesystem_type,
        mount_source=mount_source,
        super_options=super_options,
    )


def read_linux_mountinfo(
    path: Path = DEFAULT_LINUX_MOUNTINFO_PATH,
) -> tuple[LinuxMountInfoEntry, ...]:
    """Read immutable mount evidence from one Linux mount namespace."""

    if not isinstance(path, Path):
        raise TypeError(
            "Linux mountinfo path must be pathlib.Path."
        )

    try:
        raw = path.read_bytes()
    except OSError as error:
        raise LinuxMountInfoError(
            path,
            f"Could not read mountinfo: {error}",
        ) from error

    text = raw.decode(
        "utf-8",
        errors="surrogateescape",
    )

    if "\x00" in text:
        raise LinuxMountInfoError(
            path,
            "Mountinfo must not contain NUL bytes.",
        )

    entries: list[LinuxMountInfoEntry] = []
    mount_ids: set[int] = set()

    for line_number, line in enumerate(
        text.splitlines(),
        start=1,
    ):
        if not line:
            raise LinuxMountInfoError(
                path,
                "Mountinfo contains an empty record.",
                line_number=line_number,
            )

        try:
            entry = _parse_mountinfo_line(
                line
            )
        except (TypeError, ValueError) as error:
            raise LinuxMountInfoError(
                path,
                str(error),
                line_number=line_number,
            ) from error

        if entry.mount_id in mount_ids:
            raise LinuxMountInfoError(
                path,
                (
                    "Mountinfo contains duplicate mount ID "
                    f"{entry.mount_id}."
                ),
                line_number=line_number,
            )

        mount_ids.add(
            entry.mount_id
        )
        entries.append(
            entry
        )

    return tuple(entries)


DEFAULT_LINUX_SYS_DEV_BLOCK_DIRECTORY = Path("/sys/dev/block")


class LinuxBlockDeviceError(RuntimeError):
    """Report unsafe or unavailable Linux block-device evidence."""

    def __init__(
        self,
        path: Path,
        message: str,
    ) -> None:
        self.path = path
        self.message = message
        super().__init__(
            f"Linux block-device error at {path}: {message}"
        )


@dataclass(frozen=True, slots=True)
class LinuxBlockDeviceEvidence:
    """Immutable sysfs evidence for one mounted block-device number."""

    device_major: int
    device_minor: int
    sysfs_path: Path
    device_name: str
    usb_ancestor_path: Path | None
    removable: bool | None

    def __post_init__(self) -> None:
        for name, value in (
            ("device major", self.device_major),
            ("device minor", self.device_minor),
        ):
            if type(value) is not int:
                raise TypeError(
                    f"Linux block-device {name} must be an integer."
                )
            if value < 0:
                raise ValueError(
                    f"Linux block-device {name} must be non-negative."
                )

        if not isinstance(
            self.sysfs_path,
            Path,
        ):
            raise TypeError(
                "Linux block-device sysfs path must be pathlib.Path."
            )
        if not self.sysfs_path.is_absolute():
            raise ValueError(
                "Linux block-device sysfs path must be absolute."
            )

        if not isinstance(
            self.device_name,
            str,
        ):
            raise TypeError(
                "Linux block-device name must be a string."
            )
        if not self.device_name:
            raise ValueError(
                "Linux block-device name must not be empty."
            )

        if self.usb_ancestor_path is not None:
            if not isinstance(
                self.usb_ancestor_path,
                Path,
            ):
                raise TypeError(
                    "Linux block-device USB ancestor must be "
                    "pathlib.Path or None."
                )
            if not self.usb_ancestor_path.is_absolute():
                raise ValueError(
                    "Linux block-device USB ancestor must be absolute."
                )

        if (
            self.removable is not None
            and type(self.removable) is not bool
        ):
            raise TypeError(
                "Linux block-device removable state must be bool or None."
            )

    @property
    def device_number(self) -> tuple[int, int]:
        """Return the kernel major/minor device identity."""

        return (
            self.device_major,
            self.device_minor,
        )

    @property
    def is_usb(self) -> bool:
        """Return whether sysfs proves USB ancestry."""

        return self.usb_ancestor_path is not None


def _is_relative_to(
    path: Path,
    parent: Path,
) -> bool:
    try:
        path.relative_to(
            parent
        )
    except ValueError:
        return False
    return True


def _sysfs_root(
    sys_dev_block_directory: Path,
) -> Path:
    try:
        resolved_directory = sys_dev_block_directory.resolve(
            strict=True
        )
    except (OSError, RuntimeError) as error:
        raise LinuxBlockDeviceError(
            sys_dev_block_directory,
            f"Could not resolve sysfs block-device directory: {error}",
        ) from error

    if not resolved_directory.is_dir():
        raise LinuxBlockDeviceError(
            sys_dev_block_directory,
            "Sysfs block-device path must be a directory.",
        )

    if (
        resolved_directory.name != "block"
        or resolved_directory.parent.name != "dev"
    ):
        raise LinuxBlockDeviceError(
            sys_dev_block_directory,
            "Sysfs block-device directory must have a dev/block suffix.",
        )

    return resolved_directory.parent.parent


def _subsystem_name(
    directory: Path,
    *,
    sysfs_root: Path,
) -> str | None:
    subsystem = directory / "subsystem"

    try:
        status = subsystem.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise LinuxBlockDeviceError(
            subsystem,
            f"Could not inspect sysfs subsystem link: {error}",
        ) from error

    if not stat.S_ISLNK(
        status.st_mode
    ):
        raise LinuxBlockDeviceError(
            subsystem,
            "Sysfs subsystem entry must be a symbolic link.",
        )

    try:
        resolved = subsystem.resolve(
            strict=True
        )
    except (OSError, RuntimeError) as error:
        raise LinuxBlockDeviceError(
            subsystem,
            f"Could not resolve sysfs subsystem link: {error}",
        ) from error

    if not _is_relative_to(
        resolved,
        sysfs_root,
    ):
        raise LinuxBlockDeviceError(
            subsystem,
            "Sysfs subsystem link resolves outside the sysfs root.",
        )

    return resolved.name


def _read_removable_state(
    directories: tuple[Path, ...],
) -> bool | None:
    for directory in directories:
        path = directory / "removable"

        try:
            status = path.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise LinuxBlockDeviceError(
                path,
                f"Could not inspect removable state: {error}",
            ) from error

        if stat.S_ISLNK(
            status.st_mode
        ):
            raise LinuxBlockDeviceError(
                path,
                "Sysfs removable state must not be a symbolic link.",
            )

        if not stat.S_ISREG(
            status.st_mode
        ):
            raise LinuxBlockDeviceError(
                path,
                "Sysfs removable state must be a regular file.",
            )

        try:
            value = path.read_text(
                encoding="ascii"
            ).strip()
        except (OSError, UnicodeError) as error:
            raise LinuxBlockDeviceError(
                path,
                f"Could not read removable state: {error}",
            ) from error

        if value == "0":
            return False
        if value == "1":
            return True

        raise LinuxBlockDeviceError(
            path,
            "Sysfs removable state must contain exactly 0 or 1.",
        )

    return None


def read_linux_block_device_evidence(
    device_major: int,
    device_minor: int,
    *,
    sys_dev_block_directory: Path = DEFAULT_LINUX_SYS_DEV_BLOCK_DIRECTORY,
) -> LinuxBlockDeviceEvidence:
    """Resolve one Linux major/minor number through read-only sysfs evidence."""

    for name, value in (
        ("device major", device_major),
        ("device minor", device_minor),
    ):
        if type(value) is not int:
            raise TypeError(
                f"Linux block-device {name} must be an integer."
            )
        if value < 0:
            raise ValueError(
                f"Linux block-device {name} must be non-negative."
            )

    if not isinstance(
        sys_dev_block_directory,
        Path,
    ):
        raise TypeError(
            "Linux sysfs block-device directory must be pathlib.Path."
        )
    if not sys_dev_block_directory.is_absolute():
        raise ValueError(
            "Linux sysfs block-device directory must be absolute."
        )

    sysfs_root = _sysfs_root(
        sys_dev_block_directory
    )
    device_link = (
        sys_dev_block_directory
        / f"{device_major}:{device_minor}"
    )

    try:
        initial = device_link.lstat()
    except FileNotFoundError as error:
        raise LinuxBlockDeviceError(
            device_link,
            "No sysfs block-device mapping exists for this device number.",
        ) from error
    except OSError as error:
        raise LinuxBlockDeviceError(
            device_link,
            f"Could not inspect sysfs block-device mapping: {error}",
        ) from error

    if not stat.S_ISLNK(
        initial.st_mode
    ):
        raise LinuxBlockDeviceError(
            device_link,
            "Sysfs block-device mapping must be a symbolic link.",
        )

    try:
        resolved = device_link.resolve(
            strict=True
        )
    except (OSError, RuntimeError) as error:
        raise LinuxBlockDeviceError(
            device_link,
            f"Could not resolve sysfs block-device mapping: {error}",
        ) from error

    if not _is_relative_to(
        resolved,
        sysfs_root,
    ):
        raise LinuxBlockDeviceError(
            device_link,
            "Sysfs block-device mapping resolves outside the sysfs root.",
        )

    try:
        resolved_status = resolved.stat()
    except OSError as error:
        raise LinuxBlockDeviceError(
            resolved,
            f"Could not inspect resolved sysfs block device: {error}",
        ) from error

    if not stat.S_ISDIR(
        resolved_status.st_mode
    ):
        raise LinuxBlockDeviceError(
            resolved,
            "Resolved sysfs block-device target must be a directory.",
        )

    try:
        final = device_link.lstat()
    except OSError as error:
        raise LinuxBlockDeviceError(
            device_link,
            f"Could not re-inspect sysfs block-device mapping: {error}",
        ) from error

    if (
        final.st_dev,
        final.st_ino,
        final.st_mode,
        final.st_size,
        final.st_mtime_ns,
    ) != (
        initial.st_dev,
        initial.st_ino,
        initial.st_mode,
        initial.st_size,
        initial.st_mtime_ns,
    ):
        raise LinuxBlockDeviceError(
            device_link,
            "Sysfs block-device mapping changed while being resolved.",
        )

    directories: list[Path] = []
    current = resolved

    while True:
        directories.append(
            current
        )
        if current == sysfs_root:
            break
        if not _is_relative_to(
            current,
            sysfs_root,
        ):
            raise LinuxBlockDeviceError(
                resolved,
                "Resolved sysfs ancestry escaped the sysfs root.",
            )
        parent = current.parent
        if parent == current:
            raise LinuxBlockDeviceError(
                resolved,
                "Resolved sysfs ancestry did not reach the sysfs root.",
            )
        current = parent

    ancestry = tuple(
        directories
    )

    usb_ancestor: Path | None = None
    for directory in ancestry:
        if (
            _subsystem_name(
                directory,
                sysfs_root=sysfs_root,
            )
            == "usb"
        ):
            usb_ancestor = directory
            break

    removable = _read_removable_state(
        ancestry
    )

    return LinuxBlockDeviceEvidence(
        device_major=device_major,
        device_minor=device_minor,
        sysfs_path=resolved,
        device_name=resolved.name,
        usb_ancestor_path=usb_ancestor,
        removable=removable,
    )


FAVORITES_USB_STORAGE_RELATIVE_DIRECTORY = (
    Path("BCDx36HP") / "favorites_lists"
)


class FavoritesUsbStorageQualificationReason(StrEnum):
    """Deterministic reason that an observed USB mount is not a safe target."""

    AMBIGUOUS_TARGET = "ambiguous_target"
    DEVICE_IDENTITY_MISMATCH = "device_identity_mismatch"
    NOT_USB = "not_usb"
    READ_ONLY_MOUNT = "read_only_mount"
    STALE_DEVICE = "stale_device"
    STALE_MOUNT = "stale_mount"
    TARGET_UNAVAILABLE = "target_unavailable"
    TARGET_UNSAFE = "target_unsafe"
    TARGET_DEVICE_MISMATCH = "target_device_mismatch"
    TARGET_STORAGE_INVALID = "target_storage_invalid"


class FavoritesUsbStorageQualificationError(RuntimeError):
    """Report why a mounted USB candidate cannot be qualified safely."""

    def __init__(
        self,
        reason: FavoritesUsbStorageQualificationReason,
        path: Path,
        message: str,
    ) -> None:
        if not isinstance(
            reason,
            FavoritesUsbStorageQualificationReason,
        ):
            raise TypeError(
                "USB storage qualification reason must be "
                "FavoritesUsbStorageQualificationReason."
            )
        if not isinstance(
            path,
            Path,
        ):
            raise TypeError(
                "USB storage qualification error path must be pathlib.Path."
            )
        if not isinstance(
            message,
            str,
        ):
            raise TypeError(
                "USB storage qualification message must be a string."
            )
        if not message:
            raise ValueError(
                "USB storage qualification message must not be empty."
            )

        self.reason = reason
        self.path = path
        self.message = message

        super().__init__(
            "Favorites USB storage qualification failed "
            f"({reason.value}) at {path}: {message}"
        )


@dataclass(frozen=True, slots=True)
class FavoritesUsbStorageQualification:
    """Read-only evidence that one mounted USB Favorites target is safe to use."""

    mount: LinuxMountInfoEntry
    block_device: LinuxBlockDeviceEvidence
    mount_directory: Path
    favorites_directory: Path
    snapshot: FavoritesStorageSnapshot

    def __post_init__(self) -> None:
        if not isinstance(
            self.mount,
            LinuxMountInfoEntry,
        ):
            raise TypeError(
                "USB storage qualification mount must be LinuxMountInfoEntry."
            )
        if not isinstance(
            self.block_device,
            LinuxBlockDeviceEvidence,
        ):
            raise TypeError(
                "USB storage qualification block device must be "
                "LinuxBlockDeviceEvidence."
            )

        for name, path_value in (
            ("mount directory", self.mount_directory),
            ("Favorites directory", self.favorites_directory),
        ):
            if not isinstance(
                path_value,
                Path,
            ):
                raise TypeError(
                    f"USB storage qualification {name} must be pathlib.Path."
                )
            if not path_value.is_absolute():
                raise ValueError(
                    f"USB storage qualification {name} must be absolute."
                )

        if not isinstance(
            self.snapshot,
            FavoritesStorageSnapshot,
        ):
            raise TypeError(
                "USB storage qualification snapshot must be "
                "FavoritesStorageSnapshot."
            )

        if (
            self.mount.device_number
            != self.block_device.device_number
        ):
            raise ValueError(
                "USB storage qualification mount and block-device "
                "identities must match."
            )
        if not self.block_device.is_usb:
            raise ValueError(
                "USB storage qualification requires proven USB ancestry."
            )
        if not self.mount.is_writable:
            raise ValueError(
                "USB storage qualification requires a writable mount."
            )
        try:
            canonical_mount = self.mount.mount_point.resolve(
                strict=True
            )
        except (OSError, RuntimeError) as error:
            raise ValueError(
                "USB storage qualification mount evidence must resolve."
            ) from error

        if self.mount_directory != canonical_mount:
            raise ValueError(
                "USB storage qualification mount directory must be "
                "the canonical mountinfo mount path."
            )

        expected = (
            self.mount_directory
            / FAVORITES_USB_STORAGE_RELATIVE_DIRECTORY
        )
        try:
            canonical_favorites = expected.resolve(
                strict=True
            )
        except (OSError, RuntimeError) as error:
            raise ValueError(
                "USB storage qualification Favorites directory must resolve."
            ) from error

        if self.favorites_directory != canonical_favorites:
            raise ValueError(
                "USB storage qualification Favorites directory must be "
                "the canonical BCDx36HP/favorites_lists path."
            )


def _usb_qualification_directory_status(
    path: Path,
    *,
    description: str,
) -> os.stat_result:
    try:
        status = path.lstat()
    except FileNotFoundError as error:
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.TARGET_UNAVAILABLE,
            path,
            f"{description} does not exist.",
        ) from error
    except OSError as error:
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.TARGET_UNSAFE,
            path,
            f"Could not inspect {description}: {error}",
        ) from error

    if stat.S_ISLNK(
        status.st_mode
    ):
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.TARGET_UNSAFE,
            path,
            f"{description} must not be a symbolic link.",
        )

    if not stat.S_ISDIR(
        status.st_mode
    ):
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.TARGET_UNSAFE,
            path,
            f"{description} must be a directory.",
        )

    return status


def _filesystem_device_number(
    status: os.stat_result,
) -> tuple[int, int]:
    return (
        os.major(status.st_dev),
        os.minor(status.st_dev),
    )


def _qualify_favorites_usb_storage_target_evidence(
    mount: LinuxMountInfoEntry,
    block_device: LinuxBlockDeviceEvidence,
) -> FavoritesUsbStorageQualification:
    """Qualify one already-mounted USB Favorites tree without mutating storage."""

    if not isinstance(
        mount,
        LinuxMountInfoEntry,
    ):
        raise TypeError(
            "USB storage qualification mount must be LinuxMountInfoEntry."
        )
    if not isinstance(
        block_device,
        LinuxBlockDeviceEvidence,
    ):
        raise TypeError(
            "USB storage qualification block device must be "
            "LinuxBlockDeviceEvidence."
        )

    if (
        mount.device_number
        != block_device.device_number
    ):
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.DEVICE_IDENTITY_MISMATCH,
            mount.mount_point,
            (
                "Mountinfo device identity "
                f"{mount.device_major}:{mount.device_minor} "
                "does not match block-device evidence "
                f"{block_device.device_major}:{block_device.device_minor}."
            ),
        )

    if not block_device.is_usb:
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.NOT_USB,
            mount.mount_point,
            "Block-device evidence does not prove USB ancestry.",
        )

    if not mount.is_writable:
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.READ_ONLY_MOUNT,
            mount.mount_point,
            "Mounted scanner storage is read-only.",
        )

    mount_status = _usb_qualification_directory_status(
        mount.mount_point,
        description="mounted scanner storage root",
    )

    if (
        _filesystem_device_number(
            mount_status
        )
        != mount.device_number
    ):
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.TARGET_DEVICE_MISMATCH,
            mount.mount_point,
            (
                "Mounted scanner storage filesystem device no longer "
                "matches mountinfo evidence."
            ),
        )

    try:
        resolved_mount = mount.mount_point.resolve(
            strict=True
        )
    except (OSError, RuntimeError) as error:
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.TARGET_UNSAFE,
            mount.mount_point,
            f"Could not resolve mounted scanner storage root: {error}",
        ) from error

    bcd_directory = (
        mount.mount_point
        / "BCDx36HP"
    )
    _usb_qualification_directory_status(
        bcd_directory,
        description="BCDx36HP scanner storage directory",
    )

    favorites_directory = (
        bcd_directory
        / "favorites_lists"
    )
    favorites_status = _usb_qualification_directory_status(
        favorites_directory,
        description="Favorites storage directory",
    )

    try:
        resolved_favorites = favorites_directory.resolve(
            strict=True
        )
    except (OSError, RuntimeError) as error:
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.TARGET_UNSAFE,
            favorites_directory,
            f"Could not resolve Favorites storage directory: {error}",
        ) from error

    try:
        resolved_favorites.relative_to(
            resolved_mount
        )
    except ValueError as error:
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.TARGET_UNSAFE,
            favorites_directory,
            "Favorites storage directory resolves outside its mounted volume.",
        ) from error

    if (
        _filesystem_device_number(
            favorites_status
        )
        != mount.device_number
    ):
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.TARGET_DEVICE_MISMATCH,
            favorites_directory,
            (
                "Favorites storage filesystem device does not match "
                "the mounted USB device."
            ),
        )

    try:
        snapshot = FavoritesCopiedTreeStorageSource(
            resolved_favorites
        ).read_snapshot()
    except FavoritesCopiedTreeStorageError as error:
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.TARGET_STORAGE_INVALID,
            error.path,
            str(error),
        ) from error

    final_mount_status = _usb_qualification_directory_status(
        mount.mount_point,
        description="mounted scanner storage root",
    )
    final_favorites_status = _usb_qualification_directory_status(
        resolved_favorites,
        description="Favorites storage directory",
    )

    if (
        final_mount_status.st_dev,
        final_mount_status.st_ino,
        final_mount_status.st_mode,
    ) != (
        mount_status.st_dev,
        mount_status.st_ino,
        mount_status.st_mode,
    ):
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.TARGET_UNSAFE,
            mount.mount_point,
            "Mounted scanner storage root changed during qualification.",
        )

    if (
        final_favorites_status.st_dev,
        final_favorites_status.st_ino,
        final_favorites_status.st_mode,
    ) != (
        favorites_status.st_dev,
        favorites_status.st_ino,
        favorites_status.st_mode,
    ):
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.TARGET_UNSAFE,
            favorites_directory,
            "Favorites storage directory changed during qualification.",
        )

    return FavoritesUsbStorageQualification(
        mount=mount,
        block_device=block_device,
        mount_directory=resolved_mount,
        favorites_directory=resolved_favorites,
        snapshot=snapshot,
    )


@dataclass(frozen=True, slots=True)
class FavoritesUsbStorageCandidate:
    """Read-only observation of one mounted USB scanner Favorites tree."""

    mount: LinuxMountInfoEntry
    block_device: LinuxBlockDeviceEvidence
    mount_directory: Path
    favorites_directory: Path
    snapshot: FavoritesStorageSnapshot

    def __post_init__(self) -> None:
        if not isinstance(
            self.mount,
            LinuxMountInfoEntry,
        ):
            raise TypeError(
                "USB storage candidate mount must be LinuxMountInfoEntry."
            )
        if not isinstance(
            self.block_device,
            LinuxBlockDeviceEvidence,
        ):
            raise TypeError(
                "USB storage candidate block device must be "
                "LinuxBlockDeviceEvidence."
            )

        for name, path_value in (
            ("mount directory", self.mount_directory),
            ("Favorites directory", self.favorites_directory),
        ):
            if not isinstance(
                path_value,
                Path,
            ):
                raise TypeError(
                    f"USB storage candidate {name} must be pathlib.Path."
                )
            if not path_value.is_absolute():
                raise ValueError(
                    f"USB storage candidate {name} must be absolute."
                )

        if not isinstance(
            self.snapshot,
            FavoritesStorageSnapshot,
        ):
            raise TypeError(
                "USB storage candidate snapshot must be FavoritesStorageSnapshot."
            )

        if (
            self.mount.device_number
            != self.block_device.device_number
        ):
            raise ValueError(
                "USB storage candidate mount and block-device "
                "identities must match."
            )
        if not self.block_device.is_usb:
            raise ValueError(
                "USB storage candidate requires proven USB ancestry."
            )
        try:
            canonical_mount = self.mount.mount_point.resolve(
                strict=True
            )
        except (OSError, RuntimeError) as error:
            raise ValueError(
                "USB storage candidate mount evidence must resolve."
            ) from error

        if self.mount_directory != canonical_mount:
            raise ValueError(
                "USB storage candidate mount directory must be "
                "the canonical mountinfo mount path."
            )

        expected = (
            self.mount_directory
            / FAVORITES_USB_STORAGE_RELATIVE_DIRECTORY
        )
        try:
            canonical_favorites = expected.resolve(
                strict=True
            )
        except (OSError, RuntimeError) as error:
            raise ValueError(
                "USB storage candidate Favorites directory must resolve."
            ) from error

        if self.favorites_directory != canonical_favorites:
            raise ValueError(
                "USB storage candidate Favorites directory must be "
                "the canonical BCDx36HP/favorites_lists path."
            )

    @property
    def is_read_only(self) -> bool:
        """Return whether this discovered scanner mount is read-only."""

        return self.mount.is_read_only

    @property
    def is_writable(self) -> bool:
        """Return whether this discovered scanner mount is writable."""

        return self.mount.is_writable


def _observe_favorites_usb_storage_candidate(
    mount: LinuxMountInfoEntry,
    block_device: LinuxBlockDeviceEvidence,
) -> FavoritesUsbStorageCandidate:
    if (
        mount.device_number
        != block_device.device_number
    ):
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.DEVICE_IDENTITY_MISMATCH,
            mount.mount_point,
            "Mountinfo and block-device identities do not match.",
        )

    if not block_device.is_usb:
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.NOT_USB,
            mount.mount_point,
            "Block-device evidence does not prove USB ancestry.",
        )

    mount_status = _usb_qualification_directory_status(
        mount.mount_point,
        description="mounted scanner storage root",
    )

    if (
        _filesystem_device_number(
            mount_status
        )
        != mount.device_number
    ):
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.TARGET_DEVICE_MISMATCH,
            mount.mount_point,
            (
                "Mounted scanner storage filesystem device no longer "
                "matches mountinfo evidence."
            ),
        )

    try:
        resolved_mount = mount.mount_point.resolve(
            strict=True
        )
    except (OSError, RuntimeError) as error:
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.TARGET_UNSAFE,
            mount.mount_point,
            f"Could not resolve mounted scanner storage root: {error}",
        ) from error

    bcd_directory = (
        mount.mount_point
        / "BCDx36HP"
    )
    _usb_qualification_directory_status(
        bcd_directory,
        description="BCDx36HP scanner storage directory",
    )

    favorites_directory = (
        bcd_directory
        / "favorites_lists"
    )
    favorites_status = _usb_qualification_directory_status(
        favorites_directory,
        description="Favorites storage directory",
    )

    try:
        resolved_favorites = favorites_directory.resolve(
            strict=True
        )
    except (OSError, RuntimeError) as error:
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.TARGET_UNSAFE,
            favorites_directory,
            f"Could not resolve Favorites storage directory: {error}",
        ) from error

    try:
        resolved_favorites.relative_to(
            resolved_mount
        )
    except ValueError as error:
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.TARGET_UNSAFE,
            favorites_directory,
            "Favorites storage directory resolves outside its mounted volume.",
        ) from error

    if (
        _filesystem_device_number(
            favorites_status
        )
        != mount.device_number
    ):
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.TARGET_DEVICE_MISMATCH,
            favorites_directory,
            (
                "Favorites storage filesystem device does not match "
                "the mounted USB device."
            ),
        )

    try:
        snapshot = FavoritesCopiedTreeStorageSource(
            resolved_favorites
        ).read_snapshot()
    except FavoritesCopiedTreeStorageError as error:
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.TARGET_STORAGE_INVALID,
            error.path,
            str(error),
        ) from error

    final_mount_status = _usb_qualification_directory_status(
        mount.mount_point,
        description="mounted scanner storage root",
    )
    final_favorites_status = _usb_qualification_directory_status(
        resolved_favorites,
        description="Favorites storage directory",
    )

    if (
        final_mount_status.st_dev,
        final_mount_status.st_ino,
        final_mount_status.st_mode,
    ) != (
        mount_status.st_dev,
        mount_status.st_ino,
        mount_status.st_mode,
    ):
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.TARGET_UNSAFE,
            mount.mount_point,
            "Mounted scanner storage root changed during discovery.",
        )

    if (
        final_favorites_status.st_dev,
        final_favorites_status.st_ino,
        final_favorites_status.st_mode,
    ) != (
        favorites_status.st_dev,
        favorites_status.st_ino,
        favorites_status.st_mode,
    ):
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.TARGET_UNSAFE,
            favorites_directory,
            "Favorites storage directory changed during discovery.",
        )

    return FavoritesUsbStorageCandidate(
        mount=mount,
        block_device=block_device,
        mount_directory=resolved_mount,
        favorites_directory=resolved_favorites,
        snapshot=snapshot,
    )


def discover_favorites_usb_storage_candidates(
    mountinfo_path: Path = DEFAULT_LINUX_MOUNTINFO_PATH,
    *,
    sys_dev_block_directory: Path = DEFAULT_LINUX_SYS_DEV_BLOCK_DIRECTORY,
) -> tuple[FavoritesUsbStorageCandidate, ...]:
    """Discover readable Favorites trees on already-mounted Linux USB storage."""

    if not isinstance(
        mountinfo_path,
        Path,
    ):
        raise TypeError(
            "USB storage discovery mountinfo path must be pathlib.Path."
        )
    if not isinstance(
        sys_dev_block_directory,
        Path,
    ):
        raise TypeError(
            "USB storage discovery sysfs block directory must be pathlib.Path."
        )

    mounts = read_linux_mountinfo(
        mountinfo_path
    )
    candidates: list[
        FavoritesUsbStorageCandidate
    ] = []

    for mount in mounts:
        mapping = (
            sys_dev_block_directory
            / f"{mount.device_major}:{mount.device_minor}"
        )

        if not os.path.lexists(
            mapping
        ):
            continue

        block_device = read_linux_block_device_evidence(
            mount.device_major,
            mount.device_minor,
            sys_dev_block_directory=sys_dev_block_directory,
        )

        if not block_device.is_usb:
            continue

        bcd_directory = (
            mount.mount_point
            / "BCDx36HP"
        )

        if not os.path.lexists(
            bcd_directory
        ):
            continue

        candidate = _observe_favorites_usb_storage_candidate(
            mount,
            block_device,
        )

        _require_current_mount_evidence(
            mount,
            mountinfo_path,
        )
        _require_current_block_device_evidence(
            block_device,
            sys_dev_block_directory,
        )

        candidates.append(
            candidate
        )

    _reject_ambiguous_discovered_candidates(
        candidates
    )

    for candidate in candidates:
        _require_current_mount_evidence(
            candidate.mount,
            mountinfo_path,
        )
        _require_current_block_device_evidence(
            candidate.block_device,
            sys_dev_block_directory,
        )

    return tuple(
        sorted(
            candidates,
            key=lambda candidate: (
                str(
                    candidate.mount_directory
                ),
                candidate.mount.mount_id,
            ),
        )
    )


def _require_current_mount_evidence(
    mount: LinuxMountInfoEntry,
    mountinfo_path: Path,
) -> None:
    try:
        current_entries = read_linux_mountinfo(
            mountinfo_path
        )
    except LinuxMountInfoError as error:
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.STALE_MOUNT,
            mount.mount_point,
            f"Could not re-read current mount evidence: {error}",
        ) from error

    current = next(
        (
            entry
            for entry in current_entries
            if entry.mount_id == mount.mount_id
        ),
        None,
    )

    if current is None:
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.STALE_MOUNT,
            mount.mount_point,
            (
                f"Mount ID {mount.mount_id} is no longer present "
                "in the current mount namespace."
            ),
        )

    if current != mount:
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.STALE_MOUNT,
            mount.mount_point,
            (
                f"Mount ID {mount.mount_id} changed after its "
                "evidence was captured."
            ),
        )


def _require_current_block_device_evidence(
    block_device: LinuxBlockDeviceEvidence,
    sys_dev_block_directory: Path,
) -> None:
    try:
        current = read_linux_block_device_evidence(
            block_device.device_major,
            block_device.device_minor,
            sys_dev_block_directory=sys_dev_block_directory,
        )
    except LinuxBlockDeviceError as error:
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.STALE_DEVICE,
            block_device.sysfs_path,
            f"Could not re-read current block-device evidence: {error}",
        ) from error

    if current != block_device:
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.STALE_DEVICE,
            block_device.sysfs_path,
            (
                "Linux block-device evidence changed after "
                "the candidate was captured."
            ),
        )


def _reject_ambiguous_discovered_candidates(
    candidates: list[FavoritesUsbStorageCandidate],
) -> None:
    by_target: dict[
        Path,
        FavoritesUsbStorageCandidate,
    ] = {}

    for candidate in candidates:
        previous = by_target.get(
            candidate.favorites_directory
        )
        if previous is not None:
            raise FavoritesUsbStorageQualificationError(
                FavoritesUsbStorageQualificationReason.AMBIGUOUS_TARGET,
                candidate.favorites_directory,
                (
                    "Multiple current mountinfo entries identify "
                    "the same Favorites target."
                ),
            )
        by_target[
            candidate.favorites_directory
        ] = candidate


def qualify_favorites_usb_storage_path(
    path: Path,
    mountinfo_path: Path = DEFAULT_LINUX_MOUNTINFO_PATH,
    *,
    sys_dev_block_directory: Path = DEFAULT_LINUX_SYS_DEV_BLOCK_DIRECTORY,
) -> FavoritesUsbStorageQualification:
    """Qualify one explicit mounted scanner path through current Linux evidence."""

    if not isinstance(
        path,
        Path,
    ):
        raise TypeError(
            "Explicit USB storage path must be pathlib.Path."
        )
    if not path.is_absolute():
        raise ValueError(
            "Explicit USB storage path must be absolute."
        )
    if not isinstance(
        mountinfo_path,
        Path,
    ):
        raise TypeError(
            "USB storage qualification mountinfo path must be pathlib.Path."
        )
    if not isinstance(
        sys_dev_block_directory,
        Path,
    ):
        raise TypeError(
            "USB storage qualification sysfs block directory must be pathlib.Path."
        )

    try:
        explicit_status = path.lstat()
    except FileNotFoundError as error:
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.TARGET_UNAVAILABLE,
            path,
            "Explicit USB storage path does not exist.",
        ) from error
    except OSError as error:
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.TARGET_UNSAFE,
            path,
            f"Could not inspect explicit USB storage path: {error}",
        ) from error

    if stat.S_ISLNK(
        explicit_status.st_mode
    ):
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.TARGET_UNSAFE,
            path,
            "Explicit USB storage path must not be a symbolic link.",
        )

    if not stat.S_ISDIR(
        explicit_status.st_mode
    ):
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.TARGET_UNSAFE,
            path,
            "Explicit USB storage path must be a directory.",
        )

    try:
        resolved_path = path.resolve(
            strict=True
        )
    except (OSError, RuntimeError) as error:
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.TARGET_UNSAFE,
            path,
            f"Could not resolve explicit USB storage path: {error}",
        ) from error

    matching_mounts: list[
        LinuxMountInfoEntry
    ] = []

    for mount in read_linux_mountinfo(
        mountinfo_path
    ):
        try:
            mount_root = mount.mount_point.resolve(
                strict=True
            )
        except (OSError, RuntimeError):
            continue

        favorites_path = (
            mount_root
            / FAVORITES_USB_STORAGE_RELATIVE_DIRECTORY
        )
        try:
            resolved_favorites_path = favorites_path.resolve(
                strict=True
            )
        except (OSError, RuntimeError):
            resolved_favorites_path = favorites_path

        if resolved_path in (
            mount_root,
            resolved_favorites_path,
        ):
            matching_mounts.append(
                mount
            )

    if not matching_mounts:
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.TARGET_UNAVAILABLE,
            path,
            (
                "Explicit USB storage path does not exactly match a current "
                "mount point or its BCDx36HP/favorites_lists directory."
            ),
        )

    if len(matching_mounts) != 1:
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.AMBIGUOUS_TARGET,
            path,
            (
                "Explicit USB storage path matches multiple current "
                "mountinfo entries."
            ),
        )

    mount = matching_mounts[0]

    try:
        block_device = read_linux_block_device_evidence(
            mount.device_major,
            mount.device_minor,
            sys_dev_block_directory=sys_dev_block_directory,
        )
    except LinuxBlockDeviceError as error:
        raise FavoritesUsbStorageQualificationError(
            FavoritesUsbStorageQualificationReason.NOT_USB,
            path,
            (
                "Explicit mount does not have safe Linux block-device "
                f"evidence: {error}"
            ),
        ) from error

    qualification = _qualify_favorites_usb_storage_target_evidence(
        mount,
        block_device,
    )

    _require_current_mount_evidence(
        mount,
        mountinfo_path,
    )
    _require_current_block_device_evidence(
        block_device,
        sys_dev_block_directory,
    )

    return qualification


__all__ = [
    "DEFAULT_LINUX_MOUNTINFO_PATH",
    "DEFAULT_LINUX_SYS_DEV_BLOCK_DIRECTORY",
    "LinuxBlockDeviceEvidence",
    "LinuxBlockDeviceError",
    "LinuxMountInfoEntry",
    "LinuxMountInfoError",
    "read_linux_block_device_evidence",
    "read_linux_mountinfo",
    "FAVORITES_USB_STORAGE_RELATIVE_DIRECTORY",
    "FavoritesUsbStorageQualification",
    "FavoritesUsbStorageQualificationError",
    "FavoritesUsbStorageQualificationReason",
    "FavoritesUsbStorageCandidate",
    "discover_favorites_usb_storage_candidates",
    "qualify_favorites_usb_storage_path",
]
