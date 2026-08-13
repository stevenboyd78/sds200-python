from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

import sds200
from sds200 import (
    RADIOREFERENCE_SOAP_DEFAULT_MAX_DOCUMENT_BYTES,
    RADIOREFERENCE_SOAP_DEFAULT_MAX_ELEMENTS,
    RADIOREFERENCE_SOAP_DEFAULT_MAX_REFERENCE_DEPTH,
    RADIOREFERENCE_SOAP_DEFAULT_MAX_REFERENCES,
    RadioReferenceAgencyInfo,
    RadioReferenceCountryInfo,
    RadioReferenceCountyInfo,
    RadioReferenceError,
    RadioReferenceErrorReason,
    RadioReferenceFrequency,
    RadioReferenceMode,
    RadioReferenceSoapDecoder,
    RadioReferenceStateInfo,
    RadioReferenceTag,
    RadioReferenceTalkgroup,
    RadioReferenceTalkgroupCategory,
    RadioReferenceTrunkFlavor,
    RadioReferenceTrunkSite,
    RadioReferenceTrunkSystem,
    RadioReferenceTrunkType,
    RadioReferenceTrunkVoice,
    RadioReferenceWsdlOperation,
)

SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
ENC = "http://schemas.xmlsoap.org/soap/encoding/"
XSI = "http://www.w3.org/2001/XMLSchema-instance"
XSD = "http://www.w3.org/2001/XMLSchema"
RR = "http://api.radioreference.com/soap2"


def _array_attributes(item_type: str, count: int) -> str:
    return (
        ' xsi:type="enc:Array" '
        f'enc:arrayType="tns:{item_type}[{count}]"'
    )


def _array_member(
    name: str,
    item_type: str,
    items: str = "",
    *,
    count: int = 0,
) -> str:
    return (
        f"<{name}{_array_attributes(item_type, count)}>"
        f"{items}"
        f"</{name}>"
    )


def _soap(
    operation: RadioReferenceWsdlOperation,
    returned: str,
    *,
    return_attributes: str = "",
    extra_body: str = "",
) -> bytes:
    return (
        f'<soap:Envelope xmlns:soap="{SOAP}" xmlns:enc="{ENC}" '
        f'xmlns:xsi="{XSI}" xmlns:xsd="{XSD}" xmlns:tns="{RR}">'
        "<soap:Body>"
        f"<tns:{operation.value}Response>"
        f"<return{return_attributes}>{returned}</return>"
        f"</tns:{operation.value}Response>"
        f"{extra_body}"
        "</soap:Body>"
        "</soap:Envelope>"
    ).encode()


def _array_response(
    operation: RadioReferenceWsdlOperation,
    item_type: str,
    items: str = "",
    *,
    count: int = 0,
) -> bytes:
    return _soap(
        operation,
        items,
        return_attributes=_array_attributes(item_type, count),
    )


def _item(body: str, *, type_name: str | None = None) -> str:
    type_attribute = (
        "" if type_name is None else f' xsi:type="tns:{type_name}"'
    )
    return f"<item{type_attribute}>{body}</item>"


def _rectangle_item() -> str:
    return _item(
        "<nw_lat>40.1</nw_lat>"
        "<nw_lon>-105.2</nw_lon>"
        "<se_lat>39.9</se_lat>"
        "<se_lon>-104.9</se_lon>",
        type_name="Rectangle",
    )


def _rectangles_member(name: str = "rectangles") -> str:
    return _array_member(
        name,
        "Rectangle",
        _rectangle_item(),
        count=1,
    )


def _tag_item(description: str = " Fire Dispatch ") -> str:
    return _item(
        "<tagId>2</tagId>"
        f"<tagDescr>{description}</tagDescr>",
        type_name="tag",
    )


def _tags_member(name: str = "tags") -> str:
    return _array_member(
        name,
        "tag",
        _tag_item(),
        count=1,
    )


def _frequency_item(
    *,
    output: str = "155.1000",
    timestamp: str = "2026-08-13T09:21:04Z",
) -> str:
    return _item(
        "<fid>101</fid>"
        f"<out>{output}</out>"
        "<in>0</in>"
        "<callsign>WXYZ123</callsign>"
        "<descr>Dispatch</descr>"
        "<alpha>Dispatch</alpha>"
        "<tone>123.0 PL</tone>"
        "<colorCode></colorCode>"
        "<tg></tg>"
        "<slot></slot>"
        "<mode>FMN</mode>"
        "<enc>0</enc>"
        "<class>PW</class>"
        f"{_tags_member()}"
        "<scid>7</scid>"
        "<sort>10</sort>"
        f"<lastUpdated>{timestamp}</lastUpdated>",
        type_name="freq",
    )


