"""Renderer-neutral hierarchy projection over lossless Favorites records."""

from __future__ import annotations

from dataclasses import dataclass, field

from .favorites_file import FavoritesSourceFile, FavoritesSourceRecord

_SYSTEM_SUPPLEMENTAL_COMMANDS = frozenset(
    {
        "AvoidTgids",
        "FleetMap",
        "UnitID",
        "UnitIds",
    }
)


class FavoritesHierarchyError(ValueError):
    """Report a known structural record in an impossible hierarchy context."""

    def __init__(
        self,
        source_index: int,
        command: str,
        message: str,
    ) -> None:
        self.source_index = source_index
        self.command = command
        super().__init__(
            f"Favorites record {source_index + 1} "
            f"({command}): {message}"
        )


@dataclass(frozen=True, slots=True)
class FavoritesRecordReference:
    """Reference one record by its immutable source-file position."""

    source_index: int
    record: FavoritesSourceRecord

    def __post_init__(self) -> None:
        if self.source_index < 0:
            raise ValueError(
                "Favorites source index must be non-negative."
            )
        if not isinstance(self.record, FavoritesSourceRecord):
            raise TypeError(
                "Favorites record reference requires "
                "FavoritesSourceRecord."
            )


def _record_name(
    source: FavoritesRecordReference,
) -> str | None:
    fields = source.record.fields

    if len(fields) < 3:
        return None

    return fields[2]


@dataclass(frozen=True, slots=True)
class FavoritesConventionalChannel:
    """One projected conventional C-Freq channel."""

    source: FavoritesRecordReference

    @property
    def name(self) -> str | None:
        return _record_name(self.source)


@dataclass(frozen=True, slots=True)
class FavoritesConventionalDepartment:
    """One projected conventional C-Group and its ordered channels."""

    source: FavoritesRecordReference
    channels: tuple[FavoritesConventionalChannel, ...]

    @property
    def name(self) -> str | None:
        return _record_name(self.source)


@dataclass(frozen=True, slots=True)
class FavoritesConventionalSystem:
    """One projected Conventional system."""

    source: FavoritesRecordReference
    quick_key_status_records: tuple[FavoritesRecordReference, ...]
    departments: tuple[FavoritesConventionalDepartment, ...]
    supplemental_records: tuple[FavoritesRecordReference, ...]

    @property
    def name(self) -> str | None:
        return _record_name(self.source)


@dataclass(frozen=True, slots=True)
class FavoritesTrunkChannel:
    """One projected trunked TGID channel."""

    source: FavoritesRecordReference

    @property
    def name(self) -> str | None:
        return _record_name(self.source)


@dataclass(frozen=True, slots=True)
class FavoritesTrunkDepartment:
    """One projected T-Group and its ordered TGIDs."""

    source: FavoritesRecordReference
    channels: tuple[FavoritesTrunkChannel, ...]

    @property
    def name(self) -> str | None:
        return _record_name(self.source)


@dataclass(frozen=True, slots=True)
class FavoritesTrunkSite:
    """One projected trunked Site and its ordered frequency data."""

    source: FavoritesRecordReference
    frequencies: tuple[FavoritesRecordReference, ...]
    band_plans: tuple[FavoritesRecordReference, ...]

    @property
    def name(self) -> str | None:
        return _record_name(self.source)


@dataclass(frozen=True, slots=True)
class FavoritesTrunkSystem:
    """One projected Trunk system."""

    source: FavoritesRecordReference
    quick_key_status_records: tuple[FavoritesRecordReference, ...]
    sites: tuple[FavoritesTrunkSite, ...]
    departments: tuple[FavoritesTrunkDepartment, ...]
    supplemental_records: tuple[FavoritesRecordReference, ...]

    @property
    def name(self) -> str | None:
        return _record_name(self.source)


@dataclass(frozen=True, slots=True)
class FavoritesHierarchy:
    """Read-only typed projection that retains the complete lossless source."""

    source: FavoritesSourceFile
    metadata_records: tuple[FavoritesRecordReference, ...]
    systems: tuple[
        FavoritesConventionalSystem | FavoritesTrunkSystem,
        ...,
    ]
    unclassified_records: tuple[FavoritesRecordReference, ...]


@dataclass(slots=True)
class _ConventionalDepartmentBuilder:
    source: FavoritesRecordReference
    channels: list[FavoritesConventionalChannel] = field(
        default_factory=list
    )


