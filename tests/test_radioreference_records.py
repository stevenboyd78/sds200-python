from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime
from decimal import Decimal

import pytest

import sds200
from sds200 import (
    RADIOREFERENCE_PROGRAMMING_OPERATION_CONTRACTS,
    RADIOREFERENCE_SOAP_ENCODING_STYLE,
    RADIOREFERENCE_SOAP_NAMESPACE,
    RADIOREFERENCE_WSDL_EVIDENCE_SHA256,
    RadioReferenceFrequency,
    RadioReferenceRectangle,
    RadioReferenceSearchFrequencyResult,
    RadioReferenceTag,
    RadioReferenceTalkgroup,
    RadioReferenceTrunkBandplan,
    RadioReferenceTrunkFleetmap,
    RadioReferenceTrunkSite,
    RadioReferenceTrunkSiteFrequency,
    RadioReferenceTrunkSiteLicense,
    RadioReferenceTrunkSystem,
    RadioReferenceTrunkSystemId,
    RadioReferenceWsdlOperation,
    radioreference_operation_contract,
)


def _timestamp() -> datetime:
    # The WSDL says xsd:dateTime but does not require timezone evidence here.
    return datetime(2026, 8, 13, 9, 21, 4)


def _tag() -> RadioReferenceTag:
    return RadioReferenceTag(tag_id=2, description="Fire Dispatch")


def _rectangle() -> RadioReferenceRectangle:
    return RadioReferenceRectangle(
        northwest_latitude=Decimal("40.1"),
        northwest_longitude=Decimal("-105.2"),
        southeast_latitude=Decimal("39.9"),
        southeast_longitude=Decimal("-104.9"),
    )


def _frequency() -> RadioReferenceFrequency:
    return RadioReferenceFrequency(
        frequency_id=101,
        output_frequency=Decimal("155.1000"),
        input_frequency=Decimal("0"),
        callsign="WXYZ123",
        description="Dispatch",
        alpha_tag="Dispatch",
        tone="123.0 PL",
        color_code="",
        talkgroup="",
        slot="",
        mode="FMN",
        encryption=0,
        class_code="PW",
        tags=(_tag(),),
        subcategory_id=7,
        sort=10,
        last_updated=_timestamp(),
    )


def test_radioreference_wsdl_evidence_constants_are_exact() -> None:
    assert RADIOREFERENCE_SOAP_NAMESPACE == "http://api.radioreference.com/soap2"
    assert (
        RADIOREFERENCE_SOAP_ENCODING_STYLE
        == "http://schemas.xmlsoap.org/soap/encoding/"
    )
    assert (
        RADIOREFERENCE_WSDL_EVIDENCE_SHA256
        == "1bb8090cf6415e429eb432dd964b1d26164af7eb2240a8b6d345007821d12f33"
    )


def test_programming_operation_contracts_cover_reviewed_subset() -> None:
    assert len(RADIOREFERENCE_PROGRAMMING_OPERATION_CONTRACTS) == 19
    assert {
        contract.operation
        for contract in RADIOREFERENCE_PROGRAMMING_OPERATION_CONTRACTS
    } == set(RadioReferenceWsdlOperation)
    assert all(
        contract.authenticated
        for contract in RADIOREFERENCE_PROGRAMMING_OPERATION_CONTRACTS
    )
    assert len(
        {
            contract.operation
            for contract in RADIOREFERENCE_PROGRAMMING_OPERATION_CONTRACTS
        }
    ) == len(RADIOREFERENCE_PROGRAMMING_OPERATION_CONTRACTS)


@pytest.mark.parametrize(
    ("operation", "request_parts", "response_type"),
    (
        (
            RadioReferenceWsdlOperation.GET_COUNTRY_INFO,
            (("coid", "xsd:int"), ("authInfo", "tns:authInfo")),
            "tns:CountryInfo",
        ),
        (
            RadioReferenceWsdlOperation.GET_COUNTY_FREQUENCIES_BY_TAG,
            (
                ("ctid", "xsd:int"),
                ("tag", "xsd:int"),
                ("authInfo", "tns:authInfo"),
            ),
            "tns:Freqs",
        ),
        (
            RadioReferenceWsdlOperation.SEARCH_METRO_FREQUENCY,
            (
                ("mid", "xsd:int"),
                ("freq", "xsd:decimal"),
                ("tone", "xsd:string"),
                ("authInfo", "tns:authInfo"),
            ),
            "tns:searchFreqResults",
        ),
        (
            RadioReferenceWsdlOperation.GET_TRUNKED_TALKGROUPS,
            (
                ("sid", "xsd:int"),
                ("tgCid", "xsd:int"),
                ("tgTag", "xsd:int"),
                ("tgDec", "xsd:int"),
                ("authInfo", "tns:authInfo"),
            ),
            "tns:Talkgroups",
        ),
    ),
)
def test_operation_contract_preserves_exact_reviewed_request_parts(
    operation: RadioReferenceWsdlOperation,
    request_parts: tuple[tuple[str, str], ...],
    response_type: str,
) -> None:
    contract = radioreference_operation_contract(operation)

    assert tuple(
        (parameter.name, parameter.type_name)
        for parameter in contract.request_parameters
    ) == request_parts
    assert contract.response_type == response_type
    assert contract.soap_action == f"{RADIOREFERENCE_SOAP_NAMESPACE}#{operation.value}"