def _bandplan_member(name: str = "bandplan") -> str:
    item = _item(
        "<base>851.0000</base>"
        "<spacing>0.0125</spacing>"
        "<offset>0</offset>",
        type_name="trsBandplanDef",
    )
    return _array_member(
        name,
        "trsBandplanDef",
        item,
        count=1,
    )


def _trunk_site_item() -> str:
    site_frequency = _item(
        "<lcn>1</lcn>"
        "<freq>851.0125</freq>"
        "<use>c</use>"
        "<colorCode></colorCode>"
        "<ch_id></ch_id>",
        type_name="TrsSiteFreq",
    )
    license_item = _item(
        "<license>WXYZ123</license>",
        type_name="TrsSiteLicense",
    )
    return _item(
        "<siteId>11</siteId>"
        "<sid>22</sid>"
        "<siteNumber>1</siteNumber>"
        "<siteDescr>Site</siteDescr>"
        "<zoneNumber>0</zoneNumber>"
        "<zoneDescr></zoneDescr>"
        "<rfss>1</rfss>"
        "<nac>123</nac>"
        "<ran>0</ran>"
        "<siteNeighbors></siteNeighbors>"
        "<siteLocation></siteLocation>"
        "<siteCtid>3</siteCtid>"
        "<siteCt>Synthetic</siteCt>"
        "<siteModulation></siteModulation>"
        "<siteNotes></siteNotes>"
        "<lat>40</lat>"
        "<lon>-105</lon>"
        "<range>15</range>"
        f"{_rectangles_member()}"
        "<splinter>0</splinter>"
        "<rebanded>0</rebanded>"
        "<tdma_cc>1</tdma_cc>"
        f"{_array_member('siteLicenses', 'TrsSiteLicense', license_item, count=1)}"
        f"{_array_member('siteFreqs', 'TrsSiteFreq', site_frequency, count=1)}"
        f"{_bandplan_member()}",
        type_name="TrsSite",
    )


def test_decoder_defaults_are_bounded_and_immutable() -> None:
    decoder = RadioReferenceSoapDecoder()

    assert (
        decoder.max_document_bytes
        == RADIOREFERENCE_SOAP_DEFAULT_MAX_DOCUMENT_BYTES
    )
    assert decoder.max_elements == RADIOREFERENCE_SOAP_DEFAULT_MAX_ELEMENTS
    assert (
        decoder.max_references
        == RADIOREFERENCE_SOAP_DEFAULT_MAX_REFERENCES
    )
    assert (
        decoder.max_reference_depth
        == RADIOREFERENCE_SOAP_DEFAULT_MAX_REFERENCE_DEPTH
    )

    with pytest.raises(FrozenInstanceError):
        decoder.max_elements = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    "kwargs",
    (
        {"max_document_bytes": 0},
        {"max_elements": 0},
        {"max_references": 0},
        {"max_reference_depth": 0},
        {"max_elements": True},
    ),
)
def test_decoder_rejects_invalid_limits(kwargs: dict[str, object]) -> None:
    with pytest.raises((TypeError, ValueError)):
        RadioReferenceSoapDecoder(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("operation", "item_type"),
    (
        (
            RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
            "freq",
        ),
        (
            RadioReferenceWsdlOperation.GET_COUNTY_FREQUENCIES_BY_TAG,
            "freq",
        ),
        (
            RadioReferenceWsdlOperation.GET_AGENCY_FREQUENCIES_BY_TAG,
            "freq",
        ),
        (
            RadioReferenceWsdlOperation.SEARCH_COUNTY_FREQUENCY,
            "searchFreqResult",
        ),
        (
            RadioReferenceWsdlOperation.SEARCH_STATE_FREQUENCY,
            "searchFreqResult",
        ),
        (
            RadioReferenceWsdlOperation.SEARCH_METRO_FREQUENCY,
            "searchFreqResult",
        ),
        (
            RadioReferenceWsdlOperation.GET_TRUNKED_SYSTEM_SITES,
            "TrsSite",
        ),
        (
            RadioReferenceWsdlOperation.GET_TRUNKED_TALKGROUP_CATEGORIES,
            "TalkgroupCat",
        ),
        (
            RadioReferenceWsdlOperation.GET_TRUNKED_TALKGROUPS,
            "Talkgroup",
        ),
        (RadioReferenceWsdlOperation.GET_TAG, "tag"),
        (RadioReferenceWsdlOperation.GET_MODE, "mode"),
        (RadioReferenceWsdlOperation.GET_TRUNKED_TYPE, "trsTypeDef"),
        (RadioReferenceWsdlOperation.GET_TRUNKED_FLAVOR, "trsFlavorDef"),
        (RadioReferenceWsdlOperation.GET_TRUNKED_VOICE, "trsVoiceDef"),
    ),
)
def test_all_reviewed_array_operations_accept_empty_schema_arrays(
    operation: RadioReferenceWsdlOperation,
    item_type: str,
) -> None:
    decoded = RadioReferenceSoapDecoder().decode(
        operation,
        _array_response(operation, item_type),
    )

    assert decoded == ()


