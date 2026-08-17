"""Renderer-neutral application composition for assisted Favorites synchronization."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .favorites_editing import FavoritesRecordTarget
from .favorites_external import (
    FavoritesExternalFieldBinding,
    FavoritesExternalNameAcceptanceExecutor,
    FavoritesExternalRecordObservation,
    FavoritesExternalRecordPreview,
    FavoritesExternalSource,
)
from .favorites_external_field_acceptance import FavoritesExternalFieldAcceptanceExecutor
from .favorites_external_mapping import FavoritesExternalFieldMapping
from .favorites_external_provenance_lifecycle import (
    FavoritesExternalProvenanceLifecycle,
    FavoritesExternalProvenanceLifecycleSnapshot,
    FavoritesExternalProvenanceLifecycleState,
)
from .favorites_external_refresh import (
    FavoritesExternalRefreshResult,
    FavoritesExternalRefreshSession,
)
from .favorites_external_refresh_acceptance import (
    FavoritesExternalRefreshNameAcceptancePlan,
    plan_favorites_external_refresh_name_acceptance,
)
from .favorites_external_refresh_detach import (
    FavoritesExternalRefreshDetachPlan,
    FavoritesExternalRefreshDetachScope,
    plan_favorites_external_refresh_detach,
)
from .favorites_external_refresh_detach_orchestration import (
    FavoritesExternalRefreshDetachResult,
    execute_favorites_external_refresh_detach,
)
from .favorites_external_refresh_field_acceptance import (
    FavoritesExternalRefreshFieldAcceptancePlan,
    plan_favorites_external_refresh_field_acceptance,
)
from .favorites_external_refresh_field_orchestration import (
    FavoritesExternalRefreshFieldAcceptanceResult,
    execute_favorites_external_refresh_field_acceptance,
)
from .favorites_external_refresh_orchestration import (
    FavoritesExternalRefreshNameAcceptanceResult,
    execute_favorites_external_refresh_name_acceptance,
)
from .favorites_external_refresh_record_import import (
    FavoritesExternalRefreshRecordImportPlan,
    plan_favorites_external_refresh_record_import,
)
from .favorites_external_refresh_record_mutation import (
    FavoritesExternalRefreshRecordMutationExecutor,
    FavoritesExternalRefreshRecordMutationPlan,
)
from .favorites_external_refresh_record_orchestration import (
    FavoritesExternalRefreshRecordMutationResult,
    execute_favorites_external_refresh_record_mutation,
)
from .favorites_external_refresh_record_removal import (
    FavoritesExternalRefreshRecordDeletePlan,
    plan_favorites_external_refresh_record_delete,
    plan_favorites_external_refresh_record_keep_local,
)
from .favorites_file import FavoritesSourceRecord
from .radioreference import (
    RADIOREFERENCE_PROVIDER,
    RadioReferenceConfiguration,
    RadioReferenceSecretResolver,
    RadioReferenceSource,
)
from .radioreference_http import RadioReferenceHttpsSoapExchangeFactory
from .radioreference_mapping import (
    radioreference_favorites_frequency_mapping,
    radioreference_favorites_frequency_name_mapping,
    radioreference_favorites_talkgroup_decimal_mapping,
    radioreference_favorites_talkgroup_name_mapping,
)
from .radioreference_session import (
    RadioReferenceObservationRequestPlan,
    RadioReferenceObservationSessionFactory,
)


class RadioReferenceFavoritesMappedField(StrEnum):
    """Select one reviewed RadioReference-to-Favorites field mapping."""

    NAME = "name"
    FREQUENCY = "frequency"
    TALKGROUP_DECIMAL = "talkgroup_decimal"


class FavoritesExternalAssistedSynchronizationService:
    """Compose explicit assisted synchronization decisions over one lifecycle."""

    __slots__ = ("_lifecycle", "_refresh_session")

    def __init__(
        self,
        lifecycle: FavoritesExternalProvenanceLifecycle,
        source: FavoritesExternalSource,
    ) -> None:
        if not isinstance(lifecycle, FavoritesExternalProvenanceLifecycle):
            raise TypeError(
                "Assisted synchronization requires an exact "
                "FavoritesExternalProvenanceLifecycle."
            )
        self._refresh_session = FavoritesExternalRefreshSession(lifecycle, source)
        self._lifecycle = lifecycle

    @property
    def lifecycle_snapshot(self) -> FavoritesExternalProvenanceLifecycleSnapshot:
        """Return the exact current immutable lifecycle evidence."""

        return self._lifecycle.snapshot()

    def refresh(self) -> FavoritesExternalRefreshResult:
        """Perform one explicit provider read and return existing refresh evidence."""

        return self._refresh_session.refresh()

    def _require_current_refresh(
        self,
        refresh_result: FavoritesExternalRefreshResult,
    ) -> None:
        if type(refresh_result) is not FavoritesExternalRefreshResult:
            raise TypeError(
                "Assisted synchronization planning requires an exact refresh result."
            )
        current = self._lifecycle.snapshot()
        if current.state is not FavoritesExternalProvenanceLifecycleState.ACTIVE:
            raise RuntimeError(
                "Assisted synchronization lifecycle must be active for planning."
            )
        if refresh_result.lifecycle_snapshot != current:
            raise ValueError(
                "Assisted synchronization refresh lifecycle evidence is stale or "
                "belongs to another lifecycle."
            )

    def plan_name_acceptance(
        self,
        refresh_result: FavoritesExternalRefreshResult,
        selected_preview: FavoritesExternalRecordPreview,
    ) -> FavoritesExternalRefreshNameAcceptancePlan:
        self._require_current_refresh(refresh_result)
        return plan_favorites_external_refresh_name_acceptance(
            refresh_result, selected_preview
        )

    def execute_name_acceptance(
        self,
        plan: FavoritesExternalRefreshNameAcceptancePlan,
        executor: FavoritesExternalNameAcceptanceExecutor,
    ) -> FavoritesExternalRefreshNameAcceptanceResult:
        return execute_favorites_external_refresh_name_acceptance(
            plan, self._lifecycle, executor
        )

    def plan_field_acceptance(
        self,
        refresh_result: FavoritesExternalRefreshResult,
        selected_preview: FavoritesExternalRecordPreview,
        mapping: FavoritesExternalFieldMapping,
    ) -> FavoritesExternalRefreshFieldAcceptancePlan:
        self._require_current_refresh(refresh_result)
        return plan_favorites_external_refresh_field_acceptance(
            refresh_result, selected_preview, mapping
        )

    def execute_field_acceptance(
        self,
        plan: FavoritesExternalRefreshFieldAcceptancePlan,
        executor: FavoritesExternalFieldAcceptanceExecutor,
    ) -> FavoritesExternalRefreshFieldAcceptanceResult:
        return execute_favorites_external_refresh_field_acceptance(
            plan, self._lifecycle, executor
        )

    def radioreference_field_mapping(
        self,
        refresh_result: FavoritesExternalRefreshResult,
        selected_preview: FavoritesExternalRecordPreview,
        observation: FavoritesExternalRecordObservation,
        field: RadioReferenceFavoritesMappedField,
    ) -> FavoritesExternalFieldMapping:
        self._require_current_refresh(refresh_result)
        if type(selected_preview) is not FavoritesExternalRecordPreview:
            raise TypeError("RadioReference mapping requires an exact selected preview.")
        if type(observation) is not FavoritesExternalRecordObservation:
            raise TypeError("RadioReference mapping requires an exact observation.")
        if not isinstance(field, RadioReferenceFavoritesMappedField):
            raise TypeError("RadioReference mapping requires RadioReferenceFavoritesMappedField.")
        if selected_preview.target is None or selected_preview.external_identity is None:
            raise ValueError("RadioReference mapping requires a linked selected preview.")
        if selected_preview.external_identity.source.provider != RADIOREFERENCE_PROVIDER:
            raise ValueError("RadioReference mapping requires RadioReference preview evidence.")
        retained_previews = tuple(
            retained
            for retained in refresh_result.preview.records
            if retained is selected_preview
        )
        if len(retained_previews) != 1:
            raise ValueError(
                "RadioReference mapping requires the exact retained selected preview."
            )
        matches = tuple(
            retained
            for retained in refresh_result.observations
            if retained is observation
            and retained.identity == selected_preview.external_identity
            and retained.evidence == selected_preview.evidence
        )
        if len(matches) != 1:
            raise ValueError(
                "RadioReference mapping requires the exact retained observation "
                "associated with the selected preview."
            )

        target = selected_preview.target
        command = target.record.command
        if field is RadioReferenceFavoritesMappedField.NAME:
            if command == "C-Freq":
                return radioreference_favorites_frequency_name_mapping(target, observation)
            if command == "TGID":
                return radioreference_favorites_talkgroup_name_mapping(target, observation)
        elif (
            field is RadioReferenceFavoritesMappedField.FREQUENCY
            and command == "C-Freq"
        ):
            return radioreference_favorites_frequency_mapping(target, observation)
        elif (
            field is RadioReferenceFavoritesMappedField.TALKGROUP_DECIMAL
            and command == "TGID"
        ):
            return radioreference_favorites_talkgroup_decimal_mapping(target, observation)
        raise ValueError("RadioReference field is not supported for the selected target.")

    def plan_record_import(
        self,
        refresh_result: FavoritesExternalRefreshResult,
        selected_preview: FavoritesExternalRecordPreview,
        anchor: FavoritesRecordTarget,
        template: FavoritesSourceRecord,
        bindings: tuple[FavoritesExternalFieldBinding, ...],
    ) -> FavoritesExternalRefreshRecordImportPlan:
        self._require_current_refresh(refresh_result)
        return plan_favorites_external_refresh_record_import(
            refresh_result, selected_preview, anchor, template, bindings
        )

    def execute_record_mutation(
        self,
        plan: FavoritesExternalRefreshRecordMutationPlan,
        executor: FavoritesExternalRefreshRecordMutationExecutor,
    ) -> FavoritesExternalRefreshRecordMutationResult:
        return execute_favorites_external_refresh_record_mutation(
            plan, self._lifecycle, executor
        )

    def plan_record_delete(
        self,
        refresh_result: FavoritesExternalRefreshResult,
        selected_preview: FavoritesExternalRecordPreview,
    ) -> FavoritesExternalRefreshRecordDeletePlan:
        self._require_current_refresh(refresh_result)
        return plan_favorites_external_refresh_record_delete(
            refresh_result, selected_preview
        )

    def plan_record_keep_local(
        self,
        refresh_result: FavoritesExternalRefreshResult,
        selected_preview: FavoritesExternalRecordPreview,
    ) -> FavoritesExternalRefreshDetachPlan:
        self._require_current_refresh(refresh_result)
        return plan_favorites_external_refresh_record_keep_local(
            refresh_result, selected_preview
        )

    def plan_detach(
        self,
        refresh_result: FavoritesExternalRefreshResult,
        selected_preview: FavoritesExternalRecordPreview,
        scope: FavoritesExternalRefreshDetachScope,
        *,
        field_name: str | None = None,
    ) -> FavoritesExternalRefreshDetachPlan:
        self._require_current_refresh(refresh_result)
        return plan_favorites_external_refresh_detach(
            refresh_result, selected_preview, scope, field_name=field_name
        )

    def execute_detach(
        self,
        plan: FavoritesExternalRefreshDetachPlan,
    ) -> FavoritesExternalRefreshDetachResult:
        return execute_favorites_external_refresh_detach(plan, self._lifecycle)


@dataclass(frozen=True, slots=True)
class RadioReferenceAssistedSynchronizationSourceFactory:
    """Retain non-secret configuration for the production RR source chain."""

    configuration: RadioReferenceConfiguration
    request_plan: RadioReferenceObservationRequestPlan
    exchange_factory: RadioReferenceHttpsSoapExchangeFactory
    secret_resolver: RadioReferenceSecretResolver | None = field(
        default=None,
        repr=False,
    )

    def __post_init__(self) -> None:
        if not isinstance(self.configuration, RadioReferenceConfiguration):
            raise TypeError("RadioReference source factory requires exact configuration.")
        if not isinstance(self.request_plan, RadioReferenceObservationRequestPlan):
            raise TypeError("RadioReference source factory requires an exact request plan.")
        if not isinstance(
            self.exchange_factory, RadioReferenceHttpsSoapExchangeFactory
        ):
            raise TypeError(
                "RadioReference source factory requires the HTTPS exchange factory."
            )
        if self.secret_resolver is not None and not callable(self.secret_resolver):
            raise TypeError("RadioReference source factory secret resolver must be callable.")

    def __call__(self) -> RadioReferenceSource:
        session_factory = RadioReferenceObservationSessionFactory(
            plan=self.request_plan,
            exchange_factory=self.exchange_factory,
        )
        if self.secret_resolver is None:
            return RadioReferenceSource(self.configuration, session_factory)
        return RadioReferenceSource(
            self.configuration,
            session_factory,
            secret_resolver=self.secret_resolver,
        )


__all__ = [
    "FavoritesExternalAssistedSynchronizationService",
    "RadioReferenceAssistedSynchronizationSourceFactory",
    "RadioReferenceFavoritesMappedField",
]