@dataclass(slots=True)
class _ConventionalSystemBuilder:
    source: FavoritesRecordReference
    quick_key_status_records: list[
        FavoritesRecordReference
    ] = field(default_factory=list)
    departments: list[
        _ConventionalDepartmentBuilder
    ] = field(default_factory=list)
    supplemental_records: list[
        FavoritesRecordReference
    ] = field(default_factory=list)


@dataclass(slots=True)
class _TrunkSiteBuilder:
    source: FavoritesRecordReference
    frequencies: list[FavoritesRecordReference] = field(
        default_factory=list
    )
    band_plans: list[FavoritesRecordReference] = field(
        default_factory=list
    )


@dataclass(slots=True)
class _TrunkDepartmentBuilder:
    source: FavoritesRecordReference
    channels: list[FavoritesTrunkChannel] = field(
        default_factory=list
    )


@dataclass(slots=True)
class _TrunkSystemBuilder:
    source: FavoritesRecordReference
    quick_key_status_records: list[
        FavoritesRecordReference
    ] = field(default_factory=list)
    sites: list[_TrunkSiteBuilder] = field(
        default_factory=list
    )
    departments: list[
        _TrunkDepartmentBuilder
    ] = field(default_factory=list)
    supplemental_records: list[
        FavoritesRecordReference
    ] = field(default_factory=list)


def _hierarchy_error(
    reference: FavoritesRecordReference,
    message: str,
) -> FavoritesHierarchyError:
    return FavoritesHierarchyError(
        reference.source_index,
        reference.record.command,
        message,
    )


def _freeze_conventional_system(
    builder: _ConventionalSystemBuilder,
) -> FavoritesConventionalSystem:
    return FavoritesConventionalSystem(
        source=builder.source,
        quick_key_status_records=tuple(
            builder.quick_key_status_records
        ),
        departments=tuple(
            FavoritesConventionalDepartment(
                source=department.source,
                channels=tuple(department.channels),
            )
            for department in builder.departments
        ),
        supplemental_records=tuple(
            builder.supplemental_records
        ),
    )


def _freeze_trunk_system(
    builder: _TrunkSystemBuilder,
) -> FavoritesTrunkSystem:
    return FavoritesTrunkSystem(
        source=builder.source,
        quick_key_status_records=tuple(
            builder.quick_key_status_records
        ),
        sites=tuple(
            FavoritesTrunkSite(
                source=site.source,
                frequencies=tuple(site.frequencies),
                band_plans=tuple(site.band_plans),
            )
            for site in builder.sites
        ),
        departments=tuple(
            FavoritesTrunkDepartment(
                source=department.source,
                channels=tuple(department.channels),
            )
            for department in builder.departments
        ),
        supplemental_records=tuple(
            builder.supplemental_records
        ),
    )