def test_decode_tag_array_preserves_provider_string_evidence() -> None:
    response = _array_response(
        RadioReferenceWsdlOperation.GET_TAG,
        "tag",
        _tag_item(),
        count=1,
    )

    decoded = RadioReferenceSoapDecoder().decode(
        RadioReferenceWsdlOperation.GET_TAG,
        response,
    )

    assert decoded == (
        RadioReferenceTag(
            tag_id=2,
            description=" Fire Dispatch ",
        ),
    )


def test_decode_frequency_array_preserves_decimal_and_timestamp() -> None:
    response = _array_response(
        RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
        "freq",
        _frequency_item(),
        count=1,
    )

    decoded = RadioReferenceSoapDecoder().decode(
        RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
        response,
    )

    assert isinstance(decoded, tuple)
    assert len(decoded) == 1
    frequency = decoded[0]
    assert isinstance(frequency, RadioReferenceFrequency)
    assert frequency.frequency_id == 101
    assert frequency.output_frequency == Decimal("155.1000")
    assert frequency.input_frequency == Decimal("0")
    assert frequency.tags == (
        RadioReferenceTag(
            tag_id=2,
            description=" Fire Dispatch ",
        ),
    )
    assert frequency.last_updated == datetime(
        2026,
        8,
        13,
        9,
        21,
        4,
        tzinfo=UTC,
    )


def test_decode_country_info_with_nested_arrays() -> None:
    agency_item = _item(
        "<aid>7</aid><aName>Agency</aName><aType>2</aType>",
        type_name="Agency",
    )
    state_item = _item(
        "<stid>8</stid>"
        "<stateName>Colorado</stateName>"
        "<stateCode>CO</stateCode>",
        type_name="State",
    )
    response = _soap(
        RadioReferenceWsdlOperation.GET_COUNTRY_INFO,
        (
            "<coid>1</coid>"
            "<countryName>United States</countryName>"
            "<countryCode>US</countryCode>"
            f"{_array_member('agencyList', 'Agency', agency_item, count=1)}"
            f"{_array_member('stateList', 'State', state_item, count=1)}"
        ),
        return_attributes=' xsi:type="tns:CountryInfo"',
    )

    decoded = RadioReferenceSoapDecoder().decode(
        RadioReferenceWsdlOperation.GET_COUNTRY_INFO,
        response,
    )

    assert isinstance(decoded, RadioReferenceCountryInfo)
    assert decoded.country_id == 1
    assert decoded.agencies[0].agency_id == 7
    assert decoded.states[0].code == "CO"


def test_decode_state_info_covers_trunk_list_and_county_summaries() -> None:
    trunk_item = _item(
        "<sid>22</sid>"
        "<sName>System</sName>"
        "<sType>1</sType>"
        "<sFlavor>2</sFlavor>"
        "<sVoice>3</sVoice>"
        "<sCity>Denver</sCity>"
        "<lastUpdated>2026-08-13T09:21:04Z</lastUpdated>",
        type_name="TrsListDef",
    )
    county_item = _item(
        "<ctid>3</ctid>"
        "<countyName>County</countyName>"
        "<countyHeader>Header</countyHeader>",
        type_name="County",
    )
    response = _soap(
        RadioReferenceWsdlOperation.GET_STATE_INFO,
        (
            "<stid>8</stid>"
            "<stateName>Colorado</stateName>"
            "<stateEntityType>state</stateEntityType>"
            f"{_array_member('trsList', 'TrsListDef', trunk_item, count=1)}"
            f"{_array_member('agencyList', 'Agency')}"
            f"{_array_member('countyList', 'County', county_item, count=1)}"
        ),
        return_attributes=' xsi:type="tns:StateInfo"',
    )

    decoded = RadioReferenceSoapDecoder().decode(
        RadioReferenceWsdlOperation.GET_STATE_INFO,
        response,
    )

    assert isinstance(decoded, RadioReferenceStateInfo)
    assert decoded.trunked_systems[0].system_id == 22
    assert decoded.counties[0].county_id == 3


