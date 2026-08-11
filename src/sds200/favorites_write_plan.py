"""Renderer-neutral immutable Favorites write planning."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .favorites_comparison import (
    FavoritesWorkspaceComparison,
    compare_favorites_workspaces,
)
from .favorites_schema import (
    FavoritesSchemaValidation,
    validate_favorites_workspace,
)
from .favorites_storage import (
    FavoritesStorageSnapshot,
    export_favorites_workspace_snapshot,
    project_favorites_storage_snapshot,
)
from .favorites_workspace import FavoritesWorkspace


class FavoritesWriteBlocker(StrEnum):
    """Classify one condition that prevents safe future execution."""

    COMPARISON_AMBIGUITY = "comparison_ambiguity"
    INTENDED_MISSING_ENTRY = "intended_missing_entry"
    INTENDED_AMBIGUOUS_ENTRY = "intended_ambiguous_entry"
    INTENDED_DUPLICATE_CATALOG_FILENAME = (
        "intended_duplicate_catalog_filename"
    )
    INTENDED_DUPLICATE_DOCUMENT_FILENAME = (
        "intended_duplicate_document_filename"
    )
    INTENDED_SCHEMA_ERROR = "intended_schema_error"


def _write_blockers(
    *,
    intended_workspace: FavoritesWorkspace,
    comparison: FavoritesWorkspaceComparison,
    intended_validation: FavoritesSchemaValidation,
) -> tuple[FavoritesWriteBlocker, ...]:
    blockers: list[FavoritesWriteBlocker] = []

    if comparison.has_ambiguity:
        blockers.append(
            FavoritesWriteBlocker.COMPARISON_AMBIGUITY
        )

    if intended_workspace.missing_entries:
        blockers.append(
            FavoritesWriteBlocker.INTENDED_MISSING_ENTRY
        )

    if intended_workspace.ambiguous_entries:
        blockers.append(
            FavoritesWriteBlocker.INTENDED_AMBIGUOUS_ENTRY
        )

    if intended_workspace.duplicate_catalog_filenames:
        blockers.append(
            FavoritesWriteBlocker.INTENDED_DUPLICATE_CATALOG_FILENAME
        )

    if intended_workspace.duplicate_document_filenames:
        blockers.append(
            FavoritesWriteBlocker.INTENDED_DUPLICATE_DOCUMENT_FILENAME
        )

    if not intended_validation.is_valid:
        blockers.append(
            FavoritesWriteBlocker.INTENDED_SCHEMA_ERROR
        )

    return tuple(blockers)


@dataclass(frozen=True, slots=True)
class FavoritesWritePlan:
    """Immutable write preview retaining exact source and safety evidence."""

    baseline_snapshot: FavoritesStorageSnapshot
    intended_snapshot: FavoritesStorageSnapshot
    baseline_workspace: FavoritesWorkspace
    intended_workspace: FavoritesWorkspace
    comparison: FavoritesWorkspaceComparison
    baseline_validation: FavoritesSchemaValidation
    intended_validation: FavoritesSchemaValidation
    blockers: tuple[FavoritesWriteBlocker, ...]

    def __post_init__(self) -> None:
        if not isinstance(
            self.baseline_snapshot,
            FavoritesStorageSnapshot,
        ):
            raise TypeError(
                "Favorites write-plan baseline snapshot must be "
                "FavoritesStorageSnapshot."
            )

        if not isinstance(
            self.intended_snapshot,
            FavoritesStorageSnapshot,
        ):
            raise TypeError(
                "Favorites write-plan intended snapshot must be "
                "FavoritesStorageSnapshot."
            )

        if not isinstance(
            self.baseline_workspace,
            FavoritesWorkspace,
        ):
            raise TypeError(
                "Favorites write-plan baseline workspace must be "
                "FavoritesWorkspace."
            )

        if not isinstance(
            self.intended_workspace,
            FavoritesWorkspace,
        ):
            raise TypeError(
                "Favorites write-plan intended workspace must be "
                "FavoritesWorkspace."
            )

        if not isinstance(
            self.comparison,
            FavoritesWorkspaceComparison,
        ):
            raise TypeError(
                "Favorites write-plan comparison must be "
                "FavoritesWorkspaceComparison."
            )

        if not isinstance(
            self.baseline_validation,
            FavoritesSchemaValidation,
        ):
            raise TypeError(
                "Favorites write-plan baseline validation must be "
                "FavoritesSchemaValidation."
            )

        if not isinstance(
            self.intended_validation,
            FavoritesSchemaValidation,
        ):
            raise TypeError(
                "Favorites write-plan intended validation must be "
                "FavoritesSchemaValidation."
            )

        if type(self.blockers) is not tuple:
            raise TypeError(
                "Favorites write-plan blockers must be a tuple."
            )

        if any(
            not isinstance(blocker, FavoritesWriteBlocker)
            for blocker in self.blockers
        ):
            raise TypeError(
                "Favorites write-plan blockers must contain "
                "FavoritesWriteBlocker values."
            )

        if (
            export_favorites_workspace_snapshot(
                self.baseline_workspace
            )
            != self.baseline_snapshot
        ):
            raise ValueError(
                "Favorites write-plan baseline workspace must preserve "
                "the exact baseline snapshot."
            )

        if (
            export_favorites_workspace_snapshot(
                self.intended_workspace
            )
            != self.intended_snapshot
        ):
            raise ValueError(
                "Favorites write-plan intended workspace must preserve "
                "the exact intended snapshot."
            )

        if self.comparison.baseline is not self.baseline_workspace:
            raise ValueError(
                "Favorites write-plan comparison must retain the exact "
                "baseline workspace."
            )

        if self.comparison.candidate is not self.intended_workspace:
            raise ValueError(
                "Favorites write-plan comparison must retain the exact "
                "intended workspace."
            )

        if (
            self.baseline_validation.workspace
            is not self.baseline_workspace
        ):
            raise ValueError(
                "Favorites write-plan baseline validation must retain "
                "the exact baseline workspace."
            )

        if (
            self.intended_validation.workspace
            is not self.intended_workspace
        ):
            raise ValueError(
                "Favorites write-plan intended validation must retain "
                "the exact intended workspace."
            )

        if self.baseline_validation != validate_favorites_workspace(
            self.baseline_workspace
        ):
            raise ValueError(
                "Favorites write-plan baseline validation must match "
                "the deterministic schema validation."
            )

        if self.intended_validation != validate_favorites_workspace(
            self.intended_workspace
        ):
            raise ValueError(
                "Favorites write-plan intended validation must match "
                "the deterministic schema validation."
            )

        expected_blockers = _write_blockers(
            intended_workspace=self.intended_workspace,
            comparison=self.comparison,
            intended_validation=self.intended_validation,
        )

        if self.blockers != expected_blockers:
            raise ValueError(
                "Favorites write-plan blockers must match the "
                "deterministic planning evidence."
            )

    @property
    def has_changes(self) -> bool:
        """Return whether the exact intended storage differs."""

        return self.baseline_snapshot != self.intended_snapshot

    @property
    def is_noop(self) -> bool:
        """Return whether the exact intended storage is unchanged."""

        return not self.has_changes

    @property
    def is_blocked(self) -> bool:
        """Return whether planning evidence prevents safe future execution."""

        return bool(self.blockers)

    def matches_baseline_snapshot(
        self,
        snapshot: FavoritesStorageSnapshot,
    ) -> bool:
        """Return whether a freshly read target exactly matches the baseline."""

        if not isinstance(snapshot, FavoritesStorageSnapshot):
            raise TypeError(
                "Favorites write-plan target check requires "
                "FavoritesStorageSnapshot."
            )

        return snapshot == self.baseline_snapshot


def plan_favorites_write(
    baseline_snapshot: FavoritesStorageSnapshot,
    intended_snapshot: FavoritesStorageSnapshot,
) -> FavoritesWritePlan:
    """Build one pure immutable write preview from exact storage snapshots."""

    if not isinstance(
        baseline_snapshot,
        FavoritesStorageSnapshot,
    ):
        raise TypeError(
            "Favorites write planning baseline requires "
            "FavoritesStorageSnapshot."
        )

    if not isinstance(
        intended_snapshot,
        FavoritesStorageSnapshot,
    ):
        raise TypeError(
            "Favorites write planning intended value requires "
            "FavoritesStorageSnapshot."
        )

    baseline_workspace = project_favorites_storage_snapshot(
        baseline_snapshot
    )
    intended_workspace = project_favorites_storage_snapshot(
        intended_snapshot
    )

    comparison = compare_favorites_workspaces(
        baseline_workspace,
        intended_workspace,
    )

    baseline_validation = validate_favorites_workspace(
        baseline_workspace
    )
    intended_validation = validate_favorites_workspace(
        intended_workspace
    )

    return FavoritesWritePlan(
        baseline_snapshot=baseline_snapshot,
        intended_snapshot=intended_snapshot,
        baseline_workspace=baseline_workspace,
        intended_workspace=intended_workspace,
        comparison=comparison,
        baseline_validation=baseline_validation,
        intended_validation=intended_validation,
        blockers=_write_blockers(
            intended_workspace=intended_workspace,
            comparison=comparison,
            intended_validation=intended_validation,
        ),
    )


__all__ = [
    "FavoritesWriteBlocker",
    "FavoritesWritePlan",
    "plan_favorites_write",
]
