from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

import sds200
from sds200 import (
    FavoritesExternalSourceIdentity,
    RadioReferenceObservationRequestPlan,
    RadioReferenceWsdlOperation,
    radioreference_operation_contract,
)


def _source(
    *,
    provider: str = "radioreference",
    dataset: str = "synthetic-subcategory",
) -> FavoritesExternalSourceIdentity:
    return FavoritesExternalSourceIdentity(
        provider=provider,
        dataset=dataset,
    )


@pytest.mark.parametrize(
    ("operation", "parameters"),
    (
        (
            RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
            (("scid", 7),),
        ),
        (
            RadioReferenceWsdlOperation.GET_COUNTY_FREQUENCIES_BY_TAG,
            (("ctid", 3), ("tag", 2)),
        ),
        (
            RadioReferenceWsdlOperation.GET_AGENCY_FREQUENCIES_BY_TAG,
            (("aid", 9), ("tag", 2)),
        ),
        (
            RadioReferenceWsdlOperation.GET_TRUNKED_TALKGROUPS,
            (("sid", 22), ("tgCid", 0), ("tgTag", 0), ("tgDec", 0)),
        ),
    ),
)
def test_observation_request_plan_accepts_reviewed_operation_contract(
    operation: RadioReferenceWsdlOperation,
    parameters: tuple[tuple[str, object], ...],
) -> None:
    source = _source()
    plan = RadioReferenceObservationRequestPlan(
        source=source,
        operation=operation,
        parameters=parameters,
    )

    assert plan.source is source
    assert plan.operation is operation
    assert plan.parameters == parameters
    assert plan.soap_action == radioreference_operation_contract(
        operation
    ).soap_action


def test_observation_request_plan_is_immutable() -> None:
    plan = RadioReferenceObservationRequestPlan(
        source=_source(),
        operation=RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
        parameters=(("scid", 7),),
    )

    with pytest.raises(FrozenInstanceError):
        plan.parameters = (("scid", 8),)  # type: ignore[misc]


def test_observation_request_plan_returns_fresh_parameter_mapping() -> None:
    plan = RadioReferenceObservationRequestPlan(
        source=_source(),
        operation=RadioReferenceWsdlOperation.GET_COUNTY_FREQUENCIES_BY_TAG,
        parameters=(("ctid", 3), ("tag", 2)),
    )

    first = plan.parameter_mapping()
    second = plan.parameter_mapping()

    assert first == {"ctid": 3, "tag": 2}
    assert second == first
    assert second is not first

    first["ctid"] = 99
    assert plan.parameters == (("ctid", 3), ("tag", 2))


def test_observation_request_plan_requires_radioreference_source() -> None:
    with pytest.raises(
        ValueError,
        match="source provider must be radioreference",
    ):
        RadioReferenceObservationRequestPlan(
            source=_source(provider="other-provider"),
            operation=RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
            parameters=(("scid", 7),),
        )


def test_observation_request_plan_requires_source_identity_type() -> None:
    with pytest.raises(
        TypeError,
        match="FavoritesExternalSourceIdentity",
    ):
        RadioReferenceObservationRequestPlan(
            source=object(),  # type: ignore[arg-type]
            operation=RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
            parameters=(("scid", 7),),
        )


def test_observation_request_plan_requires_typed_operation() -> None:
    with pytest.raises(
        TypeError,
        match="RadioReferenceWsdlOperation",
    ):
        RadioReferenceObservationRequestPlan(
            source=_source(),
            operation="getSubcatFreqs",  # type: ignore[arg-type]
            parameters=(("scid", 7),),
        )


@pytest.mark.parametrize(
    "operation",
    (
        RadioReferenceWsdlOperation.GET_COUNTRY_INFO,
        RadioReferenceWsdlOperation.GET_COUNTY_INFO,
        RadioReferenceWsdlOperation.SEARCH_COUNTY_FREQUENCY,
        RadioReferenceWsdlOperation.GET_TRUNKED_SYSTEM_DETAILS,
    ),
)
def test_observation_request_plan_rejects_unmapped_operations(
    operation: RadioReferenceWsdlOperation,
) -> None:
    with pytest.raises(
        ValueError,
        match="no reviewed observation request plan",
    ):
        RadioReferenceObservationRequestPlan(
            source=_source(),
            operation=operation,
            parameters=(),
        )


def test_observation_request_plan_requires_exact_tuple_container() -> None:
    with pytest.raises(TypeError, match="immutable tuple"):
        RadioReferenceObservationRequestPlan(
            source=_source(),
            operation=RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
            parameters=[("scid", 7)],  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "parameters",
    (
        (["scid", 7],),
        (("scid",),),
        (("scid", 7, 8),),
    ),
)
def test_observation_request_plan_requires_exact_parameter_pairs(
    parameters: object,
) -> None:
    with pytest.raises(TypeError, match=r"\(name, value\) tuples"):
        RadioReferenceObservationRequestPlan(
            source=_source(),
            operation=RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
            parameters=parameters,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    "parameters",
    (
        (),
        (("tag", 2), ("ctid", 3)),
        (("ctid", 3),),
        (("ctid", 3), ("tag", 2), ("extra", 4)),
    ),
)
def test_observation_request_plan_requires_exact_wsdl_parameter_order(
    parameters: tuple[tuple[str, object], ...],
) -> None:
    with pytest.raises(ValueError, match="exactly match reviewed WSDL order"):
        RadioReferenceObservationRequestPlan(
            source=_source(),
            operation=RadioReferenceWsdlOperation.GET_COUNTY_FREQUENCIES_BY_TAG,
            parameters=parameters,
        )


def test_observation_request_plan_rejects_duplicate_parameter_names() -> None:
    with pytest.raises(ValueError, match="duplicate names"):
        RadioReferenceObservationRequestPlan(
            source=_source(),
            operation=RadioReferenceWsdlOperation.GET_COUNTY_FREQUENCIES_BY_TAG,
            parameters=(("ctid", 3), ("ctid", 2)),
        )


@pytest.mark.parametrize("value", (True, 7.0, "7", None))
def test_observation_request_plan_requires_exact_xsd_int_type(
    value: object,
) -> None:
    with pytest.raises(TypeError, match="xsd:int-compatible integer"):
        RadioReferenceObservationRequestPlan(
            source=_source(),
            operation=RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
            parameters=(("scid", value),),
        )


@pytest.mark.parametrize("value", (-(2**31) - 1, 2**31))
def test_observation_request_plan_rejects_xsd_int_outside_range(
    value: int,
) -> None:
    with pytest.raises(ValueError, match="outside the xsd:int range"):
        RadioReferenceObservationRequestPlan(
            source=_source(),
            operation=RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
            parameters=(("scid", value),),
        )


def test_observation_request_plan_is_secret_free_by_shape() -> None:
    plan = RadioReferenceObservationRequestPlan(
        source=_source(),
        operation=RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
        parameters=(("scid", 7),),
    )

    assert not hasattr(plan, "application_key")
    assert not hasattr(plan, "password")
    assert not hasattr(plan, "request")
    assert not hasattr(plan, "request_bytes")


def test_observation_request_plan_symbol_is_package_export() -> None:
    assert (
        sds200.RadioReferenceObservationRequestPlan
        is RadioReferenceObservationRequestPlan
    )
    assert "RadioReferenceObservationRequestPlan" in sds200.__all__