def test_decode_county_info_covers_categories_and_subcategories() -> None:
    subcategory_item = _item(
        "<scid>9</scid>"
        "<scName>Dispatch</scName>"
        "<lat>40</lat>"
        "<lon>-105</lon>"
        "<range>25</range>"
        f"{_rectangles_member()}"
        f"{_array_member('sids', 'sid')}",
        type_name="subcat",
    )
    category_item = _item(
        "<cid>4</cid>"
        "<cName>Public Safety</cName>"
        f"{_array_member('subcats', 'subcat', subcategory_item, count=1)}",
        type_name="cat",
    )
    response = _soap(
        RadioReferenceWsdlOperation.GET_COUNTY_INFO,
        (
            "<ctid>3</ctid>"
            "<countyName>County</countyName>"
            "<countyHeader>Header</countyHeader>"
            "<stid>8</stid>"
            "<fips>001</fips>"
            "<lastUpdated>2026-08-13T09:21:04Z</lastUpdated>"
            "<lat>40</lat>"
            "<lon>-105</lon>"
            "<range>25</range>"
            f"{_rectangles_member()}"
            f"{_array_member('cats', 'cat', category_item, count=1)}"
            f"{_array_member('trsList', 'TrsListDef')}"
            f"{_array_member('agencyList', 'Agency')}"
        ),
        return_attributes=' xsi:type="tns:CountyInfo"',
    )

    decoded = RadioReferenceSoapDecoder().decode(
        RadioReferenceWsdlOperation.GET_COUNTY_INFO,
        response,
    )

    assert isinstance(decoded, RadioReferenceCountyInfo)
    subcategory = decoded.categories[0].subcategories[0]
    assert subcategory.subcategory_id == 9
    assert subcategory.trunked_system_ids == ()


def test_decode_agency_info_preserves_local_datetime_evidence() -> None:
    response = _soap(
        RadioReferenceWsdlOperation.GET_AGENCY_INFO,
        (
            "<aid>7</aid>"
            "<agencyName>Agency</agencyName>"
            "<agencyType>County</agencyType>"
            "<ctid>3</ctid>"
            "<stid>8</stid>"
            "<lat>40</lat>"
            "<lon>-105</lon>"
            "<range>25</range>"
            f"{_rectangles_member()}"
            "<lastUpdated>2026-08-13T09:21:04</lastUpdated>"
            f"{_array_member('cats', 'cat')}"
        ),
        return_attributes=' xsi:type="tns:AgencyInfo"',
    )

    decoded = RadioReferenceSoapDecoder().decode(
        RadioReferenceWsdlOperation.GET_AGENCY_INFO,
        response,
    )

    assert isinstance(decoded, RadioReferenceAgencyInfo)
    assert decoded.last_updated == datetime(2026, 8, 13, 9, 21, 4)


def test_decode_search_frequency_result() -> None:
    item = _item(
        "<out>155.1000</out>"
        "<in>0</in>"
        "<callsign></callsign>"
        "<descr>Synthetic</descr>"
        "<alpha>Synthetic</alpha>"
        "<tone></tone>"
        "<colorCode></colorCode>"
        "<tg></tg>"
        "<slot></slot>"
        "<mode>FMN</mode>"
        "<class></class>"
        f"{_array_member('tags', 'tag')}"
        "<scid>7</scid>"
        "<sid>22</sid>"
        "<aid>7</aid>"
        "<ctid>3</ctid>",
        type_name="searchFreqResult",
    )
    response = _array_response(
        RadioReferenceWsdlOperation.SEARCH_COUNTY_FREQUENCY,
        "searchFreqResult",
        item,
        count=1,
    )

    decoded = RadioReferenceSoapDecoder().decode(
        RadioReferenceWsdlOperation.SEARCH_COUNTY_FREQUENCY,
        response,
    )

    assert isinstance(decoded, tuple)
    assert decoded[0].system_id == 22
    assert decoded[0].agency_id == 7


def test_decode_trunked_system_nested_provider_structures() -> None:
    county_item = _item("<ctid>3</ctid>", type_name="ctid")
    state_item = _item("<stid>8</stid>", type_name="stid")
    sysid_item = _item(
        "<sysid>123</sysid>"
        "<ct>P25</ct>"
        "<wacn>BEE00</wacn>"
        "<model></model>",
        type_name="trsSysidDef",
    )
    fleetmap = (
        '<fleetmap xsi:type="tns:TrsFleetmap">'
        "<b0></b0><b1></b1><b2></b2><b3></b3>"
        "<b4></b4><b5></b5><b6></b6><b7></b7>"
        "</fleetmap>"
    )
    response = _soap(
        RadioReferenceWsdlOperation.GET_TRUNKED_SYSTEM_DETAILS,
        (
            "<sName>Synthetic System</sName>"
            "<sType>1</sType>"
            "<sFlavor>2</sFlavor>"
            "<sVoice>3</sVoice>"
            "<sCity>Synthetic City</sCity>"
            f"{_array_member('sCounty', 'ctid', county_item, count=1)}"
            f"{_array_member('sState', 'stid', state_item, count=1)}"
            "<sCountry>US</sCountry>"
            "<lat>40</lat>"
            "<lon>-105</lon>"
            "<range>25</range>"
            f"{_rectangles_member()}"
            "<lastUpdated>2026-08-13T09:21:04-06:00</lastUpdated>"
            f"{_array_member('sysid', 'trsSysidDef', sysid_item, count=1)}"
            f"{_bandplan_member()}"
            f"{fleetmap}"
        ),
        return_attributes=' xsi:type="tns:Trs"',
    )

    decoded = RadioReferenceSoapDecoder().decode(
        RadioReferenceWsdlOperation.GET_TRUNKED_SYSTEM_DETAILS,
        response,
    )

    assert isinstance(decoded, RadioReferenceTrunkSystem)
    assert decoded.name == "Synthetic System"
    assert decoded.county_ids == (3,)
    assert decoded.state_ids == (8,)
    assert decoded.system_ids[0].wacn == "BEE00"
    assert decoded.last_updated.utcoffset() == timedelta(hours=-6)