def project_favorites_hierarchy(
    source: FavoritesSourceFile,
) -> FavoritesHierarchy:
    """Project ordered HPD records without changing their raw representation."""

    if not isinstance(source, FavoritesSourceFile):
        raise TypeError(
            "Favorites hierarchy source must be "
            "FavoritesSourceFile."
        )

    metadata_records: list[FavoritesRecordReference] = []
    unclassified_records: list[
        FavoritesRecordReference
    ] = []

    system_builders: list[
        _ConventionalSystemBuilder | _TrunkSystemBuilder
    ] = []

    current_system: (
        _ConventionalSystemBuilder
        | _TrunkSystemBuilder
        | None
    ) = None
    current_conventional_department: (
        _ConventionalDepartmentBuilder | None
    ) = None
    current_trunk_department: (
        _TrunkDepartmentBuilder | None
    ) = None
    current_site: _TrunkSiteBuilder | None = None

    for source_index, record in enumerate(source.records):
        reference = FavoritesRecordReference(
            source_index=source_index,
            record=record,
        )
        command = record.command

        if command in {"TargetModel", "FormatVersion"}:
            metadata_records.append(reference)
            continue

        if command == "Conventional":
            conventional_system_builder = (
                _ConventionalSystemBuilder(
                    source=reference
                )
            )
            system_builders.append(
                conventional_system_builder
            )
            current_system = conventional_system_builder
            current_conventional_department = None
            current_trunk_department = None
            current_site = None
            continue

        if command == "Trunk":
            trunk_system_builder = _TrunkSystemBuilder(
                source=reference
            )
            system_builders.append(
                trunk_system_builder
            )
            current_system = trunk_system_builder
            current_conventional_department = None
            current_trunk_department = None
            current_site = None
            continue

        if command == "DQKs_Status":
            if current_system is None:
                unclassified_records.append(reference)
            else:
                current_system.quick_key_status_records.append(
                    reference
                )
            continue

        if command == "C-Group":
            if not isinstance(
                current_system,
                _ConventionalSystemBuilder,
            ):
                raise _hierarchy_error(
                    reference,
                    "C-Group requires a current "
                    "Conventional system.",
                )

            conventional_department_builder = (
                _ConventionalDepartmentBuilder(
                    source=reference
                )
            )
            current_system.departments.append(
                conventional_department_builder
            )
            current_conventional_department = (
                conventional_department_builder
            )
            current_trunk_department = None
            current_site = None
            continue

        if command == "C-Freq":
            if (
                not isinstance(
                    current_system,
                    _ConventionalSystemBuilder,
                )
                or current_conventional_department is None
            ):
                raise _hierarchy_error(
                    reference,
                    "C-Freq requires a current "
                    "Conventional C-Group.",
                )

            current_conventional_department.channels.append(
                FavoritesConventionalChannel(
                    source=reference
                )
            )
            continue

        if command == "Site":
            if not isinstance(
                current_system,
                _TrunkSystemBuilder,
            ):
                raise _hierarchy_error(
                    reference,
                    "Site requires a current Trunk system.",
                )

            site = _TrunkSiteBuilder(source=reference)
            current_system.sites.append(site)
            current_site = site
            current_trunk_department = None
            current_conventional_department = None
            continue

        if command == "T-Freq":
            if (
                not isinstance(
                    current_system,
                    _TrunkSystemBuilder,
                )
                or current_site is None
            ):
                raise _hierarchy_error(
                    reference,
                    "T-Freq requires a current Trunk Site.",
                )

            current_site.frequencies.append(reference)
            continue

        if command in {"BandPlan_Mot", "BandPlan_P25"}:
            if (
                not isinstance(
                    current_system,
                    _TrunkSystemBuilder,
                )
                or current_site is None
            ):
                raise _hierarchy_error(
                    reference,
                    f"{command} requires a current Trunk Site.",
                )

            current_site.band_plans.append(reference)
            continue

        if command == "T-Group":
            if not isinstance(
                current_system,
                _TrunkSystemBuilder,
            ):
                raise _hierarchy_error(
                    reference,
                    "T-Group requires a current Trunk system.",
                )

            trunk_department_builder = (
                _TrunkDepartmentBuilder(
                    source=reference
                )
            )
            current_system.departments.append(
                trunk_department_builder
            )
            current_trunk_department = (
                trunk_department_builder
            )
            current_site = None
            current_conventional_department = None
            continue

        if command == "TGID":
            if (
                not isinstance(
                    current_system,
                    _TrunkSystemBuilder,
                )
                or current_trunk_department is None
            ):
                raise _hierarchy_error(
                    reference,
                    "TGID requires a current Trunk T-Group.",
                )

            current_trunk_department.channels.append(
                FavoritesTrunkChannel(
                    source=reference
                )
            )
            continue

        if command in _SYSTEM_SUPPLEMENTAL_COMMANDS:
            if current_system is None:
                unclassified_records.append(reference)
            else:
                current_system.supplemental_records.append(
                    reference
                )
            continue

        unclassified_records.append(reference)

    systems: list[
        FavoritesConventionalSystem | FavoritesTrunkSystem
    ] = []

    for system_builder in system_builders:
        if isinstance(
            system_builder,
            _ConventionalSystemBuilder,
        ):
            systems.append(
                _freeze_conventional_system(
                    system_builder
                )
            )
        else:
            systems.append(
                _freeze_trunk_system(system_builder)
            )

    return FavoritesHierarchy(
        source=source,
        metadata_records=tuple(metadata_records),
        systems=tuple(systems),
        unclassified_records=tuple(
            unclassified_records
        ),
    )


__all__ = [
    "FavoritesConventionalChannel",
    "FavoritesConventionalDepartment",
    "FavoritesConventionalSystem",
    "FavoritesHierarchy",
    "FavoritesHierarchyError",
    "FavoritesRecordReference",
    "FavoritesTrunkChannel",
    "FavoritesTrunkDepartment",
    "FavoritesTrunkSite",
    "FavoritesTrunkSystem",
    "project_favorites_hierarchy",
]