def test_operation_lookup_requires_typed_operation() -> None:
    with pytest.raises(TypeError):
        radioreference_operation_contract("getCountryInfo")  # type: ignore[arg-type]


def test_provider_frequency_is_immutable_and_preserves_exact_provider_evidence() -> None:
    frequency = _frequency()

    assert frequency.frequency_id == 101
    assert frequency.output_frequency == Decimal("155.1000")
    assert frequency.input_frequency == Decimal("0")
    assert frequency.alpha_tag == "Dispatch"
    assert frequency.tone == "123.0 PL"
    assert frequency.mode == "FMN"
    assert frequency.encryption == 0
    assert frequency.tags == (_tag(),)
    assert frequency.last_updated == _timestamp()

    with pytest.raises(FrozenInstanceError):
        frequency.mode = "FM"  # type: ignore[misc]


def test_provider_dto_allows_negative_xsd_int_without_inventing_id_semantics() -> None:
    tag = RadioReferenceTag(tag_id=-1, description="synthetic")

    assert tag.tag_id == -1


@pytest.mark.parametrize("bad_id", (True, 1.0, "1"))
def test_provider_dto_rejects_non_xsd_int_types(bad_id: object) -> None:
    with pytest.raises(TypeError):
        RadioReferenceTag(
            tag_id=bad_id,  # type: ignore[arg-type]
            description="synthetic",
        )


@pytest.mark.parametrize("bad_id", (-(2**31) - 1, 2**31))
def test_provider_dto_rejects_values_outside_xsd_int_range(bad_id: int) -> None:
    with pytest.raises(ValueError):
        RadioReferenceTag(tag_id=bad_id, description="synthetic")


def test_provider_decimal_fields_require_decimal_not_float() -> None:
    with pytest.raises(TypeError):
        RadioReferenceRectangle(
            northwest_latitude=40.1,  # type: ignore[arg-type]
            northwest_longitude=Decimal("-105.2"),
            southeast_latitude=Decimal("39.9"),
            southeast_longitude=Decimal("-104.9"),
        )


def test_provider_string_fields_preserve_empty_and_padded_values() -> None:
    tag = RadioReferenceTag(tag_id=1, description=" provider value ")

    assert tag.description == " provider value "
    assert RadioReferenceTag(tag_id=2, description="").description == ""


def test_provider_tuple_fields_reject_mutable_lists() -> None:
    frequency = _frequency()

    with pytest.raises(TypeError):
        RadioReferenceFrequency(
            frequency_id=frequency.frequency_id,
            output_frequency=frequency.output_frequency,
            input_frequency=frequency.input_frequency,
            callsign=frequency.callsign,
            description=frequency.description,
            alpha_tag=frequency.alpha_tag,
            tone=frequency.tone,
            color_code=frequency.color_code,
            talkgroup=frequency.talkgroup,
            slot=frequency.slot,
            mode=frequency.mode,
            encryption=frequency.encryption,
            class_code=frequency.class_code,
            tags=[_tag()],  # type: ignore[arg-type]
            subcategory_id=frequency.subcategory_id,
            sort=frequency.sort,
            last_updated=frequency.last_updated,
        )


def test_search_frequency_result_does_not_invent_frequency_record_identity() -> None:
    result = RadioReferenceSearchFrequencyResult(
        output_frequency=Decimal("155.1000"),
        input_frequency=Decimal("0"),
        callsign="",
        description="Synthetic",
        alpha_tag="Synthetic",
        tone="",
        color_code="",
        talkgroup="",
        slot="",
        mode="FMN",
        class_code="",
        tags=(),
        subcategory_id=7,
        system_id=0,
        agency_id=2,
        county_id=3,
    )

    assert not hasattr(result, "frequency_id")
    assert not hasattr(result, "last_updated")
    assert result.subcategory_id == 7