def test_decode_trunk_sites_with_nested_arrays() -> None:
    response = _array_response(
        RadioReferenceWsdlOperation.GET_TRUNKED_SYSTEM_SITES,
        "TrsSite",
        _trunk_site_item(),
        count=1,
    )

    decoded = RadioReferenceSoapDecoder().decode(
        RadioReferenceWsdlOperation.GET_TRUNKED_SYSTEM_SITES,
        response,
    )

    assert isinstance(decoded, tuple)
    assert isinstance(decoded[0], RadioReferenceTrunkSite)
    assert decoded[0].frequencies[0].frequency == Decimal("851.0125")
    assert decoded[0].licenses[0].license == "WXYZ123"


def test_decode_talkgroup_category() -> None:
    item = _item(
        "<tgCid>30</tgCid>"
        "<sid>22</sid>"
        "<tgCname>Operations</tgCname>"
        "<tgSort>1</tgSort>"
        "<tgSortBy>2</tgSortBy>"
        "<lat>40</lat>"
        "<lon>-105</lon>"
        "<range>25</range>"
        f"{_rectangles_member()}"
        "<lastUpdated>2026-08-13T09:21:04Z</lastUpdated>",
        type_name="TalkgroupCat",
    )
    response = _array_response(
        RadioReferenceWsdlOperation.GET_TRUNKED_TALKGROUP_CATEGORIES,
        "TalkgroupCat",
        item,
        count=1,
    )

    decoded = RadioReferenceSoapDecoder().decode(
        RadioReferenceWsdlOperation.GET_TRUNKED_TALKGROUP_CATEGORIES,
        response,
    )

    assert isinstance(decoded, tuple)
    assert isinstance(decoded[0], RadioReferenceTalkgroupCategory)
    assert decoded[0].category_id == 30


def test_decode_talkgroup_supports_local_href_arrays() -> None:
    tags_target = (
        '<multiRef id="tags0" xsi:type="enc:Array" '
        'enc:arrayType="tns:tag[1]">'
        f"{_tag_item()}"
        "</multiRef>"
    )
    talkgroup = _item(
        "<tgId>20</tgId>"
        "<tgDec>12345</tgDec>"
        "<tgSubfleet></tgSubfleet>"
        "<tgLtr>1</tgLtr>"
        "<tgSlot></tgSlot>"
        "<tgDescr>Operations</tgDescr>"
        "<tgAlpha>Ops</tgAlpha>"
        "<tgMode>D</tgMode>"
        "<enc>0</enc>"
        '<tags href="#tags0" />'
        "<tgCid>30</tgCid>"
        "<tgSort>1</tgSort>"
        "<tgDate>2026-08-13T09:21:04+00:00</tgDate>",
        type_name="Talkgroup",
    )
    response = _soap(
        RadioReferenceWsdlOperation.GET_TRUNKED_TALKGROUPS,
        talkgroup,
        return_attributes=_array_attributes("Talkgroup", 1),
        extra_body=tags_target,
    )

    decoded = RadioReferenceSoapDecoder().decode(
        RadioReferenceWsdlOperation.GET_TRUNKED_TALKGROUPS,
        response,
    )

    assert isinstance(decoded, tuple)
    assert isinstance(decoded[0], RadioReferenceTalkgroup)
    assert decoded[0].ltr is True
    assert decoded[0].tags[0].description == " Fire Dispatch "
    assert decoded[0].date == datetime(
        2026,
        8,
        13,
        9,
        21,
        4,
        tzinfo=UTC,
    )


