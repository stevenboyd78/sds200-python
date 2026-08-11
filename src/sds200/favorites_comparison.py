"""Renderer-neutral exact comparison over immutable Favorites workspaces."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum

from .favorites_file import FavoritesSourceFile, FavoritesSourceRecord
from .favorites_workspace import (
    FavoritesHierarchyDocument,
    FavoritesWorkspace,
)


class FavoritesComparisonSourceKind(StrEnum):
    """Identify the preserved Favorites source being compared."""

    CATALOG = "catalog"
    HPD = "hpd"


class FavoritesComparisonSourceState(StrEnum):
    """Classify one unambiguous preserved source pairing."""

    MATCHED = "matched"
    ADDED = "added"
    REMOVED = "removed"


class FavoritesComparisonChangeKind(StrEnum):
    """Classify one exact source-record change."""

    ADDED = "added"
    REMOVED = "removed"
    REPLACED = "replaced"


@dataclass(frozen=True, slots=True)
class FavoritesComparisonDocumentReference:
    """Reference one exact HPD document by workspace position and identity."""

    document_index: int
    document: FavoritesHierarchyDocument

    def __post_init__(self) -> None:
        if type(self.document_index) is not int or self.document_index < 0:
            raise ValueError(
                "Favorites comparison document index must be a "
                "non-negative integer."
            )
        if not isinstance(self.document, FavoritesHierarchyDocument):
            raise TypeError(
                "Favorites comparison document reference requires "
                "FavoritesHierarchyDocument."
            )

    @property
    def filename(self) -> str:
        """Return the exact HPD filename."""

        return self.document.filename

    @property
    def source(self) -> FavoritesSourceFile:
        """Return the exact preserved HPD source file."""

        return self.document.hierarchy.source


@dataclass(frozen=True, slots=True)
class FavoritesComparisonRecordChange:
    """One exact record addition, removal, or positional replacement."""

    kind: FavoritesComparisonChangeKind
    baseline_source_index: int | None
    baseline_record: FavoritesSourceRecord | None
    candidate_source_index: int | None
    candidate_record: FavoritesSourceRecord | None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, FavoritesComparisonChangeKind):
            raise TypeError(
                "Favorites comparison record change kind must be "
                "FavoritesComparisonChangeKind."
            )

        expected = {
            FavoritesComparisonChangeKind.ADDED: (False, True),
            FavoritesComparisonChangeKind.REMOVED: (True, False),
            FavoritesComparisonChangeKind.REPLACED: (True, True),
        }
        baseline_required, candidate_required = expected[self.kind]

        self._validate_side(
            "baseline",
            self.baseline_source_index,
            self.baseline_record,
            required=baseline_required,
        )
        self._validate_side(
            "candidate",
            self.candidate_source_index,
            self.candidate_record,
            required=candidate_required,
        )

        if (
            self.kind is FavoritesComparisonChangeKind.REPLACED
            and self.baseline_record is not None
            and self.candidate_record is not None
            and self.baseline_record.raw_bytes == self.candidate_record.raw_bytes
        ):
            raise ValueError(
                "Favorites replacement records must differ in exact raw bytes."
            )

    @staticmethod
    def _validate_side(
        label: str,
        source_index: int | None,
        record: FavoritesSourceRecord | None,
        *,
        required: bool,
    ) -> None:
        present = source_index is not None or record is not None
        if present != required:
            raise ValueError(
                f"Favorites comparison {label} record provenance "
                "does not match the change kind."
            )

        if not required:
            return

        if type(source_index) is not int or source_index < 0:
            raise ValueError(
                f"Favorites comparison {label} source index must be "
                "a non-negative integer."
            )
        if not isinstance(record, FavoritesSourceRecord):
            raise TypeError(
                f"Favorites comparison {label} record must be "
                "FavoritesSourceRecord."
            )


_RecordChangeSpec = tuple[
    FavoritesComparisonChangeKind,
    int | None,
    int | None,
]


def _record_change_specs(
    baseline: FavoritesSourceFile | None,
    candidate: FavoritesSourceFile | None,
) -> tuple[_RecordChangeSpec, ...]:
    if baseline is None:
        if candidate is None:
            raise ValueError(
                "Favorites comparison requires at least one preserved source."
            )
        return tuple(
            (
                FavoritesComparisonChangeKind.ADDED,
                None,
                source_index,
            )
            for source_index in range(len(candidate.records))
        )

    if candidate is None:
        return tuple(
            (
                FavoritesComparisonChangeKind.REMOVED,
                source_index,
                None,
            )
            for source_index in range(len(baseline.records))
        )

    matcher = SequenceMatcher(
        a=tuple(record.raw_bytes for record in baseline.records),
        b=tuple(record.raw_bytes for record in candidate.records),
        autojunk=False,
    )

    specs: list[_RecordChangeSpec] = []

    for tag, baseline_start, baseline_end, candidate_start, candidate_end in (
        matcher.get_opcodes()
    ):
        if tag == "equal":
            continue

        if tag == "delete":
            specs.extend(
                (
                    FavoritesComparisonChangeKind.REMOVED,
                    source_index,
                    None,
                )
                for source_index in range(baseline_start, baseline_end)
            )
            continue

        if tag == "insert":
            specs.extend(
                (
                    FavoritesComparisonChangeKind.ADDED,
                    None,
                    source_index,
                )
                for source_index in range(candidate_start, candidate_end)
            )
            continue

        if tag != "replace":
            raise RuntimeError(
                f"Unsupported SequenceMatcher opcode: {tag!r}"
            )

        baseline_count = baseline_end - baseline_start
        candidate_count = candidate_end - candidate_start
        replacement_count = min(baseline_count, candidate_count)

        specs.extend(
            (
                FavoritesComparisonChangeKind.REPLACED,
                baseline_start + offset,
                candidate_start + offset,
            )
            for offset in range(replacement_count)
        )
        specs.extend(
            (
                FavoritesComparisonChangeKind.REMOVED,
                source_index,
                None,
            )
            for source_index in range(
                baseline_start + replacement_count,
                baseline_end,
            )
        )
        specs.extend(
            (
                FavoritesComparisonChangeKind.ADDED,
                None,
                source_index,
            )
            for source_index in range(
                candidate_start + replacement_count,
                candidate_end,
            )
        )

    return tuple(specs)


def _record_changes(
    baseline: FavoritesSourceFile | None,
    candidate: FavoritesSourceFile | None,
) -> tuple[FavoritesComparisonRecordChange, ...]:
    changes: list[FavoritesComparisonRecordChange] = []

    for kind, baseline_index, candidate_index in _record_change_specs(
        baseline,
        candidate,
    ):
        changes.append(
            FavoritesComparisonRecordChange(
                kind=kind,
                baseline_source_index=baseline_index,
                baseline_record=(
                    None
                    if baseline_index is None or baseline is None
                    else baseline.records[baseline_index]
                ),
                candidate_source_index=candidate_index,
                candidate_record=(
                    None
                    if candidate_index is None or candidate is None
                    else candidate.records[candidate_index]
                ),
            )
        )

    return tuple(changes)


@dataclass(frozen=True, slots=True)
class FavoritesComparisonSource:
    """One exact unambiguous catalog or HPD source comparison."""

    source_kind: FavoritesComparisonSourceKind
    state: FavoritesComparisonSourceState
    filename: str | None
    baseline_document: FavoritesComparisonDocumentReference | None
    candidate_document: FavoritesComparisonDocumentReference | None
    baseline_source: FavoritesSourceFile | None
    candidate_source: FavoritesSourceFile | None
    record_changes: tuple[FavoritesComparisonRecordChange, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.source_kind, FavoritesComparisonSourceKind):
            raise TypeError(
                "Favorites comparison source kind must be "
                "FavoritesComparisonSourceKind."
            )
        if not isinstance(self.state, FavoritesComparisonSourceState):
            raise TypeError(
                "Favorites comparison source state must be "
                "FavoritesComparisonSourceState."
            )

        if self.source_kind is FavoritesComparisonSourceKind.CATALOG:
            self._validate_catalog_source()
        else:
            self._validate_hpd_source()

        if type(self.record_changes) is not tuple:
            raise TypeError(
                "Favorites comparison record changes must be a tuple."
            )
        if any(
            not isinstance(change, FavoritesComparisonRecordChange)
            for change in self.record_changes
        ):
            raise TypeError(
                "Favorites comparison record changes must contain "
                "FavoritesComparisonRecordChange values."
            )

        expected_specs = _record_change_specs(
            self.baseline_source,
            self.candidate_source,
        )
        actual_specs = tuple(
            (
                change.kind,
                change.baseline_source_index,
                change.candidate_source_index,
            )
            for change in self.record_changes
        )
        if actual_specs != expected_specs:
            raise ValueError(
                "Favorites comparison record changes must match the "
                "deterministic exact-record diff."
            )

        for change in self.record_changes:
            self._validate_record_provenance(change)

    def _validate_catalog_source(self) -> None:
        if self.state is not FavoritesComparisonSourceState.MATCHED:
            raise ValueError(
                "Favorites catalog comparison source must be matched."
            )
        if self.filename is not None:
            raise ValueError(
                "Favorites catalog comparison source must not have a filename."
            )
        if (
            self.baseline_document is not None
            or self.candidate_document is not None
        ):
            raise ValueError(
                "Favorites catalog comparison source must not have HPD "
                "document references."
            )
        if not isinstance(self.baseline_source, FavoritesSourceFile):
            raise TypeError(
                "Favorites catalog comparison requires a baseline "
                "FavoritesSourceFile."
            )
        if not isinstance(self.candidate_source, FavoritesSourceFile):
            raise TypeError(
                "Favorites catalog comparison requires a candidate "
                "FavoritesSourceFile."
            )

    def _validate_hpd_source(self) -> None:
        if not isinstance(self.filename, str) or not self.filename:
            raise ValueError(
                "Favorites HPD comparison source requires a non-empty "
                "exact filename."
            )

        expected_presence = {
            FavoritesComparisonSourceState.MATCHED: (True, True),
            FavoritesComparisonSourceState.ADDED: (False, True),
            FavoritesComparisonSourceState.REMOVED: (True, False),
        }
        baseline_required, candidate_required = expected_presence[self.state]

        self._validate_hpd_side(
            "baseline",
            self.baseline_document,
            self.baseline_source,
            required=baseline_required,
        )
        self._validate_hpd_side(
            "candidate",
            self.candidate_document,
            self.candidate_source,
            required=candidate_required,
        )

    def _validate_hpd_side(
        self,
        label: str,
        document: FavoritesComparisonDocumentReference | None,
        source: FavoritesSourceFile | None,
        *,
        required: bool,
    ) -> None:
        present = document is not None or source is not None
        if present != required:
            raise ValueError(
                f"Favorites HPD comparison {label} provenance does not "
                "match the source state."
            )
        if not required:
            return

        if not isinstance(document, FavoritesComparisonDocumentReference):
            raise TypeError(
                f"Favorites HPD comparison {label} document must be "
                "FavoritesComparisonDocumentReference."
            )
        if not isinstance(source, FavoritesSourceFile):
            raise TypeError(
                f"Favorites HPD comparison {label} source must be "
                "FavoritesSourceFile."
            )
        if document.filename != self.filename:
            raise ValueError(
                f"Favorites HPD comparison {label} filename must exactly "
                "match its document."
            )
        if source is not document.source:
            raise ValueError(
                f"Favorites HPD comparison {label} source must be the "
                "exact document source object."
            )

    def _validate_record_provenance(
        self,
        change: FavoritesComparisonRecordChange,
    ) -> None:
        self._validate_record_side(
            "baseline",
            self.baseline_source,
            change.baseline_source_index,
            change.baseline_record,
        )
        self._validate_record_side(
            "candidate",
            self.candidate_source,
            change.candidate_source_index,
            change.candidate_record,
        )

    @staticmethod
    def _validate_record_side(
        label: str,
        source: FavoritesSourceFile | None,
        source_index: int | None,
        record: FavoritesSourceRecord | None,
    ) -> None:
        if source_index is None:
            return
        if source is None or source_index >= len(source.records):
            raise ValueError(
                f"Favorites comparison {label} record index is not "
                "present in its source file."
            )
        if record is not source.records[source_index]:
            raise ValueError(
                f"Favorites comparison {label} record must be the exact "
                "original source record at its source index."
            )

    @property
    def changed(self) -> bool:
        """Return whether this preserved source differs."""

        return (
            self.state is not FavoritesComparisonSourceState.MATCHED
            or bool(self.record_changes)
        )


@dataclass(frozen=True, slots=True)
class FavoritesComparisonAmbiguity:
    """One exact HPD filename that cannot be paired without guessing."""

    filename: str
    baseline_documents: tuple[FavoritesComparisonDocumentReference, ...]
    candidate_documents: tuple[FavoritesComparisonDocumentReference, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.filename, str) or not self.filename:
            raise ValueError(
                "Favorites comparison ambiguity requires a non-empty "
                "exact filename."
            )

        self._validate_documents(
            "baseline",
            self.baseline_documents,
        )
        self._validate_documents(
            "candidate",
            self.candidate_documents,
        )

        if (
            len(self.baseline_documents) <= 1
            and len(self.candidate_documents) <= 1
        ):
            raise ValueError(
                "Favorites comparison ambiguity requires duplicate HPD "
                "documents on at least one side."
            )

    def _validate_documents(
        self,
        label: str,
        documents: tuple[FavoritesComparisonDocumentReference, ...],
    ) -> None:
        if type(documents) is not tuple:
            raise TypeError(
                f"Favorites comparison ambiguity {label} documents "
                "must be a tuple."
            )
        if any(
            not isinstance(
                document,
                FavoritesComparisonDocumentReference,
            )
            for document in documents
        ):
            raise TypeError(
                f"Favorites comparison ambiguity {label} documents "
                "must contain FavoritesComparisonDocumentReference values."
            )

        indexes = tuple(
            document.document_index
            for document in documents
        )
        if indexes != tuple(sorted(indexes)):
            raise ValueError(
                f"Favorites comparison ambiguity {label} documents "
                "must preserve workspace order."
            )
        if len(set(indexes)) != len(indexes):
            raise ValueError(
                f"Favorites comparison ambiguity {label} document "
                "indexes must be unique."
            )
        if any(
            document.filename != self.filename
            for document in documents
        ):
            raise ValueError(
                f"Favorites comparison ambiguity {label} filenames "
                "must exactly match."
            )


FavoritesComparisonItem = (
    FavoritesComparisonSource | FavoritesComparisonAmbiguity
)


def _document_references(
    workspace: FavoritesWorkspace,
) -> dict[str, tuple[FavoritesComparisonDocumentReference, ...]]:
    references: dict[
        str,
        list[FavoritesComparisonDocumentReference],
    ] = {}

    for document_index, document in enumerate(workspace.documents):
        references.setdefault(document.filename, []).append(
            FavoritesComparisonDocumentReference(
                document_index=document_index,
                document=document,
            )
        )

    return {
        filename: tuple(items)
        for filename, items in references.items()
    }


def _validate_document_reference(
    workspace: FavoritesWorkspace,
    reference: FavoritesComparisonDocumentReference,
    *,
    label: str,
) -> None:
    if reference.document_index >= len(workspace.documents):
        raise ValueError(
            f"Favorites comparison {label} document index is not "
            "present in its workspace."
        )
    if workspace.documents[reference.document_index] is not reference.document:
        raise ValueError(
            f"Favorites comparison {label} document must be the exact "
            "original workspace document at its document index."
        )


def _item_filename(
    item: FavoritesComparisonItem,
) -> str | None:
    if isinstance(item, FavoritesComparisonAmbiguity):
        return item.filename
    return item.filename


def _item_order_key(
    item: FavoritesComparisonItem,
) -> tuple[int, int]:
    if (
        isinstance(item, FavoritesComparisonSource)
        and item.source_kind is FavoritesComparisonSourceKind.CATALOG
    ):
        return (0, 0)

    if isinstance(item, FavoritesComparisonAmbiguity):
        if item.baseline_documents:
            return (
                1,
                item.baseline_documents[0].document_index,
            )
        return (
            2,
            item.candidate_documents[0].document_index,
        )

    if item.baseline_document is not None:
        return (
            1,
            item.baseline_document.document_index,
        )

    if item.candidate_document is None:
        raise ValueError(
            "Favorites HPD comparison item has no document provenance."
        )
    return (
        2,
        item.candidate_document.document_index,
    )


@dataclass(frozen=True, slots=True)
class FavoritesWorkspaceComparison:
    """Immutable exact comparison retaining both authoritative workspaces."""

    baseline: FavoritesWorkspace
    candidate: FavoritesWorkspace
    items: tuple[FavoritesComparisonItem, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.baseline, FavoritesWorkspace):
            raise TypeError(
                "Favorites comparison baseline must be FavoritesWorkspace."
            )
        if not isinstance(self.candidate, FavoritesWorkspace):
            raise TypeError(
                "Favorites comparison candidate must be FavoritesWorkspace."
            )
        if type(self.items) is not tuple:
            raise TypeError(
                "Favorites comparison items must be a tuple."
            )
        if any(
            not isinstance(
                item,
                (
                    FavoritesComparisonSource,
                    FavoritesComparisonAmbiguity,
                ),
            )
            for item in self.items
        ):
            raise TypeError(
                "Favorites comparison items must contain source "
                "comparisons or ambiguities."
            )

        if not self.items:
            raise ValueError(
                "Favorites comparison must contain the catalog comparison."
            )

        catalog = self.items[0]
        if (
            not isinstance(catalog, FavoritesComparisonSource)
            or catalog.source_kind is not FavoritesComparisonSourceKind.CATALOG
        ):
            raise ValueError(
                "Favorites comparison first item must be the catalog source."
            )
        if catalog.baseline_source is not self.baseline.catalog.source:
            raise ValueError(
                "Favorites comparison catalog baseline source must be "
                "the exact workspace catalog source."
            )
        if catalog.candidate_source is not self.candidate.catalog.source:
            raise ValueError(
                "Favorites comparison catalog candidate source must be "
                "the exact workspace catalog source."
            )

        previous_key: tuple[int, int] | None = None
        seen_filenames: set[str] = set()

        baseline_by_filename = _document_references(self.baseline)
        candidate_by_filename = _document_references(self.candidate)

        for item in self.items:
            order_key = _item_order_key(item)
            if previous_key is not None and order_key <= previous_key:
                raise ValueError(
                    "Favorites comparison items must preserve canonical "
                    "catalog/baseline/candidate order."
                )
            previous_key = order_key

            filename = _item_filename(item)
            if filename is None:
                continue
            if filename in seen_filenames:
                raise ValueError(
                    "Favorites comparison HPD filenames must be represented "
                    "exactly once."
                )
            seen_filenames.add(filename)

            baseline_documents = baseline_by_filename.get(filename, ())
            candidate_documents = candidate_by_filename.get(filename, ())

            if (
                len(baseline_documents) > 1
                or len(candidate_documents) > 1
            ):
                if not isinstance(item, FavoritesComparisonAmbiguity):
                    raise ValueError(
                        "Duplicate HPD filenames must be represented as "
                        "comparison ambiguities."
                    )
                if item.baseline_documents != baseline_documents:
                    raise ValueError(
                        "Favorites comparison ambiguity baseline documents "
                        "must preserve exact workspace provenance."
                    )
                if item.candidate_documents != candidate_documents:
                    raise ValueError(
                        "Favorites comparison ambiguity candidate documents "
                        "must preserve exact workspace provenance."
                    )
                for reference in item.baseline_documents:
                    _validate_document_reference(
                        self.baseline,
                        reference,
                        label="baseline",
                    )
                for reference in item.candidate_documents:
                    _validate_document_reference(
                        self.candidate,
                        reference,
                        label="candidate",
                    )
                continue

            if not isinstance(item, FavoritesComparisonSource):
                raise ValueError(
                    "Unambiguous HPD filename must be represented as a "
                    "source comparison."
                )
            if item.source_kind is not FavoritesComparisonSourceKind.HPD:
                raise ValueError(
                    "HPD filename item must have HPD comparison source kind."
                )

            expected_state: FavoritesComparisonSourceState
            if baseline_documents and candidate_documents:
                expected_state = FavoritesComparisonSourceState.MATCHED
            elif baseline_documents:
                expected_state = FavoritesComparisonSourceState.REMOVED
            else:
                expected_state = FavoritesComparisonSourceState.ADDED

            if item.state is not expected_state:
                raise ValueError(
                    "Favorites HPD comparison source state does not match "
                    "workspace document presence."
                )
            if item.baseline_document != (
                baseline_documents[0]
                if baseline_documents
                else None
            ):
                raise ValueError(
                    "Favorites HPD comparison baseline document must preserve "
                    "exact workspace provenance."
                )
            if item.candidate_document != (
                candidate_documents[0]
                if candidate_documents
                else None
            ):
                raise ValueError(
                    "Favorites HPD comparison candidate document must preserve "
                    "exact workspace provenance."
                )
            if item.baseline_document is not None:
                _validate_document_reference(
                    self.baseline,
                    item.baseline_document,
                    label="baseline",
                )
            if item.candidate_document is not None:
                _validate_document_reference(
                    self.candidate,
                    item.candidate_document,
                    label="candidate",
                )

        expected_filenames = (
            set(baseline_by_filename)
            | set(candidate_by_filename)
        )
        if seen_filenames != expected_filenames:
            raise ValueError(
                "Favorites comparison must represent every HPD filename "
                "exactly once."
            )

    @property
    def has_ambiguity(self) -> bool:
        """Return whether duplicate filenames prevent exact HPD pairing."""

        return any(
            isinstance(item, FavoritesComparisonAmbiguity)
            for item in self.items
        )

    @property
    def has_changes(self) -> bool:
        """Return whether any unambiguous preserved source differs."""

        return any(
            item.changed
            for item in self.items
            if isinstance(item, FavoritesComparisonSource)
        )

    @property
    def is_comparable(self) -> bool:
        """Return whether every HPD filename has an unambiguous pairing."""

        return not self.has_ambiguity

    @property
    def is_equal(self) -> bool:
        """Return whether exact preserved source material is fully equal."""

        return self.is_comparable and not self.has_changes


def _source_comparison(
    *,
    source_kind: FavoritesComparisonSourceKind,
    state: FavoritesComparisonSourceState,
    filename: str | None,
    baseline_document: FavoritesComparisonDocumentReference | None,
    candidate_document: FavoritesComparisonDocumentReference | None,
    baseline_source: FavoritesSourceFile | None,
    candidate_source: FavoritesSourceFile | None,
) -> FavoritesComparisonSource:
    return FavoritesComparisonSource(
        source_kind=source_kind,
        state=state,
        filename=filename,
        baseline_document=baseline_document,
        candidate_document=candidate_document,
        baseline_source=baseline_source,
        candidate_source=candidate_source,
        record_changes=_record_changes(
            baseline_source,
            candidate_source,
        ),
    )


def compare_favorites_workspaces(
    baseline: FavoritesWorkspace,
    candidate: FavoritesWorkspace,
) -> FavoritesWorkspaceComparison:
    """Return an exact immutable comparison without storage access or repair."""

    if not isinstance(baseline, FavoritesWorkspace):
        raise TypeError(
            "Favorites comparison baseline must be FavoritesWorkspace."
        )
    if not isinstance(candidate, FavoritesWorkspace):
        raise TypeError(
            "Favorites comparison candidate must be FavoritesWorkspace."
        )

    items: list[FavoritesComparisonItem] = [
        _source_comparison(
            source_kind=FavoritesComparisonSourceKind.CATALOG,
            state=FavoritesComparisonSourceState.MATCHED,
            filename=None,
            baseline_document=None,
            candidate_document=None,
            baseline_source=baseline.catalog.source,
            candidate_source=candidate.catalog.source,
        )
    ]

    baseline_by_filename = _document_references(baseline)
    candidate_by_filename = _document_references(candidate)
    emitted_filenames: set[str] = set()

    for document in baseline.documents:
        filename = document.filename
        if filename in emitted_filenames:
            continue

        baseline_documents = baseline_by_filename[filename]
        candidate_documents = candidate_by_filename.get(filename, ())

        if (
            len(baseline_documents) > 1
            or len(candidate_documents) > 1
        ):
            items.append(
                FavoritesComparisonAmbiguity(
                    filename=filename,
                    baseline_documents=baseline_documents,
                    candidate_documents=candidate_documents,
                )
            )
        elif candidate_documents:
            baseline_reference = baseline_documents[0]
            candidate_reference = candidate_documents[0]
            items.append(
                _source_comparison(
                    source_kind=FavoritesComparisonSourceKind.HPD,
                    state=FavoritesComparisonSourceState.MATCHED,
                    filename=filename,
                    baseline_document=baseline_reference,
                    candidate_document=candidate_reference,
                    baseline_source=baseline_reference.source,
                    candidate_source=candidate_reference.source,
                )
            )
        else:
            baseline_reference = baseline_documents[0]
            items.append(
                _source_comparison(
                    source_kind=FavoritesComparisonSourceKind.HPD,
                    state=FavoritesComparisonSourceState.REMOVED,
                    filename=filename,
                    baseline_document=baseline_reference,
                    candidate_document=None,
                    baseline_source=baseline_reference.source,
                    candidate_source=None,
                )
            )

        emitted_filenames.add(filename)

    for document in candidate.documents:
        filename = document.filename
        if filename in emitted_filenames:
            continue

        candidate_documents = candidate_by_filename[filename]

        if len(candidate_documents) > 1:
            items.append(
                FavoritesComparisonAmbiguity(
                    filename=filename,
                    baseline_documents=(),
                    candidate_documents=candidate_documents,
                )
            )
        else:
            candidate_reference = candidate_documents[0]
            items.append(
                _source_comparison(
                    source_kind=FavoritesComparisonSourceKind.HPD,
                    state=FavoritesComparisonSourceState.ADDED,
                    filename=filename,
                    baseline_document=None,
                    candidate_document=candidate_reference,
                    baseline_source=None,
                    candidate_source=candidate_reference.source,
                )
            )

        emitted_filenames.add(filename)

    return FavoritesWorkspaceComparison(
        baseline=baseline,
        candidate=candidate,
        items=tuple(items),
    )


__all__ = [
    "FavoritesComparisonAmbiguity",
    "FavoritesComparisonChangeKind",
    "FavoritesComparisonDocumentReference",
    "FavoritesComparisonItem",
    "FavoritesComparisonRecordChange",
    "FavoritesComparisonSource",
    "FavoritesComparisonSourceKind",
    "FavoritesComparisonSourceState",
    "FavoritesWorkspaceComparison",
    "compare_favorites_workspaces",
]
