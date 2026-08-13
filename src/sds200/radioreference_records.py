"""Immutable RadioReference WSDL contract and provider-record DTO foundation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final

RADIOREFERENCE_SOAP_NAMESPACE: Final = "http://api.radioreference.com/soap2"
RADIOREFERENCE_SOAP_ENCODING_STYLE: Final = (
    "http://schemas.xmlsoap.org/soap/encoding/"
)
RADIOREFERENCE_WSDL_EVIDENCE_SHA256: Final = (
    "1bb8090cf6415e429eb432dd964b1d26164af7eb2240a8b6d345007821d12f33"
)


class RadioReferenceWsdlOperation(StrEnum):
    """Programming-relevant operations from the reviewed RadioReference WSDL."""

    GET_COUNTRY_INFO = "getCountryInfo"
    GET_STATE_INFO = "getStateInfo"
    GET_COUNTY_INFO = "getCountyInfo"
    GET_AGENCY_INFO = "getAgencyInfo"
    GET_SUBCATEGORY_FREQUENCIES = "getSubcatFreqs"
    GET_COUNTY_FREQUENCIES_BY_TAG = "getCountyFreqsByTag"
    GET_AGENCY_FREQUENCIES_BY_TAG = "getAgencyFreqsByTag"
    SEARCH_COUNTY_FREQUENCY = "searchCountyFreq"
    SEARCH_STATE_FREQUENCY = "searchStateFreq"
    SEARCH_METRO_FREQUENCY = "searchMetroFreq"
    GET_TRUNKED_SYSTEM_DETAILS = "getTrsDetails"
    GET_TRUNKED_SYSTEM_SITES = "getTrsSites"
    GET_TRUNKED_TALKGROUP_CATEGORIES = "getTrsTalkgroupCats"
    GET_TRUNKED_TALKGROUPS = "getTrsTalkgroups"
    GET_TAG = "getTag"
    GET_MODE = "getMode"
    GET_TRUNKED_TYPE = "getTrsType"
    GET_TRUNKED_FLAVOR = "getTrsFlavor"
    GET_TRUNKED_VOICE = "getTrsVoice"


@dataclass(frozen=True, slots=True)
class RadioReferenceWsdlParameter:
    """One exact request-message part from the reviewed WSDL."""

    name: str
    type_name: str


@dataclass(frozen=True, slots=True)
class RadioReferenceWsdlOperationContract:
    """Exact reviewed RPC/encoded request and response contract."""

    operation: RadioReferenceWsdlOperation
    request_parameters: tuple[RadioReferenceWsdlParameter, ...]
    response_type: str

    @property
    def soap_action(self) -> str:
        """Return the reviewed SOAP action for this RPC operation."""

        return f"{RADIOREFERENCE_SOAP_NAMESPACE}#{self.operation.value}"

    @property
    def authenticated(self) -> bool:
        """Return whether the reviewed request contains an authInfo part."""

        return any(
            parameter.name == "authInfo"
            and parameter.type_name == "tns:authInfo"
            for parameter in self.request_parameters
        )


def _parameter(name: str, type_name: str) -> RadioReferenceWsdlParameter:
    return RadioReferenceWsdlParameter(name=name, type_name=type_name)


RADIOREFERENCE_PROGRAMMING_OPERATION_CONTRACTS: Final[
    tuple[RadioReferenceWsdlOperationContract, ...]
] = (
    RadioReferenceWsdlOperationContract(
        RadioReferenceWsdlOperation.GET_COUNTRY_INFO,
        (_parameter("coid", "xsd:int"), _parameter("authInfo", "tns:authInfo")),
        "tns:CountryInfo",
    ),
    RadioReferenceWsdlOperationContract(
        RadioReferenceWsdlOperation.GET_STATE_INFO,
        (_parameter("stid", "xsd:int"), _parameter("authInfo", "tns:authInfo")),
        "tns:StateInfo",
    ),
    RadioReferenceWsdlOperationContract(
        RadioReferenceWsdlOperation.GET_COUNTY_INFO,
        (_parameter("ctid", "xsd:int"), _parameter("authInfo", "tns:authInfo")),
        "tns:CountyInfo",
    ),
    RadioReferenceWsdlOperationContract(
        RadioReferenceWsdlOperation.GET_AGENCY_INFO,
        (_parameter("aid", "xsd:int"), _parameter("authInfo", "tns:authInfo")),
        "tns:AgencyInfo",
    ),
    RadioReferenceWsdlOperationContract(
        RadioReferenceWsdlOperation.GET_SUBCATEGORY_FREQUENCIES,
        (_parameter("scid", "xsd:int"), _parameter("authInfo", "tns:authInfo")),
        "tns:Freqs",
    ),
    RadioReferenceWsdlOperationContract(
        RadioReferenceWsdlOperation.GET_COUNTY_FREQUENCIES_BY_TAG,
        (
            _parameter("ctid", "xsd:int"),
            _parameter("tag", "xsd:int"),
            _parameter("authInfo", "tns:authInfo"),
        ),
        "tns:Freqs",
    ),
    RadioReferenceWsdlOperationContract(
        RadioReferenceWsdlOperation.GET_AGENCY_FREQUENCIES_BY_TAG,
        (
            _parameter("aid", "xsd:int"),
            _parameter("tag", "xsd:int"),
            _parameter("authInfo", "tns:authInfo"),
        ),
        "tns:Freqs",
    ),
    RadioReferenceWsdlOperationContract(
        RadioReferenceWsdlOperation.SEARCH_COUNTY_FREQUENCY,
        (
            _parameter("ctid", "xsd:int"),
            _parameter("freq", "xsd:decimal"),
            _parameter("tone", "xsd:string"),
            _parameter("authInfo", "tns:authInfo"),
        ),
        "tns:searchFreqResults",
    ),
    RadioReferenceWsdlOperationContract(
        RadioReferenceWsdlOperation.SEARCH_STATE_FREQUENCY,
        (
            _parameter("stid", "xsd:int"),
            _parameter("freq", "xsd:decimal"),
            _parameter("tone", "xsd:string"),
            _parameter("authInfo", "tns:authInfo"),
        ),
        "tns:searchFreqResults",
    ),
    RadioReferenceWsdlOperationContract(
        RadioReferenceWsdlOperation.SEARCH_METRO_FREQUENCY,
        (
            _parameter("mid", "xsd:int"),
            _parameter("freq", "xsd:decimal"),
            _parameter("tone", "xsd:string"),
            _parameter("authInfo", "tns:authInfo"),
        ),
        "tns:searchFreqResults",
    ),
    RadioReferenceWsdlOperationContract(
        RadioReferenceWsdlOperation.GET_TRUNKED_SYSTEM_DETAILS,
        (_parameter("sid", "xsd:int"), _parameter("authInfo", "tns:authInfo")),
        "tns:Trs",
    ),
    RadioReferenceWsdlOperationContract(
        RadioReferenceWsdlOperation.GET_TRUNKED_SYSTEM_SITES,
        (_parameter("sid", "xsd:int"), _parameter("authInfo", "tns:authInfo")),
        "tns:TrsSites",
    ),
    RadioReferenceWsdlOperationContract(
        RadioReferenceWsdlOperation.GET_TRUNKED_TALKGROUP_CATEGORIES,
        (_parameter("sid", "xsd:int"), _parameter("authInfo", "tns:authInfo")),
        "tns:TalkgroupCats",
    ),
    RadioReferenceWsdlOperationContract(
        RadioReferenceWsdlOperation.GET_TRUNKED_TALKGROUPS,
        (
            _parameter("sid", "xsd:int"),
            _parameter("tgCid", "xsd:int"),
            _parameter("tgTag", "xsd:int"),
            _parameter("tgDec", "xsd:int"),
            _parameter("authInfo", "tns:authInfo"),
        ),
        "tns:Talkgroups",
    ),
    RadioReferenceWsdlOperationContract(
        RadioReferenceWsdlOperation.GET_TAG,
        (_parameter("id", "xsd:int"), _parameter("authInfo", "tns:authInfo")),
        "tns:tags",
    ),
    RadioReferenceWsdlOperationContract(
        RadioReferenceWsdlOperation.GET_MODE,
        (_parameter("mode", "xsd:int"), _parameter("authInfo", "tns:authInfo")),
        "tns:modes",
    ),
    RadioReferenceWsdlOperationContract(
        RadioReferenceWsdlOperation.GET_TRUNKED_TYPE,
        (_parameter("id", "xsd:int"), _parameter("authInfo", "tns:authInfo")),
        "tns:TrsType",
    ),
    RadioReferenceWsdlOperationContract(
        RadioReferenceWsdlOperation.GET_TRUNKED_FLAVOR,
        (_parameter("id", "xsd:int"), _parameter("authInfo", "tns:authInfo")),
        "tns:TrsFlavor",
    ),
    RadioReferenceWsdlOperationContract(
        RadioReferenceWsdlOperation.GET_TRUNKED_VOICE,
        (_parameter("id", "xsd:int"), _parameter("authInfo", "tns:authInfo")),
        "tns:TrsVoice",
    ),
)

_OPERATION_CONTRACTS: Final = {
    contract.operation: contract
    for contract in RADIOREFERENCE_PROGRAMMING_OPERATION_CONTRACTS
}


def radioreference_operation_contract(
    operation: RadioReferenceWsdlOperation,
) -> RadioReferenceWsdlOperationContract:
    """Return the immutable reviewed WSDL contract for one operation."""

    if not isinstance(operation, RadioReferenceWsdlOperation):
        raise TypeError(
            "RadioReference WSDL operation must be RadioReferenceWsdlOperation."
        )
    return _OPERATION_CONTRACTS[operation]


_XSD_INT_MIN = -(2**31)
_XSD_INT_MAX = 2**31 - 1


def _require_xsd_int(value: int, *, label: str) -> None:
    if type(value) is not int:
        raise TypeError(f"{label} must be an xsd:int-compatible integer.")
    if not _XSD_INT_MIN <= value <= _XSD_INT_MAX:
        raise ValueError(f"{label} is outside the xsd:int range.")


def _require_xsd_string(value: str, *, label: str) -> None:
    if type(value) is not str:
        raise TypeError(f"{label} must be a string.")


def _require_xsd_decimal(value: Decimal, *, label: str) -> None:
    if type(value) is not Decimal:
        raise TypeError(f"{label} must be Decimal for xsd:decimal.")


def _require_xsd_boolean(value: bool, *, label: str) -> None:
    if type(value) is not bool:
        raise TypeError(f"{label} must be bool for xsd:boolean.")


def _require_xsd_datetime(value: datetime, *, label: str) -> None:
    if type(value) is not datetime:
        raise TypeError(f"{label} must be datetime for xsd:dateTime.")


def _require_tuple_of(
    value: tuple[object, ...],
    item_type: type[object],
    *,
    label: str,
) -> None:
    if type(value) is not tuple:
        raise TypeError(f"{label} must be an immutable tuple.")
    if any(not isinstance(item, item_type) for item in value):
        raise TypeError(f"{label} contains an invalid item type.")


def _require_int_tuple(value: tuple[int, ...], *, label: str) -> None:
    if type(value) is not tuple:
        raise TypeError(f"{label} must be an immutable tuple.")
    for item in value:
        _require_xsd_int(item, label=f"{label} item")


@dataclass(frozen=True, slots=True)
class RadioReferenceRectangle:
    """Provider location rectangle from the reviewed WSDL."""

    northwest_latitude: Decimal
    northwest_longitude: Decimal
    southeast_latitude: Decimal
    southeast_longitude: Decimal

    def __post_init__(self) -> None:
        _require_xsd_decimal(self.northwest_latitude, label="northwest latitude")
        _require_xsd_decimal(self.northwest_longitude, label="northwest longitude")
        _require_xsd_decimal(self.southeast_latitude, label="southeast latitude")
        _require_xsd_decimal(self.southeast_longitude, label="southeast longitude")


@dataclass(frozen=True, slots=True)
class RadioReferenceTag:
    """Provider tag record."""

    tag_id: int
    description: str

    def __post_init__(self) -> None:
        _require_xsd_int(self.tag_id, label="tag ID")
        _require_xsd_string(self.description, label="tag description")


@dataclass(frozen=True, slots=True)
class RadioReferenceMode:
    """Provider mode record."""

    mode: int
    name: str

    def __post_init__(self) -> None:
        _require_xsd_int(self.mode, label="mode")
        _require_xsd_string(self.name, label="mode name")


@dataclass(frozen=True, slots=True)
class RadioReferenceAgency:
    """Provider agency summary record."""

    agency_id: int
    name: str
    agency_type: int

    def __post_init__(self) -> None:
        _require_xsd_int(self.agency_id, label="agency ID")
        _require_xsd_string(self.name, label="agency name")
        _require_xsd_int(self.agency_type, label="agency type")


@dataclass(frozen=True, slots=True)
class RadioReferenceCounty:
    """Provider county summary record."""

    county_id: int
    name: str
    header: str

    def __post_init__(self) -> None:
        _require_xsd_int(self.county_id, label="county ID")
        _require_xsd_string(self.name, label="county name")
        _require_xsd_string(self.header, label="county header")


@dataclass(frozen=True, slots=True)
class RadioReferenceState:
    """Provider state summary record."""

    state_id: int
    name: str
    code: str

    def __post_init__(self) -> None:
        _require_xsd_int(self.state_id, label="state ID")
        _require_xsd_string(self.name, label="state name")
        _require_xsd_string(self.code, label="state code")


@dataclass(frozen=True, slots=True)
class RadioReferenceSubcategory:
    """Provider conventional subcategory record."""

    subcategory_id: int
    name: str
    latitude: Decimal
    longitude: Decimal
    range: Decimal
    rectangles: tuple[RadioReferenceRectangle, ...]
    trunked_system_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        _require_xsd_int(self.subcategory_id, label="subcategory ID")
        _require_xsd_string(self.name, label="subcategory name")
        _require_xsd_decimal(self.latitude, label="subcategory latitude")
        _require_xsd_decimal(self.longitude, label="subcategory longitude")
        _require_xsd_decimal(self.range, label="subcategory range")
        _require_tuple_of(
            self.rectangles,
            RadioReferenceRectangle,
            label="subcategory rectangles",
        )
        _require_int_tuple(
            self.trunked_system_ids,
            label="subcategory trunked-system IDs",
        )


@dataclass(frozen=True, slots=True)
class RadioReferenceCategory:
    """Provider conventional category record."""

    category_id: int
    name: str
    subcategories: tuple[RadioReferenceSubcategory, ...]

    def __post_init__(self) -> None:
        _require_xsd_int(self.category_id, label="category ID")
        _require_xsd_string(self.name, label="category name")
        _require_tuple_of(
            self.subcategories,
            RadioReferenceSubcategory,
            label="category subcategories",
        )


@dataclass(frozen=True, slots=True)
class RadioReferenceTrunkListEntry:
    """Provider trunked-system list record."""

    system_id: int
    name: str
    system_type: int
    flavor: int
    voice: int
    city: str
    last_updated: datetime

    def __post_init__(self) -> None:
        _require_xsd_int(self.system_id, label="trunked-system ID")
        _require_xsd_string(self.name, label="trunked-system name")
        _require_xsd_int(self.system_type, label="trunked-system type")
        _require_xsd_int(self.flavor, label="trunked-system flavor")
        _require_xsd_int(self.voice, label="trunked-system voice")
        _require_xsd_string(self.city, label="trunked-system city")
        _require_xsd_datetime(self.last_updated, label="trunked-system lastUpdated")


@dataclass(frozen=True, slots=True)
class RadioReferenceCountryInfo:
    """Provider country-info record."""

    country_id: int
    name: str
    code: str
    agencies: tuple[RadioReferenceAgency, ...]
    states: tuple[RadioReferenceState, ...]

    def __post_init__(self) -> None:
        _require_xsd_int(self.country_id, label="country ID")
        _require_xsd_string(self.name, label="country name")
        _require_xsd_string(self.code, label="country code")
        _require_tuple_of(self.agencies, RadioReferenceAgency, label="country agencies")
        _require_tuple_of(self.states, RadioReferenceState, label="country states")


@dataclass(frozen=True, slots=True)
class RadioReferenceStateInfo:
    """Provider state-info record."""

    state_id: int
    name: str
    entity_type: str
    trunked_systems: tuple[RadioReferenceTrunkListEntry, ...]
    agencies: tuple[RadioReferenceAgency, ...]
    counties: tuple[RadioReferenceCounty, ...]

    def __post_init__(self) -> None:
        _require_xsd_int(self.state_id, label="state ID")
        _require_xsd_string(self.name, label="state name")
        _require_xsd_string(self.entity_type, label="state entity type")
        _require_tuple_of(
            self.trunked_systems,
            RadioReferenceTrunkListEntry,
            label="state trunked systems",
        )
        _require_tuple_of(self.agencies, RadioReferenceAgency, label="state agencies")
        _require_tuple_of(self.counties, RadioReferenceCounty, label="state counties")


@dataclass(frozen=True, slots=True)
class RadioReferenceCountyInfo:
    """Provider county-info record."""

    county_id: int
    name: str
    header: str
    state_id: int
    fips: str
    last_updated: datetime
    latitude: Decimal
    longitude: Decimal
    range: Decimal
    rectangles: tuple[RadioReferenceRectangle, ...]
    categories: tuple[RadioReferenceCategory, ...]
    trunked_systems: tuple[RadioReferenceTrunkListEntry, ...]
    agencies: tuple[RadioReferenceAgency, ...]

    def __post_init__(self) -> None:
        _require_xsd_int(self.county_id, label="county ID")
        _require_xsd_string(self.name, label="county name")
        _require_xsd_string(self.header, label="county header")
        _require_xsd_int(self.state_id, label="county state ID")
        _require_xsd_string(self.fips, label="county FIPS")
        _require_xsd_datetime(self.last_updated, label="county lastUpdated")
        _require_xsd_decimal(self.latitude, label="county latitude")
        _require_xsd_decimal(self.longitude, label="county longitude")
        _require_xsd_decimal(self.range, label="county range")
        _require_tuple_of(
            self.rectangles,
            RadioReferenceRectangle,
            label="county rectangles",
        )
        _require_tuple_of(
            self.categories,
            RadioReferenceCategory,
            label="county categories",
        )
        _require_tuple_of(
            self.trunked_systems,
            RadioReferenceTrunkListEntry,
            label="county trunked systems",
        )
        _require_tuple_of(self.agencies, RadioReferenceAgency, label="county agencies")


@dataclass(frozen=True, slots=True)
class RadioReferenceAgencyInfo:
    """Provider agency-info record."""

    agency_id: int
    name: str
    agency_type: str
    county_id: int
    state_id: int
    latitude: Decimal
    longitude: Decimal
    range: Decimal
    rectangles: tuple[RadioReferenceRectangle, ...]
    last_updated: datetime
    categories: tuple[RadioReferenceCategory, ...]

    def __post_init__(self) -> None:
        _require_xsd_int(self.agency_id, label="agency ID")
        _require_xsd_string(self.name, label="agency name")
        _require_xsd_string(self.agency_type, label="agency type")
        _require_xsd_int(self.county_id, label="agency county ID")
        _require_xsd_int(self.state_id, label="agency state ID")
        _require_xsd_decimal(self.latitude, label="agency latitude")
        _require_xsd_decimal(self.longitude, label="agency longitude")
        _require_xsd_decimal(self.range, label="agency range")
        _require_tuple_of(
            self.rectangles,
            RadioReferenceRectangle,
            label="agency rectangles",
        )
        _require_xsd_datetime(self.last_updated, label="agency lastUpdated")
        _require_tuple_of(
            self.categories,
            RadioReferenceCategory,
            label="agency categories",
        )


@dataclass(frozen=True, slots=True)
class RadioReferenceFrequency:
    """Provider conventional frequency record."""

    frequency_id: int
    output_frequency: Decimal
    input_frequency: Decimal
    callsign: str
    description: str
    alpha_tag: str
    tone: str
    color_code: str
    talkgroup: str
    slot: str
    mode: str
    encryption: int
    class_code: str
    tags: tuple[RadioReferenceTag, ...]
    subcategory_id: int
    sort: int
    last_updated: datetime

    def __post_init__(self) -> None:
        _require_xsd_int(self.frequency_id, label="frequency ID")
        _require_xsd_decimal(self.output_frequency, label="output frequency")
        _require_xsd_decimal(self.input_frequency, label="input frequency")
        _require_xsd_string(self.callsign, label="callsign")
        _require_xsd_string(self.description, label="frequency description")
        _require_xsd_string(self.alpha_tag, label="frequency alpha tag")
        _require_xsd_string(self.tone, label="frequency tone")
        _require_xsd_string(self.color_code, label="frequency color code")
        _require_xsd_string(self.talkgroup, label="frequency talkgroup")
        _require_xsd_string(self.slot, label="frequency slot")
        _require_xsd_string(self.mode, label="frequency mode")
        _require_xsd_int(self.encryption, label="frequency encryption")
        _require_xsd_string(self.class_code, label="frequency class")
        _require_tuple_of(self.tags, RadioReferenceTag, label="frequency tags")
        _require_xsd_int(self.subcategory_id, label="frequency subcategory ID")
        _require_xsd_int(self.sort, label="frequency sort")
        _require_xsd_datetime(self.last_updated, label="frequency lastUpdated")


@dataclass(frozen=True, slots=True)
class RadioReferenceSearchFrequencyResult:
    """Provider frequency-search result without inferred record identity."""

    output_frequency: Decimal
    input_frequency: Decimal
    callsign: str
    description: str
    alpha_tag: str
    tone: str
    color_code: str
    talkgroup: str
    slot: str
    mode: str
    class_code: str
    tags: tuple[RadioReferenceTag, ...]
    subcategory_id: int
    system_id: int
    agency_id: int
    county_id: int

    def __post_init__(self) -> None:
        _require_xsd_decimal(self.output_frequency, label="search output frequency")
        _require_xsd_decimal(self.input_frequency, label="search input frequency")
        _require_xsd_string(self.callsign, label="search callsign")
        _require_xsd_string(self.description, label="search description")
        _require_xsd_string(self.alpha_tag, label="search alpha tag")
        _require_xsd_string(self.tone, label="search tone")
        _require_xsd_string(self.color_code, label="search color code")
        _require_xsd_string(self.talkgroup, label="search talkgroup")
        _require_xsd_string(self.slot, label="search slot")
        _require_xsd_string(self.mode, label="search mode")
        _require_xsd_string(self.class_code, label="search class")
        _require_tuple_of(self.tags, RadioReferenceTag, label="search tags")
        _require_xsd_int(self.subcategory_id, label="search subcategory ID")
        _require_xsd_int(self.system_id, label="search system ID")
        _require_xsd_int(self.agency_id, label="search agency ID")
        _require_xsd_int(self.county_id, label="search county ID")


@dataclass(frozen=True, slots=True)
class RadioReferenceTalkgroup:
    """Provider trunked talkgroup record."""

    talkgroup_id: int
    decimal: int
    subfleet: str
    ltr: bool
    slot: str
    description: str
    alpha_tag: str
    mode: str
    encryption: int
    tags: tuple[RadioReferenceTag, ...]
    category_id: int
    sort: int
    date: datetime

    def __post_init__(self) -> None:
        _require_xsd_int(self.talkgroup_id, label="talkgroup ID")
        _require_xsd_int(self.decimal, label="talkgroup decimal")
        _require_xsd_string(self.subfleet, label="talkgroup subfleet")
        _require_xsd_boolean(self.ltr, label="talkgroup LTR")
        _require_xsd_string(self.slot, label="talkgroup slot")
        _require_xsd_string(self.description, label="talkgroup description")
        _require_xsd_string(self.alpha_tag, label="talkgroup alpha tag")
        _require_xsd_string(self.mode, label="talkgroup mode")
        _require_xsd_int(self.encryption, label="talkgroup encryption")
        _require_tuple_of(self.tags, RadioReferenceTag, label="talkgroup tags")
        _require_xsd_int(self.category_id, label="talkgroup category ID")
        _require_xsd_int(self.sort, label="talkgroup sort")
        _require_xsd_datetime(self.date, label="talkgroup tgDate")


@dataclass(frozen=True, slots=True)
class RadioReferenceTalkgroupCategory:
    """Provider trunked talkgroup-category record."""

    category_id: int
    system_id: int
    name: str
    sort: int
    sort_by: int
    latitude: Decimal
    longitude: Decimal
    range: Decimal
    rectangles: tuple[RadioReferenceRectangle, ...]
    last_updated: datetime

    def __post_init__(self) -> None:
        _require_xsd_int(self.category_id, label="talkgroup category ID")
        _require_xsd_int(self.system_id, label="talkgroup category system ID")
        _require_xsd_string(self.name, label="talkgroup category name")
        _require_xsd_int(self.sort, label="talkgroup category sort")
        _require_xsd_int(self.sort_by, label="talkgroup category sort-by")
        _require_xsd_decimal(self.latitude, label="talkgroup category latitude")
        _require_xsd_decimal(self.longitude, label="talkgroup category longitude")
        _require_xsd_decimal(self.range, label="talkgroup category range")
        _require_tuple_of(
            self.rectangles,
            RadioReferenceRectangle,
            label="talkgroup category rectangles",
        )
        _require_xsd_datetime(
            self.last_updated,
            label="talkgroup category lastUpdated",
        )


@dataclass(frozen=True, slots=True)
class RadioReferenceTrunkSystemId:
    """Provider trunked system-identification record."""

    system_id: str
    ct: str
    wacn: str
    model: str

    def __post_init__(self) -> None:
        _require_xsd_string(self.system_id, label="trunked system sysid")
        _require_xsd_string(self.ct, label="trunked system ct")
        _require_xsd_string(self.wacn, label="trunked system WACN")
        _require_xsd_string(self.model, label="trunked system model")


@dataclass(frozen=True, slots=True)
class RadioReferenceTrunkBandplan:
    """Provider trunked bandplan record."""

    base: str
    spacing: str
    offset: str

    def __post_init__(self) -> None:
        _require_xsd_string(self.base, label="bandplan base")
        _require_xsd_string(self.spacing, label="bandplan spacing")
        _require_xsd_string(self.offset, label="bandplan offset")


@dataclass(frozen=True, slots=True)
class RadioReferenceTrunkFleetmap:
    """Provider trunked fleetmap record."""

    block_0: str
    block_1: str
    block_2: str
    block_3: str
    block_4: str
    block_5: str
    block_6: str
    block_7: str

    def __post_init__(self) -> None:
        for index, value in enumerate(
            (
                self.block_0,
                self.block_1,
                self.block_2,
                self.block_3,
                self.block_4,
                self.block_5,
                self.block_6,
                self.block_7,
            )
        ):
            _require_xsd_string(value, label=f"fleetmap block {index}")


@dataclass(frozen=True, slots=True)
class RadioReferenceTrunkSystem:
    """Provider trunked-system details record."""

    name: str
    system_type: int
    flavor: int
    voice: int
    city: str
    county_ids: tuple[int, ...]
    state_ids: tuple[int, ...]
    country: str
    latitude: Decimal
    longitude: Decimal
    range: Decimal
    rectangles: tuple[RadioReferenceRectangle, ...]
    last_updated: datetime
    system_ids: tuple[RadioReferenceTrunkSystemId, ...]
    bandplan: tuple[RadioReferenceTrunkBandplan, ...]
    fleetmap: RadioReferenceTrunkFleetmap

    def __post_init__(self) -> None:
        _require_xsd_string(self.name, label="trunked-system name")
        _require_xsd_int(self.system_type, label="trunked-system type")
        _require_xsd_int(self.flavor, label="trunked-system flavor")
        _require_xsd_int(self.voice, label="trunked-system voice")
        _require_xsd_string(self.city, label="trunked-system city")
        _require_int_tuple(self.county_ids, label="trunked-system county IDs")
        _require_int_tuple(self.state_ids, label="trunked-system state IDs")
        _require_xsd_string(self.country, label="trunked-system country")
        _require_xsd_decimal(self.latitude, label="trunked-system latitude")
        _require_xsd_decimal(self.longitude, label="trunked-system longitude")
        _require_xsd_decimal(self.range, label="trunked-system range")
        _require_tuple_of(
            self.rectangles,
            RadioReferenceRectangle,
            label="trunked-system rectangles",
        )
        _require_xsd_datetime(
            self.last_updated,
            label="trunked-system lastUpdated",
        )
        _require_tuple_of(
            self.system_ids,
            RadioReferenceTrunkSystemId,
            label="trunked-system system IDs",
        )
        _require_tuple_of(
            self.bandplan,
            RadioReferenceTrunkBandplan,
            label="trunked-system bandplan",
        )
        if not isinstance(self.fleetmap, RadioReferenceTrunkFleetmap):
            raise TypeError("trunked-system fleetmap has an invalid type.")


@dataclass(frozen=True, slots=True)
class RadioReferenceTrunkSiteFrequency:
    """Provider trunked-site frequency record."""

    logical_channel_number: int
    frequency: Decimal
    use: str
    color_code: str
    channel_id: str

    def __post_init__(self) -> None:
        _require_xsd_int(
            self.logical_channel_number,
            label="site logical channel number",
        )
        _require_xsd_decimal(self.frequency, label="site frequency")
        _require_xsd_string(self.use, label="site frequency use")
        _require_xsd_string(self.color_code, label="site frequency color code")
        _require_xsd_string(self.channel_id, label="site frequency channel ID")


@dataclass(frozen=True, slots=True)
class RadioReferenceTrunkSiteLicense:
    """Provider trunked-site license record."""

    license: str

    def __post_init__(self) -> None:
        _require_xsd_string(self.license, label="site license")


@dataclass(frozen=True, slots=True)
class RadioReferenceTrunkSite:
    """Provider trunked-site record."""

    site_id: int
    system_id: int
    site_number: int
    description: str
    zone_number: int
    zone_description: str
    rfss: int
    nac: str
    ran: int
    neighbors: str
    location: str
    county_id: int
    county: str
    modulation: str
    notes: str
    latitude: Decimal
    longitude: Decimal
    range: Decimal
    rectangles: tuple[RadioReferenceRectangle, ...]
    splinter: int
    rebanded: int
    tdma_control_channel: int
    licenses: tuple[RadioReferenceTrunkSiteLicense, ...]
    frequencies: tuple[RadioReferenceTrunkSiteFrequency, ...]
    bandplan: tuple[RadioReferenceTrunkBandplan, ...]

    def __post_init__(self) -> None:
        _require_xsd_int(self.site_id, label="site ID")
        _require_xsd_int(self.system_id, label="site system ID")
        _require_xsd_int(self.site_number, label="site number")
        _require_xsd_string(self.description, label="site description")
        _require_xsd_int(self.zone_number, label="site zone number")
        _require_xsd_string(self.zone_description, label="site zone description")
        _require_xsd_int(self.rfss, label="site RFSS")
        _require_xsd_string(self.nac, label="site NAC")
        _require_xsd_int(self.ran, label="site RAN")
        _require_xsd_string(self.neighbors, label="site neighbors")
        _require_xsd_string(self.location, label="site location")
        _require_xsd_int(self.county_id, label="site county ID")
        _require_xsd_string(self.county, label="site county")
        _require_xsd_string(self.modulation, label="site modulation")
        _require_xsd_string(self.notes, label="site notes")
        _require_xsd_decimal(self.latitude, label="site latitude")
        _require_xsd_decimal(self.longitude, label="site longitude")
        _require_xsd_decimal(self.range, label="site range")
        _require_tuple_of(
            self.rectangles,
            RadioReferenceRectangle,
            label="site rectangles",
        )
        _require_xsd_int(self.splinter, label="site splinter")
        _require_xsd_int(self.rebanded, label="site rebanded")
        _require_xsd_int(
            self.tdma_control_channel,
            label="site TDMA control-channel evidence",
        )
        _require_tuple_of(
            self.licenses,
            RadioReferenceTrunkSiteLicense,
            label="site licenses",
        )
        _require_tuple_of(
            self.frequencies,
            RadioReferenceTrunkSiteFrequency,
            label="site frequencies",
        )
        _require_tuple_of(
            self.bandplan,
            RadioReferenceTrunkBandplan,
            label="site bandplan",
        )


@dataclass(frozen=True, slots=True)
class RadioReferenceTrunkType:
    """Provider trunked-system type lookup record."""

    system_type: int
    description: str

    def __post_init__(self) -> None:
        _require_xsd_int(self.system_type, label="trunked type")
        _require_xsd_string(self.description, label="trunked type description")


@dataclass(frozen=True, slots=True)
class RadioReferenceTrunkFlavor:
    """Provider trunked-system flavor lookup record."""

    system_type: int
    flavor: int
    description: str

    def __post_init__(self) -> None:
        _require_xsd_int(self.system_type, label="trunked flavor system type")
        _require_xsd_int(self.flavor, label="trunked flavor")
        _require_xsd_string(self.description, label="trunked flavor description")


@dataclass(frozen=True, slots=True)
class RadioReferenceTrunkVoice:
    """Provider trunked-system voice lookup record."""

    system_type: int
    voice: int
    description: str

    def __post_init__(self) -> None:
        _require_xsd_int(self.system_type, label="trunked voice system type")
        _require_xsd_int(self.voice, label="trunked voice")
        _require_xsd_string(self.description, label="trunked voice description")


__all__ = [
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
]