@pytest.mark.parametrize(
    ("operation", "item_type", "item", "expected_type"),
    (
        (
            RadioReferenceWsdlOperation.GET_MODE,
            "mode",
            "<item><mode>1</mode><modeName>FM</modeName></item>",
            RadioReferenceMode,
        ),
        (
            RadioReferenceWsdlOperation.GET_TRUNKED_TYPE,
            "trsTypeDef",
            "<item><sType>1</sType><sTypeDescr>P25</sTypeDescr></item>",
            RadioReferenceTrunkType,
        ),
        (
            RadioReferenceWsdlOperation.GET_TRUNKED_FLAVOR,
            "trsFlavorDef",
            (
                "<item><sType>1</sType><sFlavor>2</sFlavor>"
                "<sFlavorDescr>Phase II</sFlavorDescr></item>"
            ),
            RadioReferenceTrunkFlavor,
        ),
        (
            RadioReferenceWsdlOperation.GET_TRUNKED_VOICE,
            "trsVoiceDef",
            (
                "<item><sType>1</sType><sVoice>3</sVoice>"
                "<sVoiceDescr>Digital</sVoiceDescr></item>"
            ),
            RadioReferenceTrunkVoice,
        ),
    ),
)
def test_decode_lookup_operation_arrays(
    operation: RadioReferenceWsdlOperation,
    item_type: str,
    item: str,
    expected_type: type[object],
) -> None:
    response = _array_response(
        operation,
        item_type,
        item,
        count=1,
    )

    decoded = RadioReferenceSoapDecoder().decode(operation, response)

    assert isinstance(decoded, tuple)
    assert isinstance(decoded[0], expected_type)


def test_decoder_accepts_return_href_to_multiref_array() -> None:
    target = (
        '<multiRef id="result0" xsi:type="enc:Array" '
        'enc:arrayType="tns:tag[1]">'
        f"{_tag_item()}"
        "</multiRef>"
    )
    response = _soap(
        RadioReferenceWsdlOperation.GET_TAG,
        "",
        return_attributes=' href="#result0"',
        extra_body=target,
    )

    decoded = RadioReferenceSoapDecoder().decode(
        RadioReferenceWsdlOperation.GET_TAG,
        response,
    )

    assert decoded == (
        RadioReferenceTag(
            tag_id=2,
            description=" Fire Dispatch ",
        ),
    )


@pytest.mark.parametrize(
    "xml",
    (
        b"",
        b"<not-xml",
        b"<root />",
        (
            f'<soap:Envelope xmlns:soap="{SOAP}">'
            "<soap:Body><soap:Fault>"
            "<faultstring>provider secret detail</faultstring>"
            "</soap:Fault></soap:Body></soap:Envelope>"
        ).encode(),
    ),
)
def test_decoder_redacts_malformed_or_fault_responses(xml: bytes) -> None:
    with pytest.raises(RadioReferenceError) as captured:
        RadioReferenceSoapDecoder().decode(
            RadioReferenceWsdlOperation.GET_TAG,
            xml,
        )

    assert captured.value.reason is RadioReferenceErrorReason.INVALID_RESPONSE
    assert "provider secret detail" not in str(captured.value)
    assert captured.value.__cause__ is None


def test_decoder_rejects_operation_response_mismatch() -> None:
    response = _array_response(
        RadioReferenceWsdlOperation.GET_MODE,
        "mode",
    )

    with pytest.raises(RadioReferenceError):
        RadioReferenceSoapDecoder().decode(
            RadioReferenceWsdlOperation.GET_TAG,
            response,
        )


def test_decoder_rejects_return_type_mismatch() -> None:
    response = _soap(
        RadioReferenceWsdlOperation.GET_TAG,
        _tag_item(),
        return_attributes=' xsi:type="tns:modes"',
    )

    with pytest.raises(RadioReferenceError):
        RadioReferenceSoapDecoder().decode(
            RadioReferenceWsdlOperation.GET_TAG,
            response,
        )


@pytest.mark.parametrize(
    "return_attributes",
    (
        ' xsi:type="enc:Array" enc:arrayType="tns:mode[1]"',
        ' xsi:type="enc:Array" enc:arrayType="tns:tag[2]"',
    ),
)
def test_decoder_rejects_array_type_and_count_mismatches(
    return_attributes: str,
) -> None:
    response = _soap(
        RadioReferenceWsdlOperation.GET_TAG,
        _tag_item(),
        return_attributes=return_attributes,
    )

    with pytest.raises(RadioReferenceError):
        RadioReferenceSoapDecoder().decode(
            RadioReferenceWsdlOperation.GET_TAG,
            response,
        )


@pytest.mark.parametrize(
    "returned",
    (
        "<item><tagId>1</tagId></item>",
        (
            "<item><tagId>1</tagId><tagId>2</tagId>"
            "<tagDescr>x</tagDescr></item>"
        ),
        (
            "<item><tagId>1</tagId><tagDescr>x</tagDescr>"
            "<unexpected>y</unexpected></item>"
        ),
    ),
)
def test_decoder_rejects_missing_duplicate_or_unknown_members(
    returned: str,
) -> None:
    response = _soap(
        RadioReferenceWsdlOperation.GET_TAG,
        returned,
        return_attributes=_array_attributes("tag", 1),
    )

    with pytest.raises(RadioReferenceError):
        RadioReferenceSoapDecoder().decode(
            RadioReferenceWsdlOperation.GET_TAG,
            response,
        )


