"""Offline RadioReference SOAP 1.1 RPC/encoded response decoding."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Final, TypeAlias
from xml.parsers import expat

from .radioreference import RadioReferenceError, RadioReferenceErrorReason
from .radioreference_records import (
    RADIOREFERENCE_SOAP_NAMESPACE,
    RadioReferenceAgency,
    RadioReferenceAgencyInfo,
    RadioReferenceCategory,
    RadioReferenceCountryInfo,
    RadioReferenceCounty,
    RadioReferenceCountyInfo,
    RadioReferenceFrequency,
    RadioReferenceMode,
    RadioReferenceRectangle,
    RadioReferenceSearchFrequencyResult,
    RadioReferenceState,
    RadioReferenceStateInfo,
    RadioReferenceSubcategory,
    RadioReferenceTag,
    RadioReferenceTalkgroup,
    RadioReferenceTalkgroupCategory,
    RadioReferenceTrunkBandplan,
    RadioReferenceTrunkFlavor,
    RadioReferenceTrunkFleetmap,
    RadioReferenceTrunkListEntry,
    RadioReferenceTrunkSite,
    RadioReferenceTrunkSiteFrequency,
    RadioReferenceTrunkSiteLicense,
    RadioReferenceTrunkSystem,
    RadioReferenceTrunkSystemId,
    RadioReferenceTrunkType,
    RadioReferenceTrunkVoice,
    RadioReferenceWsdlOperation,
    radioreference_operation_contract,
)

RADIOREFERENCE_SOAP_ENVELOPE_NAMESPACE: Final = (
    "http://schemas.xmlsoap.org/soap/envelope/"
)
RADIOREFERENCE_SOAP_ENCODING_NAMESPACE: Final = (
    "http://schemas.xmlsoap.org/soap/encoding/"
)
RADIOREFERENCE_XML_SCHEMA_INSTANCE_NAMESPACE: Final = (
    "http://www.w3.org/2001/XMLSchema-instance"
)

RADIOREFERENCE_SOAP_DEFAULT_MAX_DOCUMENT_BYTES: Final = 4 * 1024 * 1024
RADIOREFERENCE_SOAP_DEFAULT_MAX_ELEMENTS: Final = 20_000
RADIOREFERENCE_SOAP_DEFAULT_MAX_REFERENCES: Final = 4_096
RADIOREFERENCE_SOAP_DEFAULT_MAX_REFERENCE_DEPTH: Final = 64

_SOAP_ENVELOPE = (
    f"{{{RADIOREFERENCE_SOAP_ENVELOPE_NAMESPACE}}}Envelope"
)
_SOAP_BODY = f"{{{RADIOREFERENCE_SOAP_ENVELOPE_NAMESPACE}}}Body"
_SOAP_HEADER = f"{{{RADIOREFERENCE_SOAP_ENVELOPE_NAMESPACE}}}Header"
_SOAP_FAULT = f"{{{RADIOREFERENCE_SOAP_ENVELOPE_NAMESPACE}}}Fault"
_SOAP_ARRAY_TYPE = (
    f"{{{RADIOREFERENCE_SOAP_ENCODING_NAMESPACE}}}arrayType"
)
_XSI_TYPE = f"{{{RADIOREFERENCE_XML_SCHEMA_INSTANCE_NAMESPACE}}}type"
_XSI_NIL = f"{{{RADIOREFERENCE_XML_SCHEMA_INSTANCE_NAMESPACE}}}nil"

_XSD_INT_MIN = -(2**31)
_XSD_INT_MAX = 2**31 - 1

_INT_PATTERN = re.compile(r"[+-]?[0-9]+\Z")
_DECIMAL_PATTERN = re.compile(
    r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)\Z"
)
_DATETIME_PATTERN = re.compile(
    r"(?P<year>[0-9]{4})-"
    r"(?P<month>[0-9]{2})-"
    r"(?P<day>[0-9]{2})T"
    r"(?P<hour>[0-9]{2}):"
    r"(?P<minute>[0-9]{2}):"
    r"(?P<second>[0-9]{2})"
    r"(?P<fraction>\.[0-9]{1,6})?"
    r"(?P<zone>Z|[+-][0-9]{2}:[0-9]{2})?\Z"
)
_ARRAY_TYPE_PATTERN = re.compile(
    r"(?P<type>[^\[\]]+)\[(?P<count>[0-9]*)\]\Z"
)
_EXPAT_NAMESPACE_SEPARATOR = "\x1f"

RadioReferenceSoapResult: TypeAlias = (
    RadioReferenceCountryInfo
    | RadioReferenceStateInfo
    | RadioReferenceCountyInfo
    | RadioReferenceAgencyInfo
    | tuple[RadioReferenceFrequency, ...]
    | tuple[RadioReferenceSearchFrequencyResult, ...]
    | RadioReferenceTrunkSystem
    | tuple[RadioReferenceTrunkSite, ...]
    | tuple[RadioReferenceTalkgroupCategory, ...]
    | tuple[RadioReferenceTalkgroup, ...]
    | tuple[RadioReferenceTag, ...]
    | tuple[RadioReferenceMode, ...]
    | tuple[RadioReferenceTrunkType, ...]
    | tuple[RadioReferenceTrunkFlavor, ...]
    | tuple[RadioReferenceTrunkVoice, ...]
)


class _DecodeFailure(Exception):
    """Internal failure whose details must never cross the public boundary."""


@dataclass(frozen=True, slots=True)
class _Context:
    references: dict[str, ET.Element]
    max_reference_depth: int


def _local_name(name: str) -> str:
    if name.startswith("{"):
        return name.rsplit("}", 1)[-1]
    return name.split(":", 1)[-1]


def _validate_positive_limit(value: int, *, label: str) -> None:
    if type(value) is not int:
        raise TypeError(f"{label} must be an integer.")
    if value <= 0:
        raise ValueError(f"{label} must be greater than zero.")


def _invalid_response() -> RadioReferenceError:
    return RadioReferenceError(RadioReferenceErrorReason.INVALID_RESPONSE)


def _expanded_xml_name(name: str) -> str:
    if _EXPAT_NAMESPACE_SEPARATOR not in name:
        return name
    namespace, local_name = name.split(_EXPAT_NAMESPACE_SEPARATOR, 1)
    if not namespace or not local_name:
        raise _DecodeFailure
    return f"{{{namespace}}}{local_name}"


def _parse_document(
    xml: bytes,
    *,
    max_elements: int,
) -> ET.Element:
    builder = ET.TreeBuilder()
    parser = expat.ParserCreate(
        namespace_separator=_EXPAT_NAMESPACE_SEPARATOR
    )
    parser.buffer_text = True
    element_count = 0

    def start_element(
        name: str,
        attributes: dict[str, str],
    ) -> None:
        nonlocal element_count
        element_count += 1
        if element_count > max_elements:
            raise _DecodeFailure
        builder.start(
            _expanded_xml_name(name),
            {
                _expanded_xml_name(attribute): value
                for attribute, value in attributes.items()
            },
        )

    def end_element(name: str) -> None:
        builder.end(_expanded_xml_name(name))

    def reject_declaration(*_args: object) -> None:
        raise _DecodeFailure

    def reject_external_entity(*_args: object) -> int:
        raise _DecodeFailure

    parser.StartElementHandler = start_element
    parser.EndElementHandler = end_element
    parser.CharacterDataHandler = builder.data
    parser.StartDoctypeDeclHandler = reject_declaration
    parser.EntityDeclHandler = reject_declaration
    parser.UnparsedEntityDeclHandler = reject_declaration
    parser.NotationDeclHandler = reject_declaration
    parser.ExternalEntityRefHandler = reject_external_entity
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)

    try:
        parser.Parse(xml, True)
    except expat.ExpatError:
        raise _DecodeFailure from None

    try:
        return builder.close()
    except (IndexError, AssertionError):
        raise _DecodeFailure from None


def _require_no_nil(element: ET.Element) -> None:
    if _XSI_NIL in element.attrib:
        raise _DecodeFailure


def _resolve(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> tuple[ET.Element, tuple[str, ...]]:
    _require_no_nil(element)
    href = element.attrib.get("href")
    if href is None:
        return element, trail

    if not href.startswith("#") or len(href) == 1:
        raise _DecodeFailure
    if list(element):
        raise _DecodeFailure
    if element.text is not None and element.text.strip():
        raise _DecodeFailure

    reference_id = href[1:]
    if reference_id in trail:
        raise _DecodeFailure
    if len(trail) >= context.max_reference_depth:
        raise _DecodeFailure

    target = context.references.get(reference_id)
    if target is None:
        raise _DecodeFailure

    return _resolve(
        target,
        context,
        trail + (reference_id,),
    )


def _require_complex_text(element: ET.Element) -> None:
    if element.text is not None and element.text.strip():
        raise _DecodeFailure
    for child in element:
        if child.tail is not None and child.tail.strip():
            raise _DecodeFailure


def _validate_declared_type(
    element: ET.Element,
    expected_type: str,
) -> None:
    declared = element.attrib.get(_XSI_TYPE)
    if declared is None:
        return
    if _local_name(declared) != expected_type:
        raise _DecodeFailure


def _members(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
    *,
    expected_type: str,
    expected_names: tuple[str, ...],
) -> tuple[dict[str, ET.Element], tuple[str, ...]]:
    resolved, resolved_trail = _resolve(element, context, trail)
    _validate_declared_type(resolved, expected_type)
    _require_complex_text(resolved)

    expected = set(expected_names)
    found: dict[str, ET.Element] = {}
    for child in resolved:
        name = _local_name(child.tag)
        if name not in expected or name in found:
            raise _DecodeFailure
        found[name] = child

    if set(found) != expected:
        raise _DecodeFailure

    return found, resolved_trail


def _scalar_element(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
    *,
    expected_type: str,
) -> str:
    resolved, _resolved_trail = _resolve(element, context, trail)
    _validate_declared_type(resolved, expected_type)
    if list(resolved):
        raise _DecodeFailure
    _require_no_nil(resolved)
    return "" if resolved.text is None else resolved.text


def _string(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> str:
    return _scalar_element(
        element,
        context,
        trail,
        expected_type="string",
    )


def _integer(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> int:
    text = _scalar_element(
        element,
        context,
        trail,
        expected_type="int",
    )
    if _INT_PATTERN.fullmatch(text) is None:
        raise _DecodeFailure
    try:
        value = int(text, 10)
    except ValueError:
        raise _DecodeFailure from None
    if not _XSD_INT_MIN <= value <= _XSD_INT_MAX:
        raise _DecodeFailure
    return value


def _decimal(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> Decimal:
    text = _scalar_element(
        element,
        context,
        trail,
        expected_type="decimal",
    )
    if _DECIMAL_PATTERN.fullmatch(text) is None:
        raise _DecodeFailure
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise _DecodeFailure from None
    if not value.is_finite():
        raise _DecodeFailure
    return value


def _boolean(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> bool:
    text = _scalar_element(
        element,
        context,
        trail,
        expected_type="boolean",
    )
    if text in {"true", "1"}:
        return True
    if text in {"false", "0"}:
        return False
    raise _DecodeFailure


def _datetime(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> datetime:
    text = _scalar_element(
        element,
        context,
        trail,
        expected_type="dateTime",
    )
    match = _DATETIME_PATTERN.fullmatch(text)
    if match is None:
        raise _DecodeFailure

    fraction = match.group("fraction")
    microsecond = 0
    if fraction is not None:
        microsecond = int(fraction[1:].ljust(6, "0"))

    zone = match.group("zone")
    tzinfo: timezone | None = None
    if zone == "Z":
        tzinfo = UTC
    elif zone is not None:
        hours = int(zone[1:3])
        minutes = int(zone[4:6])
        if hours > 14 or minutes > 59:
            raise _DecodeFailure
        if hours == 14 and minutes != 0:
            raise _DecodeFailure
        offset = timedelta(hours=hours, minutes=minutes)
        if zone.startswith("-"):
            offset = -offset
        try:
            tzinfo = timezone(offset)
        except ValueError:
            raise _DecodeFailure from None

    try:
        return datetime(
            year=int(match.group("year")),
            month=int(match.group("month")),
            day=int(match.group("day")),
            hour=int(match.group("hour")),
            minute=int(match.group("minute")),
            second=int(match.group("second")),
            microsecond=microsecond,
            tzinfo=tzinfo,
        )
    except ValueError:
        raise _DecodeFailure from None


def _array_items(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
    *,
    expected_item_type: str,
) -> tuple[tuple[ET.Element, ...], tuple[str, ...]]:
    resolved, resolved_trail = _resolve(element, context, trail)
    _require_complex_text(resolved)

    declared_type = resolved.attrib.get(_XSI_TYPE)
    if (
        declared_type is not None
        and _local_name(declared_type) not in {"Array", expected_item_type}
    ):
        raise _DecodeFailure

    declared_count: int | None = None
    array_type = resolved.attrib.get(_SOAP_ARRAY_TYPE)
    if array_type is not None:
        match = _ARRAY_TYPE_PATTERN.fullmatch(array_type)
        if match is None:
            raise _DecodeFailure
        if _local_name(match.group("type")) != expected_item_type:
            raise _DecodeFailure
        count = match.group("count")
        if count:
            declared_count = int(count)

    items = tuple(resolved)
    if any(_local_name(item.tag) != "item" for item in items):
        raise _DecodeFailure
    if declared_count is not None and declared_count != len(items):
        raise _DecodeFailure

    return items, resolved_trail


def _parse_rectangle(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> RadioReferenceRectangle:
    fields, trail = _members(
        element,
        context,
        trail,
        expected_type="Rectangle",
        expected_names=("nw_lat", "nw_lon", "se_lat", "se_lon"),
    )
    return RadioReferenceRectangle(
        northwest_latitude=_decimal(fields["nw_lat"], context, trail),
        northwest_longitude=_decimal(fields["nw_lon"], context, trail),
        southeast_latitude=_decimal(fields["se_lat"], context, trail),
        southeast_longitude=_decimal(fields["se_lon"], context, trail),
    )


def _parse_rectangles(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> tuple[RadioReferenceRectangle, ...]:
    items, trail = _array_items(
        element,
        context,
        trail,
        expected_item_type="Rectangle",
    )
    return tuple(_parse_rectangle(item, context, trail) for item in items)


def _parse_tag(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> RadioReferenceTag:
    fields, trail = _members(
        element,
        context,
        trail,
        expected_type="tag",
        expected_names=("tagId", "tagDescr"),
    )
    return RadioReferenceTag(
        tag_id=_integer(fields["tagId"], context, trail),
        description=_string(fields["tagDescr"], context, trail),
    )


def _parse_tags(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> tuple[RadioReferenceTag, ...]:
    items, trail = _array_items(
        element,
        context,
        trail,
        expected_item_type="tag",
    )
    return tuple(_parse_tag(item, context, trail) for item in items)


def _parse_mode(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> RadioReferenceMode:
    fields, trail = _members(
        element,
        context,
        trail,
        expected_type="mode",
        expected_names=("mode", "modeName"),
    )
    return RadioReferenceMode(
        mode=_integer(fields["mode"], context, trail),
        name=_string(fields["modeName"], context, trail),
    )


def _parse_modes(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> tuple[RadioReferenceMode, ...]:
    items, trail = _array_items(
        element,
        context,
        trail,
        expected_item_type="mode",
    )
    return tuple(_parse_mode(item, context, trail) for item in items)


def _parse_agency(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> RadioReferenceAgency:
    fields, trail = _members(
        element,
        context,
        trail,
        expected_type="Agency",
        expected_names=("aid", "aName", "aType"),
    )
    return RadioReferenceAgency(
        agency_id=_integer(fields["aid"], context, trail),
        name=_string(fields["aName"], context, trail),
        agency_type=_integer(fields["aType"], context, trail),
    )


def _parse_agencies(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> tuple[RadioReferenceAgency, ...]:
    items, trail = _array_items(
        element,
        context,
        trail,
        expected_item_type="Agency",
    )
    return tuple(_parse_agency(item, context, trail) for item in items)


def _parse_county(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> RadioReferenceCounty:
    fields, trail = _members(
        element,
        context,
        trail,
        expected_type="County",
        expected_names=("ctid", "countyName", "countyHeader"),
    )
    return RadioReferenceCounty(
        county_id=_integer(fields["ctid"], context, trail),
        name=_string(fields["countyName"], context, trail),
        header=_string(fields["countyHeader"], context, trail),
    )


def _parse_counties(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> tuple[RadioReferenceCounty, ...]:
    items, trail = _array_items(
        element,
        context,
        trail,
        expected_item_type="County",
    )
    return tuple(_parse_county(item, context, trail) for item in items)


def _parse_state(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> RadioReferenceState:
    fields, trail = _members(
        element,
        context,
        trail,
        expected_type="State",
        expected_names=("stid", "stateName", "stateCode"),
    )
    return RadioReferenceState(
        state_id=_integer(fields["stid"], context, trail),
        name=_string(fields["stateName"], context, trail),
        code=_string(fields["stateCode"], context, trail),
    )


def _parse_states(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> tuple[RadioReferenceState, ...]:
    items, trail = _array_items(
        element,
        context,
        trail,
        expected_item_type="State",
    )
    return tuple(_parse_state(item, context, trail) for item in items)


def _parse_id_wrapper(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
    *,
    wrapper_type: str,
    member_name: str,
) -> int:
    fields, trail = _members(
        element,
        context,
        trail,
        expected_type=wrapper_type,
        expected_names=(member_name,),
    )
    return _integer(fields[member_name], context, trail)


def _parse_id_array(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
    *,
    wrapper_type: str,
    member_name: str,
) -> tuple[int, ...]:
    items, trail = _array_items(
        element,
        context,
        trail,
        expected_item_type=wrapper_type,
    )
    return tuple(
        _parse_id_wrapper(
            item,
            context,
            trail,
            wrapper_type=wrapper_type,
            member_name=member_name,
        )
        for item in items
    )


def _parse_subcategory(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> RadioReferenceSubcategory:
    fields, trail = _members(
        element,
        context,
        trail,
        expected_type="subcat",
        expected_names=(
            "scid",
            "scName",
            "lat",
            "lon",
            "range",
            "rectangles",
            "sids",
        ),
    )
    return RadioReferenceSubcategory(
        subcategory_id=_integer(fields["scid"], context, trail),
        name=_string(fields["scName"], context, trail),
        latitude=_decimal(fields["lat"], context, trail),
        longitude=_decimal(fields["lon"], context, trail),
        range=_decimal(fields["range"], context, trail),
        rectangles=_parse_rectangles(fields["rectangles"], context, trail),
        trunked_system_ids=_parse_id_array(
            fields["sids"],
            context,
            trail,
            wrapper_type="sid",
            member_name="sid",
        ),
    )


def _parse_subcategories(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> tuple[RadioReferenceSubcategory, ...]:
    items, trail = _array_items(
        element,
        context,
        trail,
        expected_item_type="subcat",
    )
    return tuple(_parse_subcategory(item, context, trail) for item in items)


def _parse_category(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> RadioReferenceCategory:
    fields, trail = _members(
        element,
        context,
        trail,
        expected_type="cat",
        expected_names=("cid", "cName", "subcats"),
    )
    return RadioReferenceCategory(
        category_id=_integer(fields["cid"], context, trail),
        name=_string(fields["cName"], context, trail),
        subcategories=_parse_subcategories(
            fields["subcats"],
            context,
            trail,
        ),
    )


def _parse_categories(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> tuple[RadioReferenceCategory, ...]:
    items, trail = _array_items(
        element,
        context,
        trail,
        expected_item_type="cat",
    )
    return tuple(_parse_category(item, context, trail) for item in items)


def _parse_trunk_list_entry(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> RadioReferenceTrunkListEntry:
    fields, trail = _members(
        element,
        context,
        trail,
        expected_type="TrsListDef",
        expected_names=(
            "sid",
            "sName",
            "sType",
            "sFlavor",
            "sVoice",
            "sCity",
            "lastUpdated",
        ),
    )
    return RadioReferenceTrunkListEntry(
        system_id=_integer(fields["sid"], context, trail),
        name=_string(fields["sName"], context, trail),
        system_type=_integer(fields["sType"], context, trail),
        flavor=_integer(fields["sFlavor"], context, trail),
        voice=_integer(fields["sVoice"], context, trail),
        city=_string(fields["sCity"], context, trail),
        last_updated=_datetime(fields["lastUpdated"], context, trail),
    )


def _parse_trunk_list(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> tuple[RadioReferenceTrunkListEntry, ...]:
    items, trail = _array_items(
        element,
        context,
        trail,
        expected_item_type="TrsListDef",
    )
    return tuple(
        _parse_trunk_list_entry(item, context, trail)
        for item in items
    )


def _parse_country_info(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> RadioReferenceCountryInfo:
    fields, trail = _members(
        element,
        context,
        trail,
        expected_type="CountryInfo",
        expected_names=(
            "coid",
            "countryName",
            "countryCode",
            "agencyList",
            "stateList",
        ),
    )
    return RadioReferenceCountryInfo(
        country_id=_integer(fields["coid"], context, trail),
        name=_string(fields["countryName"], context, trail),
        code=_string(fields["countryCode"], context, trail),
        agencies=_parse_agencies(fields["agencyList"], context, trail),
        states=_parse_states(fields["stateList"], context, trail),
    )


def _parse_state_info(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> RadioReferenceStateInfo:
    fields, trail = _members(
        element,
        context,
        trail,
        expected_type="StateInfo",
        expected_names=(
            "stid",
            "stateName",
            "stateEntityType",
            "trsList",
            "agencyList",
            "countyList",
        ),
    )
    return RadioReferenceStateInfo(
        state_id=_integer(fields["stid"], context, trail),
        name=_string(fields["stateName"], context, trail),
        entity_type=_string(fields["stateEntityType"], context, trail),
        trunked_systems=_parse_trunk_list(
            fields["trsList"],
            context,
            trail,
        ),
        agencies=_parse_agencies(fields["agencyList"], context, trail),
        counties=_parse_counties(fields["countyList"], context, trail),
    )


def _parse_county_info(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> RadioReferenceCountyInfo:
    fields, trail = _members(
        element,
        context,
        trail,
        expected_type="CountyInfo",
        expected_names=(
            "ctid",
            "countyName",
            "countyHeader",
            "stid",
            "fips",
            "lastUpdated",
            "lat",
            "lon",
            "range",
            "rectangles",
            "cats",
            "trsList",
            "agencyList",
        ),
    )
    return RadioReferenceCountyInfo(
        county_id=_integer(fields["ctid"], context, trail),
        name=_string(fields["countyName"], context, trail),
        header=_string(fields["countyHeader"], context, trail),
        state_id=_integer(fields["stid"], context, trail),
        fips=_string(fields["fips"], context, trail),
        last_updated=_datetime(fields["lastUpdated"], context, trail),
        latitude=_decimal(fields["lat"], context, trail),
        longitude=_decimal(fields["lon"], context, trail),
        range=_decimal(fields["range"], context, trail),
        rectangles=_parse_rectangles(fields["rectangles"], context, trail),
        categories=_parse_categories(fields["cats"], context, trail),
        trunked_systems=_parse_trunk_list(
            fields["trsList"],
            context,
            trail,
        ),
        agencies=_parse_agencies(fields["agencyList"], context, trail),
    )


def _parse_agency_info(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> RadioReferenceAgencyInfo:
    fields, trail = _members(
        element,
        context,
        trail,
        expected_type="AgencyInfo",
        expected_names=(
            "aid",
            "agencyName",
            "agencyType",
            "ctid",
            "stid",
            "lat",
            "lon",
            "range",
            "rectangles",
            "lastUpdated",
            "cats",
        ),
    )
    return RadioReferenceAgencyInfo(
        agency_id=_integer(fields["aid"], context, trail),
        name=_string(fields["agencyName"], context, trail),
        agency_type=_string(fields["agencyType"], context, trail),
        county_id=_integer(fields["ctid"], context, trail),
        state_id=_integer(fields["stid"], context, trail),
        latitude=_decimal(fields["lat"], context, trail),
        longitude=_decimal(fields["lon"], context, trail),
        range=_decimal(fields["range"], context, trail),
        rectangles=_parse_rectangles(fields["rectangles"], context, trail),
        last_updated=_datetime(fields["lastUpdated"], context, trail),
        categories=_parse_categories(fields["cats"], context, trail),
    )


def _parse_frequency(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> RadioReferenceFrequency:
    fields, trail = _members(
        element,
        context,
        trail,
        expected_type="freq",
        expected_names=(
            "fid",
            "out",
            "in",
            "callsign",
            "descr",
            "alpha",
            "tone",
            "colorCode",
            "tg",
            "slot",
            "mode",
            "enc",
            "class",
            "tags",
            "scid",
            "sort",
            "lastUpdated",
        ),
    )
    return RadioReferenceFrequency(
        frequency_id=_integer(fields["fid"], context, trail),
        output_frequency=_decimal(fields["out"], context, trail),
        input_frequency=_decimal(fields["in"], context, trail),
        callsign=_string(fields["callsign"], context, trail),
        description=_string(fields["descr"], context, trail),
        alpha_tag=_string(fields["alpha"], context, trail),
        tone=_string(fields["tone"], context, trail),
        color_code=_string(fields["colorCode"], context, trail),
        talkgroup=_string(fields["tg"], context, trail),
        slot=_string(fields["slot"], context, trail),
        mode=_string(fields["mode"], context, trail),
        encryption=_integer(fields["enc"], context, trail),
        class_code=_string(fields["class"], context, trail),
        tags=_parse_tags(fields["tags"], context, trail),
        subcategory_id=_integer(fields["scid"], context, trail),
        sort=_integer(fields["sort"], context, trail),
        last_updated=_datetime(fields["lastUpdated"], context, trail),
    )


def _parse_frequencies(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> tuple[RadioReferenceFrequency, ...]:
    items, trail = _array_items(
        element,
        context,
        trail,
        expected_item_type="freq",
    )
    return tuple(_parse_frequency(item, context, trail) for item in items)


def _parse_search_frequency(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> RadioReferenceSearchFrequencyResult:
    fields, trail = _members(
        element,
        context,
        trail,
        expected_type="searchFreqResult",
        expected_names=(
            "out",
            "in",
            "callsign",
            "descr",
            "alpha",
            "tone",
            "colorCode",
            "tg",
            "slot",
            "mode",
            "class",
            "tags",
            "scid",
            "sid",
            "aid",
            "ctid",
        ),
    )
    return RadioReferenceSearchFrequencyResult(
        output_frequency=_decimal(fields["out"], context, trail),
        input_frequency=_decimal(fields["in"], context, trail),
        callsign=_string(fields["callsign"], context, trail),
        description=_string(fields["descr"], context, trail),
        alpha_tag=_string(fields["alpha"], context, trail),
        tone=_string(fields["tone"], context, trail),
        color_code=_string(fields["colorCode"], context, trail),
        talkgroup=_string(fields["tg"], context, trail),
        slot=_string(fields["slot"], context, trail),
        mode=_string(fields["mode"], context, trail),
        class_code=_string(fields["class"], context, trail),
        tags=_parse_tags(fields["tags"], context, trail),
        subcategory_id=_integer(fields["scid"], context, trail),
        system_id=_integer(fields["sid"], context, trail),
        agency_id=_integer(fields["aid"], context, trail),
        county_id=_integer(fields["ctid"], context, trail),
    )


def _parse_search_frequencies(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> tuple[RadioReferenceSearchFrequencyResult, ...]:
    items, trail = _array_items(
        element,
        context,
        trail,
        expected_item_type="searchFreqResult",
    )
    return tuple(
        _parse_search_frequency(item, context, trail)
        for item in items
    )


def _parse_talkgroup(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> RadioReferenceTalkgroup:
    fields, trail = _members(
        element,
        context,
        trail,
        expected_type="Talkgroup",
        expected_names=(
            "tgId",
            "tgDec",
            "tgSubfleet",
            "tgLtr",
            "tgSlot",
            "tgDescr",
            "tgAlpha",
            "tgMode",
            "enc",
            "tags",
            "tgCid",
            "tgSort",
            "tgDate",
        ),
    )
    return RadioReferenceTalkgroup(
        talkgroup_id=_integer(fields["tgId"], context, trail),
        decimal=_integer(fields["tgDec"], context, trail),
        subfleet=_string(fields["tgSubfleet"], context, trail),
        ltr=_boolean(fields["tgLtr"], context, trail),
        slot=_string(fields["tgSlot"], context, trail),
        description=_string(fields["tgDescr"], context, trail),
        alpha_tag=_string(fields["tgAlpha"], context, trail),
        mode=_string(fields["tgMode"], context, trail),
        encryption=_integer(fields["enc"], context, trail),
        tags=_parse_tags(fields["tags"], context, trail),
        category_id=_integer(fields["tgCid"], context, trail),
        sort=_integer(fields["tgSort"], context, trail),
        date=_datetime(fields["tgDate"], context, trail),
    )


def _parse_talkgroups(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> tuple[RadioReferenceTalkgroup, ...]:
    items, trail = _array_items(
        element,
        context,
        trail,
        expected_item_type="Talkgroup",
    )
    return tuple(_parse_talkgroup(item, context, trail) for item in items)


def _parse_talkgroup_category(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> RadioReferenceTalkgroupCategory:
    fields, trail = _members(
        element,
        context,
        trail,
        expected_type="TalkgroupCat",
        expected_names=(
            "tgCid",
            "sid",
            "tgCname",
            "tgSort",
            "tgSortBy",
            "lat",
            "lon",
            "range",
            "rectangles",
            "lastUpdated",
        ),
    )
    return RadioReferenceTalkgroupCategory(
        category_id=_integer(fields["tgCid"], context, trail),
        system_id=_integer(fields["sid"], context, trail),
        name=_string(fields["tgCname"], context, trail),
        sort=_integer(fields["tgSort"], context, trail),
        sort_by=_integer(fields["tgSortBy"], context, trail),
        latitude=_decimal(fields["lat"], context, trail),
        longitude=_decimal(fields["lon"], context, trail),
        range=_decimal(fields["range"], context, trail),
        rectangles=_parse_rectangles(fields["rectangles"], context, trail),
        last_updated=_datetime(fields["lastUpdated"], context, trail),
    )


def _parse_talkgroup_categories(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> tuple[RadioReferenceTalkgroupCategory, ...]:
    items, trail = _array_items(
        element,
        context,
        trail,
        expected_item_type="TalkgroupCat",
    )
    return tuple(
        _parse_talkgroup_category(item, context, trail)
        for item in items
    )


def _parse_system_id(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> RadioReferenceTrunkSystemId:
    fields, trail = _members(
        element,
        context,
        trail,
        expected_type="trsSysidDef",
        expected_names=("sysid", "ct", "wacn", "model"),
    )
    return RadioReferenceTrunkSystemId(
        system_id=_string(fields["sysid"], context, trail),
        ct=_string(fields["ct"], context, trail),
        wacn=_string(fields["wacn"], context, trail),
        model=_string(fields["model"], context, trail),
    )


def _parse_system_ids(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> tuple[RadioReferenceTrunkSystemId, ...]:
    items, trail = _array_items(
        element,
        context,
        trail,
        expected_item_type="trsSysidDef",
    )
    return tuple(_parse_system_id(item, context, trail) for item in items)


def _parse_bandplan(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> RadioReferenceTrunkBandplan:
    fields, trail = _members(
        element,
        context,
        trail,
        expected_type="trsBandplanDef",
        expected_names=("base", "spacing", "offset"),
    )
    return RadioReferenceTrunkBandplan(
        base=_string(fields["base"], context, trail),
        spacing=_string(fields["spacing"], context, trail),
        offset=_string(fields["offset"], context, trail),
    )


def _parse_bandplans(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> tuple[RadioReferenceTrunkBandplan, ...]:
    items, trail = _array_items(
        element,
        context,
        trail,
        expected_item_type="trsBandplanDef",
    )
    return tuple(_parse_bandplan(item, context, trail) for item in items)


def _parse_fleetmap(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> RadioReferenceTrunkFleetmap:
    names = tuple(f"b{index}" for index in range(8))
    fields, trail = _members(
        element,
        context,
        trail,
        expected_type="TrsFleetmap",
        expected_names=names,
    )
    return RadioReferenceTrunkFleetmap(
        block_0=_string(fields["b0"], context, trail),
        block_1=_string(fields["b1"], context, trail),
        block_2=_string(fields["b2"], context, trail),
        block_3=_string(fields["b3"], context, trail),
        block_4=_string(fields["b4"], context, trail),
        block_5=_string(fields["b5"], context, trail),
        block_6=_string(fields["b6"], context, trail),
        block_7=_string(fields["b7"], context, trail),
    )


def _parse_trunk_system(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> RadioReferenceTrunkSystem:
    fields, trail = _members(
        element,
        context,
        trail,
        expected_type="Trs",
        expected_names=(
            "sName",
            "sType",
            "sFlavor",
            "sVoice",
            "sCity",
            "sCounty",
            "sState",
            "sCountry",
            "lat",
            "lon",
            "range",
            "rectangles",
            "lastUpdated",
            "sysid",
            "bandplan",
            "fleetmap",
        ),
    )
    return RadioReferenceTrunkSystem(
        name=_string(fields["sName"], context, trail),
        system_type=_integer(fields["sType"], context, trail),
        flavor=_integer(fields["sFlavor"], context, trail),
        voice=_integer(fields["sVoice"], context, trail),
        city=_string(fields["sCity"], context, trail),
        county_ids=_parse_id_array(
            fields["sCounty"],
            context,
            trail,
            wrapper_type="ctid",
            member_name="ctid",
        ),
        state_ids=_parse_id_array(
            fields["sState"],
            context,
            trail,
            wrapper_type="stid",
            member_name="stid",
        ),
        country=_string(fields["sCountry"], context, trail),
        latitude=_decimal(fields["lat"], context, trail),
        longitude=_decimal(fields["lon"], context, trail),
        range=_decimal(fields["range"], context, trail),
        rectangles=_parse_rectangles(fields["rectangles"], context, trail),
        last_updated=_datetime(fields["lastUpdated"], context, trail),
        system_ids=_parse_system_ids(fields["sysid"], context, trail),
        bandplan=_parse_bandplans(fields["bandplan"], context, trail),
        fleetmap=_parse_fleetmap(fields["fleetmap"], context, trail),
    )


def _parse_site_frequency(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> RadioReferenceTrunkSiteFrequency:
    fields, trail = _members(
        element,
        context,
        trail,
        expected_type="TrsSiteFreq",
        expected_names=("lcn", "freq", "use", "colorCode", "ch_id"),
    )
    return RadioReferenceTrunkSiteFrequency(
        logical_channel_number=_integer(fields["lcn"], context, trail),
        frequency=_decimal(fields["freq"], context, trail),
        use=_string(fields["use"], context, trail),
        color_code=_string(fields["colorCode"], context, trail),
        channel_id=_string(fields["ch_id"], context, trail),
    )


def _parse_site_frequencies(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> tuple[RadioReferenceTrunkSiteFrequency, ...]:
    items, trail = _array_items(
        element,
        context,
        trail,
        expected_item_type="TrsSiteFreq",
    )
    return tuple(
        _parse_site_frequency(item, context, trail)
        for item in items
    )


def _parse_site_license(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> RadioReferenceTrunkSiteLicense:
    fields, trail = _members(
        element,
        context,
        trail,
        expected_type="TrsSiteLicense",
        expected_names=("license",),
    )
    return RadioReferenceTrunkSiteLicense(
        license=_string(fields["license"], context, trail)
    )


def _parse_site_licenses(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> tuple[RadioReferenceTrunkSiteLicense, ...]:
    items, trail = _array_items(
        element,
        context,
        trail,
        expected_item_type="TrsSiteLicense",
    )
    return tuple(
        _parse_site_license(item, context, trail)
        for item in items
    )


def _parse_trunk_site(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> RadioReferenceTrunkSite:
    fields, trail = _members(
        element,
        context,
        trail,
        expected_type="TrsSite",
        expected_names=(
            "siteId",
            "sid",
            "siteNumber",
            "siteDescr",
            "zoneNumber",
            "zoneDescr",
            "rfss",
            "nac",
            "ran",
            "siteNeighbors",
            "siteLocation",
            "siteCtid",
            "siteCt",
            "siteModulation",
            "siteNotes",
            "lat",
            "lon",
            "range",
            "rectangles",
            "splinter",
            "rebanded",
            "tdma_cc",
            "siteLicenses",
            "siteFreqs",
            "bandplan",
        ),
    )
    return RadioReferenceTrunkSite(
        site_id=_integer(fields["siteId"], context, trail),
        system_id=_integer(fields["sid"], context, trail),
        site_number=_integer(fields["siteNumber"], context, trail),
        description=_string(fields["siteDescr"], context, trail),
        zone_number=_integer(fields["zoneNumber"], context, trail),
        zone_description=_string(fields["zoneDescr"], context, trail),
        rfss=_integer(fields["rfss"], context, trail),
        nac=_string(fields["nac"], context, trail),
        ran=_integer(fields["ran"], context, trail),
        neighbors=_string(fields["siteNeighbors"], context, trail),
        location=_string(fields["siteLocation"], context, trail),
        county_id=_integer(fields["siteCtid"], context, trail),
        county=_string(fields["siteCt"], context, trail),
        modulation=_string(fields["siteModulation"], context, trail),
        notes=_string(fields["siteNotes"], context, trail),
        latitude=_decimal(fields["lat"], context, trail),
        longitude=_decimal(fields["lon"], context, trail),
        range=_decimal(fields["range"], context, trail),
        rectangles=_parse_rectangles(fields["rectangles"], context, trail),
        splinter=_integer(fields["splinter"], context, trail),
        rebanded=_integer(fields["rebanded"], context, trail),
        tdma_control_channel=_integer(fields["tdma_cc"], context, trail),
        licenses=_parse_site_licenses(
            fields["siteLicenses"],
            context,
            trail,
        ),
        frequencies=_parse_site_frequencies(
            fields["siteFreqs"],
            context,
            trail,
        ),
        bandplan=_parse_bandplans(fields["bandplan"], context, trail),
    )


def _parse_trunk_sites(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> tuple[RadioReferenceTrunkSite, ...]:
    items, trail = _array_items(
        element,
        context,
        trail,
        expected_item_type="TrsSite",
    )
    return tuple(_parse_trunk_site(item, context, trail) for item in items)


def _parse_trunk_type(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> RadioReferenceTrunkType:
    fields, trail = _members(
        element,
        context,
        trail,
        expected_type="trsTypeDef",
        expected_names=("sType", "sTypeDescr"),
    )
    return RadioReferenceTrunkType(
        system_type=_integer(fields["sType"], context, trail),
        description=_string(fields["sTypeDescr"], context, trail),
    )


def _parse_trunk_types(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> tuple[RadioReferenceTrunkType, ...]:
    items, trail = _array_items(
        element,
        context,
        trail,
        expected_item_type="trsTypeDef",
    )
    return tuple(_parse_trunk_type(item, context, trail) for item in items)


def _parse_trunk_flavor(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> RadioReferenceTrunkFlavor:
    fields, trail = _members(
        element,
        context,
        trail,
        expected_type="trsFlavorDef",
        expected_names=("sType", "sFlavor", "sFlavorDescr"),
    )
    return RadioReferenceTrunkFlavor(
        system_type=_integer(fields["sType"], context, trail),
        flavor=_integer(fields["sFlavor"], context, trail),
        description=_string(fields["sFlavorDescr"], context, trail),
    )


def _parse_trunk_flavors(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> tuple[RadioReferenceTrunkFlavor, ...]:
    items, trail = _array_items(
        element,
        context,
        trail,
        expected_item_type="trsFlavorDef",
    )
    return tuple(
        _parse_trunk_flavor(item, context, trail)
        for item in items
    )


def _parse_trunk_voice(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> RadioReferenceTrunkVoice:
    fields, trail = _members(
        element,
        context,
        trail,
        expected_type="trsVoiceDef",
        expected_names=("sType", "sVoice", "sVoiceDescr"),
    )
    return RadioReferenceTrunkVoice(
        system_type=_integer(fields["sType"], context, trail),
        voice=_integer(fields["sVoice"], context, trail),
        description=_string(fields["sVoiceDescr"], context, trail),
    )


def _parse_trunk_voices(
    element: ET.Element,
    context: _Context,
    trail: tuple[str, ...],
) -> tuple[RadioReferenceTrunkVoice, ...]:
    items, trail = _array_items(
        element,
        context,
        trail,
        expected_item_type="trsVoiceDef",
    )
    return tuple(_parse_trunk_voice(item, context, trail) for item in items)


def _index_document(
    root: ET.Element,
    *,
    max_references: int,
) -> dict[str, ET.Element]:
    references: dict[str, ET.Element] = {}

    for element in root.iter():
        reference_id = element.attrib.get("id")
        if reference_id is not None:
            if not reference_id or reference_id in references:
                raise _DecodeFailure
            references[reference_id] = element
            if len(references) > max_references:
                raise _DecodeFailure

        href = element.attrib.get("href")
        if href is not None and (
            not href.startswith("#") or len(href) == 1
        ):
            raise _DecodeFailure

    return references


def _validate_reference_graph(
    root: ET.Element,
    context: _Context,
) -> None:
    for element in root.iter():
        if "href" in element.attrib:
            _resolve(element, context, ())


def _soap_return(
    root: ET.Element,
    operation: RadioReferenceWsdlOperation,
) -> ET.Element:
    if root.tag != _SOAP_ENVELOPE:
        raise _DecodeFailure

    envelope_children = tuple(root)
    body_nodes = tuple(
        child for child in envelope_children if child.tag == _SOAP_BODY
    )
    header_nodes = tuple(
        child for child in envelope_children if child.tag == _SOAP_HEADER
    )
    if len(body_nodes) != 1 or len(header_nodes) > 1:
        raise _DecodeFailure
    if any(
        child.tag not in {_SOAP_HEADER, _SOAP_BODY}
        for child in envelope_children
    ):
        raise _DecodeFailure

    body = body_nodes[0]
    if body.text is not None and body.text.strip():
        raise _DecodeFailure

    if any(child.tag == _SOAP_FAULT for child in body):
        raise _DecodeFailure

    expected_response = (
        f"{{{RADIOREFERENCE_SOAP_NAMESPACE}}}"
        f"{operation.value}Response"
    )
    response_nodes = tuple(
        child for child in body if child.tag == expected_response
    )
    if len(response_nodes) != 1:
        raise _DecodeFailure

    response = response_nodes[0]
    for child in body:
        if child is response:
            continue
        if child.attrib.get("id") is None:
            raise _DecodeFailure

    _require_complex_text(response)
    return_nodes = tuple(
        child for child in response if _local_name(child.tag) == "return"
    )
    if len(return_nodes) != 1 or len(response) != 1:
        raise _DecodeFailure

    contract = radioreference_operation_contract(operation)
    return_node = return_nodes[0]
    declared = return_node.attrib.get(_XSI_TYPE)
    if declared is not None:
        declared_name = _local_name(declared)
        expected_name = _local_name(contract.response_type)
        if declared_name not in {expected_name, "Array"}:
            raise _DecodeFailure

    _require_no_nil(return_node)
    return return_node


def _decode_result(
    operation: RadioReferenceWsdlOperation,
    element: ET.Element,
    context: _Context,
) -> RadioReferenceSoapResult:
    trail: tuple[str, ...] = ()

    if operation is RadioReferenceWsdlOperation.GET_COUNTRY_INFO:
        return _parse_country_info(element, context, trail)
    if operation is RadioReferenceWsdlOperation.GET_STATE_INFO:
        return _parse_state_info(element, context, trail)
    if operation is RadioReferenceWsdlOperation.GET_COUNTY_INFO:
        return _parse_county_info(element, context, trail)
    if operation is RadioReferenceWsdlOperation.GET_AGENCY_INFO:
        return _parse_agency_info(element, context, trail)
    if operation in {
        RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
        RadioReferenceWsdlOperation.GET_COUNTY_FREQUENCIES_BY_TAG,
        RadioReferenceWsdlOperation.GET_AGENCY_FREQUENCIES_BY_TAG,
    }:
        return _parse_frequencies(element, context, trail)
    if operation in {
        RadioReferenceWsdlOperation.SEARCH_COUNTY_FREQUENCY,
        RadioReferenceWsdlOperation.SEARCH_STATE_FREQUENCY,
        RadioReferenceWsdlOperation.SEARCH_METRO_FREQUENCY,
    }:
        return _parse_search_frequencies(element, context, trail)
    if operation is RadioReferenceWsdlOperation.GET_TRUNKED_SYSTEM_DETAILS:
        return _parse_trunk_system(element, context, trail)
    if operation is RadioReferenceWsdlOperation.GET_TRUNKED_SYSTEM_SITES:
        return _parse_trunk_sites(element, context, trail)
    if operation is RadioReferenceWsdlOperation.GET_TRUNKED_TALKGROUP_CATEGORIES:
        return _parse_talkgroup_categories(element, context, trail)
    if operation is RadioReferenceWsdlOperation.GET_TRUNKED_TALKGROUPS:
        return _parse_talkgroups(element, context, trail)
    if operation is RadioReferenceWsdlOperation.GET_TAG:
        return _parse_tags(element, context, trail)
    if operation is RadioReferenceWsdlOperation.GET_MODE:
        return _parse_modes(element, context, trail)
    if operation is RadioReferenceWsdlOperation.GET_TRUNKED_TYPE:
        return _parse_trunk_types(element, context, trail)
    if operation is RadioReferenceWsdlOperation.GET_TRUNKED_FLAVOR:
        return _parse_trunk_flavors(element, context, trail)
    if operation is RadioReferenceWsdlOperation.GET_TRUNKED_VOICE:
        return _parse_trunk_voices(element, context, trail)

    raise _DecodeFailure


@dataclass(frozen=True, slots=True)
class RadioReferenceSoapDecoder:
    """Decode bounded offline SOAP responses into immutable provider DTOs."""

    max_document_bytes: int = RADIOREFERENCE_SOAP_DEFAULT_MAX_DOCUMENT_BYTES
    max_elements: int = RADIOREFERENCE_SOAP_DEFAULT_MAX_ELEMENTS
    max_references: int = RADIOREFERENCE_SOAP_DEFAULT_MAX_REFERENCES
    max_reference_depth: int = RADIOREFERENCE_SOAP_DEFAULT_MAX_REFERENCE_DEPTH

    def __post_init__(self) -> None:
        _validate_positive_limit(
            self.max_document_bytes,
            label="RadioReference SOAP document-byte limit",
        )
        _validate_positive_limit(
            self.max_elements,
            label="RadioReference SOAP element limit",
        )
        _validate_positive_limit(
            self.max_references,
            label="RadioReference SOAP reference limit",
        )
        _validate_positive_limit(
            self.max_reference_depth,
            label="RadioReference SOAP reference-depth limit",
        )

    def decode(
        self,
        operation: RadioReferenceWsdlOperation,
        xml: bytes,
    ) -> RadioReferenceSoapResult:
        """Decode one sanitized/offline response with redacted failures."""

        if not isinstance(operation, RadioReferenceWsdlOperation):
            raise TypeError(
                "RadioReference SOAP operation must be "
                "RadioReferenceWsdlOperation."
            )
        if type(xml) is not bytes:
            raise TypeError("RadioReference SOAP response must be bytes.")

        try:
            if not xml or len(xml) > self.max_document_bytes:
                raise _DecodeFailure

            root = _parse_document(
                xml,
                max_elements=self.max_elements,
            )
            references = _index_document(
                root,
                max_references=self.max_references,
            )
            context = _Context(
                references=references,
                max_reference_depth=self.max_reference_depth,
            )
            _validate_reference_graph(root, context)
            return_node = _soap_return(root, operation)
            return _decode_result(
                operation,
                return_node,
                context,
            )
        except (
            _DecodeFailure,
            ET.ParseError,
            InvalidOperation,
            OverflowError,
            TypeError,
            ValueError,
        ):
            raise _invalid_response() from None


__all__ = [
    "RADIOREFERENCE_SOAP_DEFAULT_MAX_DOCUMENT_BYTES",
    "RADIOREFERENCE_SOAP_DEFAULT_MAX_ELEMENTS",
    "RADIOREFERENCE_SOAP_DEFAULT_MAX_REFERENCES",
    "RADIOREFERENCE_SOAP_DEFAULT_MAX_REFERENCE_DEPTH",
    "RADIOREFERENCE_SOAP_ENCODING_NAMESPACE",
    "RADIOREFERENCE_SOAP_ENVELOPE_NAMESPACE",
    "RADIOREFERENCE_XML_SCHEMA_INSTANCE_NAMESPACE",
    "RadioReferenceSoapDecoder",
    "RadioReferenceSoapResult",
]