def test_talkgroup_preserves_provider_timestamp_without_revision_semantics() -> None:
    talkgroup = RadioReferenceTalkgroup(
        talkgroup_id=200,
        decimal=12345,
        subfleet="",
        ltr=False,
        slot="",
        description="Operations",
        alpha_tag="Ops",
        mode="D",
        encryption=0,
        tags=(_tag(),),
        category_id=30,
        sort=1,
        date=_timestamp(),
    )

    assert talkgroup.talkgroup_id == 200
    assert talkgroup.date == _timestamp()
    assert not hasattr(talkgroup, "revision")


def test_trunked_system_preserves_request_independent_response_shape() -> None:
    system = RadioReferenceTrunkSystem(
        name="Synthetic System",
        system_type=1,
        flavor=2,
        voice=3,
        city="Synthetic City",
        county_ids=(10,),
        state_ids=(20,),
        country="US",
        latitude=Decimal("40"),
        longitude=Decimal("-105"),
        range=Decimal("25"),
        rectangles=(_rectangle(),),
        last_updated=_timestamp(),
        system_ids=(
            RadioReferenceTrunkSystemId(
                system_id="123",
                ct="P25",
                wacn="BEE00",
                model="",
            ),
        ),
        bandplan=(
            RadioReferenceTrunkBandplan(
                base="851.0000",
                spacing="0.0125",
                offset="0",
            ),
        ),
        fleetmap=RadioReferenceTrunkFleetmap(
            block_0="",
            block_1="",
            block_2="",
            block_3="",
            block_4="",
            block_5="",
            block_6="",
            block_7="",
        ),
    )

    assert system.name == "Synthetic System"
    assert system.county_ids == (10,)
    assert not hasattr(system, "system_id")


def test_trunked_site_preserves_nested_provider_arrays() -> None:
    site = RadioReferenceTrunkSite(
        site_id=11,
        system_id=22,
        site_number=1,
        description="Site",
        zone_number=0,
        zone_description="",
        rfss=1,
        nac="123",
        ran=0,
        neighbors="",
        location="",
        county_id=3,
        county="Synthetic",
        modulation="",
        notes="",
        latitude=Decimal("40"),
        longitude=Decimal("-105"),
        range=Decimal("15"),
        rectangles=(_rectangle(),),
        splinter=0,
        rebanded=0,
        tdma_control_channel=1,
        licenses=(RadioReferenceTrunkSiteLicense(license="WXYZ123"),),
        frequencies=(
            RadioReferenceTrunkSiteFrequency(
                logical_channel_number=1,
                frequency=Decimal("851.0125"),
                use="c",
                color_code="",
                channel_id="",
            ),
        ),
        bandplan=(
            RadioReferenceTrunkBandplan(
                base="851.0000",
                spacing="0.0125",
                offset="0",
            ),
        ),
    )

    assert site.site_id == 11
    assert site.system_id == 22
    assert site.frequencies[0].frequency == Decimal("851.0125")
    assert site.licenses[0].license == "WXYZ123"


def test_provider_record_types_are_separate_from_normalized_external_observations() -> None:
    assert not isinstance(_frequency(), sds200.FavoritesExternalRecordObservation)


@pytest.mark.parametrize(
    "name",
    (
        "RADIOREFERENCE_PROGRAMMING_OPERATION_CONTRACTS",
        "RADIOREFERENCE_SOAP_ENCODING_STYLE",
        "RADIOREFERENCE_SOAP_NAMESPACE",
        "RADIOREFERENCE_WSDL_EVIDENCE_SHA256",
        "RadioReferenceAgency",
        "RadioReferenceAgencyInfo",
        "RadioReferenceCategory",
        "RadioReferenceCountryInfo",
        "RadioReferenceCounty",
        "RadioReferenceCountyInfo",
        "RadioReferenceFrequency",
        "RadioReferenceMode",
        "RadioReferenceRectangle",
        "RadioReferenceSearchFrequencyResult",
        "RadioReferenceState",
        "RadioReferenceStateInfo",
        "RadioReferenceSubcategory",
        "RadioReferenceTag",
        "RadioReferenceTalkgroup",
        "RadioReferenceTalkgroupCategory",
        "RadioReferenceTrunkBandplan",
        "RadioReferenceTrunkFlavor",
        "RadioReferenceTrunkFleetmap",
        "RadioReferenceTrunkListEntry",
        "RadioReferenceTrunkSite",
        "RadioReferenceTrunkSiteFrequency",
        "RadioReferenceTrunkSiteLicense",
        "RadioReferenceTrunkSystem",
        "RadioReferenceTrunkSystemId",
        "RadioReferenceTrunkType",
        "RadioReferenceTrunkVoice",
        "RadioReferenceWsdlOperation",
        "RadioReferenceWsdlOperationContract",
        "RadioReferenceWsdlParameter",
        "radioreference_operation_contract",
    ),
)
def test_provider_record_contract_exports_are_public(name: str) -> None:
    assert name in sds200.__all__
    assert hasattr(sds200, name)