@pytest.mark.parametrize(
    "tag_id",
    ("2147483648", "-2147483649", "1.0", " 1"),
)
def test_decoder_enforces_xsd_int_lexical_and_range_rules(tag_id: str) -> None:
    item = f"<item><tagId>{tag_id}</tagId><tagDescr>x</tagDescr></item>"
    response = _array_response(
        RadioReferenceWsdlOperation.GET_TAG,
        "tag",
        item,
        count=1,
    )

    with pytest.raises(RadioReferenceError):
        RadioReferenceSoapDecoder().decode(
            RadioReferenceWsdlOperation.GET_TAG,
            response,
        )


@pytest.mark.parametrize(
    "frequency",
    ("NaN", "Infinity", "1E3", " 155.1"),
)
def test_decoder_enforces_xsd_decimal_lexical_rules(frequency: str) -> None:
    response = _array_response(
        RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
        "freq",
        _frequency_item(output=frequency),
        count=1,
    )

    with pytest.raises(RadioReferenceError):
        RadioReferenceSoapDecoder().decode(
            RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
            response,
        )


@pytest.mark.parametrize("lexical", ("yes", "TRUE", " true"))
def test_decoder_accepts_only_xsd_boolean_lexical_forms(lexical: str) -> None:
    talkgroup = _item(
        "<tgId>20</tgId>"
        "<tgDec>12345</tgDec>"
        "<tgSubfleet></tgSubfleet>"
        f"<tgLtr>{lexical}</tgLtr>"
        "<tgSlot></tgSlot>"
        "<tgDescr>Operations</tgDescr>"
        "<tgAlpha>Ops</tgAlpha>"
        "<tgMode>D</tgMode>"
        "<enc>0</enc>"
        f"{_array_member('tags', 'tag')}"
        "<tgCid>30</tgCid>"
        "<tgSort>1</tgSort>"
        "<tgDate>2026-08-13T09:21:04Z</tgDate>"
    )
    response = _array_response(
        RadioReferenceWsdlOperation.GET_TRUNKED_TALKGROUPS,
        "Talkgroup",
        talkgroup,
        count=1,
    )

    with pytest.raises(RadioReferenceError):
        RadioReferenceSoapDecoder().decode(
            RadioReferenceWsdlOperation.GET_TRUNKED_TALKGROUPS,
            response,
        )


@pytest.mark.parametrize(
    "timestamp",
    (
        "2026-08-13 09:21:04Z",
        "2026-08-13T09:21:04.1234567Z",
        "2026-08-13T09:21:04+14:01",
        "2026-13-13T09:21:04Z",
    ),
)
def test_decoder_rejects_unrepresentable_datetime_lexical_forms(
    timestamp: str,
) -> None:
    response = _array_response(
        RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
        "freq",
        _frequency_item(timestamp=timestamp),
        count=1,
    )

    with pytest.raises(RadioReferenceError):
        RadioReferenceSoapDecoder().decode(
            RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
            response,
        )


def test_decoder_preserves_positive_timezone_offset() -> None:
    response = _array_response(
        RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
        "freq",
        _frequency_item(timestamp="2026-08-13T09:21:04+05:30"),
        count=1,
    )

    decoded = RadioReferenceSoapDecoder().decode(
        RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
        response,
    )

    assert isinstance(decoded, tuple)
    assert isinstance(decoded[0], RadioReferenceFrequency)
    assert decoded[0].last_updated.tzinfo == timezone(
        timedelta(hours=5, minutes=30)
    )


def test_decoder_rejects_doctype_and_entity_input() -> None:
    response = (
        b'<!DOCTYPE soap:Envelope [<!ENTITY leak "secret">]>'
        + _array_response(
            RadioReferenceWsdlOperation.GET_TAG,
            "tag",
        )
    )

    with pytest.raises(RadioReferenceError):
        RadioReferenceSoapDecoder().decode(
            RadioReferenceWsdlOperation.GET_TAG,
            response,
        )


def test_decoder_enforces_document_size_bound() -> None:
    response = _array_response(
        RadioReferenceWsdlOperation.GET_TAG,
        "tag",
    )
    decoder = RadioReferenceSoapDecoder(
        max_document_bytes=len(response) - 1,
    )

    with pytest.raises(RadioReferenceError):
        decoder.decode(RadioReferenceWsdlOperation.GET_TAG, response)


def test_decoder_enforces_element_count_bound() -> None:
    response = _array_response(
        RadioReferenceWsdlOperation.GET_TAG,
        "tag",
        _tag_item(),
        count=1,
    )
    decoder = RadioReferenceSoapDecoder(max_elements=3)

    with pytest.raises(RadioReferenceError):
        decoder.decode(RadioReferenceWsdlOperation.GET_TAG, response)


def test_decoder_rejects_duplicate_reference_ids() -> None:
    response = _soap(
        RadioReferenceWsdlOperation.GET_TAG,
        "",
        return_attributes=' href="#a"',
        extra_body=(
            '<multiRef id="a" xsi:type="enc:Array" '
            'enc:arrayType="tns:tag[0]" />'
            '<multiRef id="a" xsi:type="enc:Array" '
            'enc:arrayType="tns:tag[0]" />'
        ),
    )

    with pytest.raises(RadioReferenceError):
        RadioReferenceSoapDecoder().decode(
            RadioReferenceWsdlOperation.GET_TAG,
            response,
        )


@pytest.mark.parametrize(
    "href",
    ("missing", "https://example.invalid/provider", "#missing"),
)
def test_decoder_rejects_external_or_missing_references(href: str) -> None:
    response = _soap(
        RadioReferenceWsdlOperation.GET_TAG,
        "",
        return_attributes=f' href="{href}"',
    )

    with pytest.raises(RadioReferenceError):
        RadioReferenceSoapDecoder().decode(
            RadioReferenceWsdlOperation.GET_TAG,
            response,
        )


def test_decoder_rejects_reference_cycles() -> None:
    response = _soap(
        RadioReferenceWsdlOperation.GET_TAG,
        "",
        return_attributes=' href="#a"',
        extra_body=(
            '<multiRef id="a" href="#b" />'
            '<multiRef id="b" href="#a" />'
        ),
    )

    with pytest.raises(RadioReferenceError):
        RadioReferenceSoapDecoder().decode(
            RadioReferenceWsdlOperation.GET_TAG,
            response,
        )


def test_decoder_enforces_reference_count_bound() -> None:
    response = _array_response(
        RadioReferenceWsdlOperation.GET_TAG,
        "tag",
    ).replace(
        b"</soap:Body>",
        b'<multiRef id="a" /><multiRef id="b" /></soap:Body>',
    )
    decoder = RadioReferenceSoapDecoder(max_references=1)

    with pytest.raises(RadioReferenceError):
        decoder.decode(RadioReferenceWsdlOperation.GET_TAG, response)


def test_decoder_enforces_reference_depth_bound() -> None:
    response = _soap(
        RadioReferenceWsdlOperation.GET_TAG,
        "",
        return_attributes=' href="#a"',
        extra_body=(
            '<multiRef id="a" href="#b" />'
            '<multiRef id="b" xsi:type="enc:Array" '
            'enc:arrayType="tns:tag[0]" />'
        ),
    )
    decoder = RadioReferenceSoapDecoder(max_reference_depth=1)

    with pytest.raises(RadioReferenceError):
        decoder.decode(RadioReferenceWsdlOperation.GET_TAG, response)


def test_decoder_rejects_xsi_nil_without_inventing_optional_semantics() -> None:
    response = _soap(
        RadioReferenceWsdlOperation.GET_TAG,
        "",
        return_attributes=' xsi:nil="true"',
    )

    with pytest.raises(RadioReferenceError):
        RadioReferenceSoapDecoder().decode(
            RadioReferenceWsdlOperation.GET_TAG,
            response,
        )


def test_decoder_requires_typed_operation_and_bytes_input() -> None:
    decoder = RadioReferenceSoapDecoder()

    with pytest.raises(TypeError):
        decoder.decode(  # type: ignore[arg-type]
            "getTag",
            b"<xml />",
        )

    with pytest.raises(TypeError):
        decoder.decode(  # type: ignore[arg-type]
            RadioReferenceWsdlOperation.GET_TAG,
            "<xml />",
        )


@pytest.mark.parametrize(
    "name",
    (
        "RADIOREFERENCE_SOAP_DEFAULT_MAX_DOCUMENT_BYTES",
        "RADIOREFERENCE_SOAP_DEFAULT_MAX_ELEMENTS",
        "RADIOREFERENCE_SOAP_DEFAULT_MAX_REFERENCES",
        "RADIOREFERENCE_SOAP_DEFAULT_MAX_REFERENCE_DEPTH",
        "RADIOREFERENCE_SOAP_ENCODING_NAMESPACE",
        "RADIOREFERENCE_SOAP_ENVELOPE_NAMESPACE",
        "RADIOREFERENCE_XML_SCHEMA_INSTANCE_NAMESPACE",
        "RadioReferenceSoapDecoder",
        "RadioReferenceSoapResult",
    ),
)
def test_soap_decoder_exports_are_public(name: str) -> None:
    assert name in sds200.__all__
    assert hasattr(sds200, name)
