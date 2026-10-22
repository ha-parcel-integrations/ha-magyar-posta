"""Tests for the pure parcel-mapping helpers.

These need no Home Assistant instance — the whole point of keeping
``parcels.py`` free of I/O is that the carrier-specific mapping can be tested
as plain functions.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.magyar_posta.const import (
    CAPABILITIES,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DOMAIN,
    KNOWN_CAPABILITIES,
    PENDING_CAPABILITIES,
    ParcelStatus,
)
from custom_components.magyar_posta.parcels import (
    _delivered_timestamp,
    apply_delivered_filter,
    build_history,
    map_parcel_status,
    map_row_status,
    normalize_parcel,
    parse_iso,
    parse_row_datetime,
    row_category,
    row_timestamp,
    sort_parcels_by_ts,
    unique_rows,
)

from .payloads import (
    ACTIVE_CODE,
    DELIVERED_CODE,
    DELIVERED_ROWS,
    active_sample,
    customs_sample,
    delivered_sample,
    in_transit_sample,
    payload,
    pending_sample,
    pickup_sample,
    registered_sample,
    row,
    two_scans_one_moment_sample,
    unknown_category_sample,
)

BUDAPEST = ZoneInfo("Europe/Budapest")

# ---------------------------------------------------------------------------
# map_parcel_status / map_row_status
# ---------------------------------------------------------------------------


def _scan(category, detail=None):
    return row(category, "20261013", "08:00:00", "x", detail)


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        ("1", ParcelStatus.REGISTERED),
        ("2", ParcelStatus.IN_TRANSIT),
        ("3", ParcelStatus.IN_TRANSIT),
        ("4", ParcelStatus.OUT_FOR_DELIVERY),
        ("5", ParcelStatus.DELIVERED),
    ],
)
def test_map_parcel_status_known(category, expected):
    assert map_parcel_status([_scan(category)]) == expected


def test_the_ordinal_bands_do_not_slide():
    """2 and 3 are both still in the network; only 4 is on a vehicle.

    Reading 3 as "out for delivery" — the tempting shift, since it is the last
    band before the courier — would announce every sorting scan as a delivery
    attempt.
    """
    assert map_parcel_status([_scan("3")]) == ParcelStatus.IN_TRANSIT
    assert map_parcel_status([_scan("4")]) == ParcelStatus.OUT_FOR_DELIVERY


def test_map_parcel_status_accepts_a_numeric_category():
    """The ordinal has been seen both as a number and as a string."""
    assert map_parcel_status([_scan(4)]) == ParcelStatus.OUT_FOR_DELIVERY


def test_map_parcel_status_missing_is_unknown():
    assert map_parcel_status(None) == ParcelStatus.UNKNOWN
    assert map_parcel_status([]) == ParcelStatus.UNKNOWN
    assert map_parcel_status([_scan("")]) == ParcelStatus.UNKNOWN


def test_map_parcel_status_unmapped_is_unknown():
    assert map_parcel_status([_scan("9")]) == ParcelStatus.UNKNOWN


def test_map_parcel_status_falls_back_to_an_older_scan():
    """An unknown newest category is not worth losing the whole status over."""
    assert map_parcel_status(unknown_category_sample()["rows"]) == (
        ParcelStatus.IN_TRANSIT
    )


def test_map_parcel_status_still_asks_for_a_report_when_it_falls_back(caplog):
    """Falling back keeps the parcel useful; the report is how the map grows."""
    rows = [_scan("9"), *unknown_category_sample()["rows"]]
    assert map_parcel_status(rows) == ParcelStatus.IN_TRANSIT
    assert "category=9" in caplog.text


def test_map_row_status_missing_and_unmapped_are_none():
    """History keeps ``null`` rather than ``unknown`` so consumers can tell
    "no mapping" from "mapped to unknown"."""
    assert map_row_status({}) is None
    assert map_row_status(_scan("9")) is None
    assert map_row_status(_scan("5")) == ParcelStatus.DELIVERED


def test_unmapped_status_warns_only_once(caplog):
    assert map_parcel_status([_scan("7")]) == ParcelStatus.UNKNOWN
    assert map_parcel_status([_scan("7")]) == ParcelStatus.UNKNOWN
    assert caplog.text.count("category=7") == 1
    assert "issues/new" in caplog.text


def test_the_detail_code_is_not_a_mapping_input_outside_the_courier_band():
    """Outside band 4 the ordinal maps alone; the detail code is not consulted.

    A pickup-ready reading of this detail code came from a reconstruction and
    was never seen on the wire; the rows carrying it are in-network scans.
    """
    rows = [row("2", "20261013", "10:00:00", "Feldolgozás alatt", "AVATS")]
    parcel = normalize_parcel(payload(ACTIVE_CODE, rows))
    assert parcel["status"] == ParcelStatus.IN_TRANSIT
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None


def test_an_unknown_detail_code_in_the_courier_band_asks_for_a_report(caplog):
    """Band 4 is the only branch where a wrong answer looks plausible.

    An unknown code still reports out for delivery — that is the safer default
    — but silently doing so would hide the very scans we are waiting for, and
    it drives the hot polling tier and the out-for-delivery event.
    """
    rows = [row("4", "20261013", "08:00:00", "Ismeretlen esemény", "XXXXX")]
    parcel = normalize_parcel(payload(ACTIVE_CODE, rows))
    assert parcel["status"] == ParcelStatus.OUT_FOR_DELIVERY
    assert "detail=XXXXX" in caplog.text
    assert "issues/new" in caplog.text


def test_a_known_detail_code_in_the_courier_band_stays_quiet(caplog):
    for detail in ("IKRKI", "KIJKO", "KLHKT"):
        normalize_parcel(
            payload(ACTIVE_CODE, [row("4", "20261013", "08:00:00", "x", detail)])
        )
    assert "detail=" not in caplog.text


def test_the_courier_band_splits_on_the_detail_code():
    """Band 4 covers both a courier carrying it and a counter holding it.

    A real parcel went to ``KLHKT`` — collectable at the post office — and was
    collected there the next day. Reading the ordinal alone would announce it
    as out for delivery and never fill ``awaiting_pickup``.
    """
    held = row("4", "20260921", "09:00:19", "Postán átvehető", "KLHKT")
    held["postaNev"] = "Nyíregyháza 1 posta|Nyíregyháza"
    parcel = normalize_parcel(payload(ACTIVE_CODE, [held]))
    assert parcel["status"] == ParcelStatus.AT_PICKUP_POINT
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == "Nyíregyháza 1 posta"

    carried = row(
        "4", "20260921", "09:00:19", "A küldemény a kézbesítőnél van", "IKRKI"
    )
    carried["postaNev"] = "Székesfehérvári Operatív Osztály|Székesfehérvár"
    on_van = normalize_parcel(payload(ACTIVE_CODE, [carried]))
    assert on_van["status"] == ParcelStatus.OUT_FOR_DELIVERY
    assert on_van["pickup"] is False
    # The office field names the depot that scanned it, never a destination.
    assert on_van["pickup_point"] is None


def test_customs_steps_stay_in_transit():
    parcel = normalize_parcel(customs_sample(), include_history=True)
    assert parcel["status"] == ParcelStatus.IN_TRANSIT
    assert {entry["status"] for entry in parcel["history"]} == {
        ParcelStatus.REGISTERED,
        ParcelStatus.IN_TRANSIT,
    }


# ---------------------------------------------------------------------------
# weight
# ---------------------------------------------------------------------------


def test_weight_comes_back_in_kilograms():
    """The rows state whole grams; the contract is kilograms."""
    assert normalize_parcel(delivered_sample())["weight"] == 1.048


def test_weight_takes_the_newest_row_that_states_one():
    """The carrier leaves the weight off some scans and re-measures in transit.

    Reading row 0 blindly would report nothing for a delivered parcel, which is
    exactly the row that omits it.
    """
    assert delivered_sample()["rows"][0]["suly"] == ""
    assert normalize_parcel(delivered_sample())["weight"] == 1.048

    rows = [
        row("5", "20261013", "14:05:12", "Sikeres kézbesítés", "KLKEZ", weight="3551"),
        row("2", "20261012", "11:02:55", "Feldolgozás alatt", "ROEEC", weight="3530"),
    ]
    assert normalize_parcel(payload(ACTIVE_CODE, rows))["weight"] == 3.551


def test_weight_survives_a_payload_that_states_none():
    rows = [row("3", "20261012", "19:44:03", "Szállítás alatt", weight="")]
    assert normalize_parcel(payload(ACTIVE_CODE, rows))["weight"] is None
    rows = [row("3", "20261012", "19:44:03", "Szállítás alatt", weight="kilo")]
    assert normalize_parcel(payload(ACTIVE_CODE, rows))["weight"] is None


# ---------------------------------------------------------------------------
# timestamp helpers
# ---------------------------------------------------------------------------


def test_the_epoch_is_preferred_over_the_offset_free_split_pair():
    """Both are on every row and agree; the epoch is read because it is exact.

    The split pair sends no offset, so it cannot say which of the two 02:30s it
    means on the night the clocks go back. The epoch can.
    """
    scan = row("3", "20261012", "19:44:03", "Szállítás alatt")
    assert parse_row_datetime(scan) == datetime(
        2026, 10, 12, 19, 44, 3, tzinfo=BUDAPEST
    )
    # Disagree the two on purpose: the epoch is what comes back.
    scan["tranzakcioValodiIdopont"] = "03:00:00"
    assert parse_row_datetime(scan).hour == 19


def test_parse_iso_handles_z_naive_and_garbage():
    assert parse_iso("2026-04-29T13:12:42Z").tzinfo is not None
    # A naive value is assumed UTC so mixed lists still sort.
    assert parse_iso("2026-04-29T13:12:42").tzinfo == UTC
    assert parse_iso("not-a-date") is None
    assert parse_iso(None) is None


def test_parse_row_datetime_applies_the_carrier_zone():
    """The carrier sends a local date and time with no offset at all."""
    parsed = parse_row_datetime(row("4", "20261013", "08:12:40", "x"))
    assert parsed == datetime(2026, 10, 13, 8, 12, 40, tzinfo=BUDAPEST)
    assert parsed.utcoffset() == timedelta(hours=2)
    assert row_timestamp(row("4", "20261013", "08:12:40", "x")) == (
        "2026-10-13T08:12:40+02:00"
    )


def test_parse_row_datetime_anchors_a_timeless_row_at_midnight():
    parsed = parse_row_datetime(
        {"tranzakcioKategoriaKod": "1", "tranzakcioValodiDatum": "20261013"}
    )
    assert parsed == datetime(2026, 10, 13, 0, 0, tzinfo=BUDAPEST)


def test_parse_row_datetime_without_a_usable_moment_is_none():
    assert parse_row_datetime({}) is None
    assert row_timestamp({}) is None
    # A row whose split date is unusable still has its epoch to fall back on;
    # only a row with neither has no place in the timeline.
    assert (
        parse_row_datetime(
            {
                "tranzakcioValodiDatum": "13/10/2026",
                "tranzakcioValodiIdopont": "08:00:00",
            }
        )
        is None
    )
    assert parse_row_datetime({"time": "not-an-epoch"}) is None


def test_row_category_normalises_to_text():
    assert row_category(row(5, "20261013", "08:00:00", "x")) == "5"
    assert row_category(row(" 5 ", "20261013", "08:00:00", "x")) == "5"
    assert row_category({}) is None
    assert row_category({"tranzakcioKategoriaKod": "  "}) is None


# ---------------------------------------------------------------------------
# unique_rows / build_history
# ---------------------------------------------------------------------------


def test_unique_rows_collapses_the_duplicated_handover():
    """A delivered parcel reports its handover twice at one timestamp."""
    rows = delivered_sample()["rows"]
    assert len(rows) == 6
    unique = unique_rows(rows)
    assert len(unique) == 5
    # The first wording the carrier sends for a moment is the one kept.
    assert unique[0]["tranzakcioTipusLeirasSimple"] == "Sikeresen kézbesítve háznál"


def test_unique_rows_keeps_distinct_scans_at_one_moment_apart():
    rows = [
        row("4", "20261013", "08:00:00", "Kézbesítésre átadva"),
        row("3", "20261013", "08:00:00", "Szállítás alatt"),
    ]
    assert len(unique_rows(rows)) == 2


def test_two_scans_sharing_a_moment_and_a_category_both_survive():
    """Collapsing is confined to the delivered band, and this is why.

    A real parcel reported two different processing steps at one second, both
    in band 2. A moment-plus-category rule dropped the second without a trace.
    """
    rows = two_scans_one_moment_sample()["rows"]
    assert len(unique_rows(rows)) == len(rows) == 3
    parcel = normalize_parcel(two_scans_one_moment_sample(), include_history=True)
    wordings = [entry["raw_status"] for entry in parcel["history"]]
    assert "Küldemény előkészítése" in wordings
    assert "Feldolgozás folyamatban" in wordings


def test_a_scan_the_carrier_repeats_verbatim_collapses():
    """A real parcel sent the same transit scan twice, identical in every field.

    Keeping the moment-plus-detail key is what still catches these once the
    delivered-band rule stopped collapsing on category alone.
    """
    scan = row("3", "20260921", "08:16:52", "Továbbítás kézbesítésre", "KLLEK")
    assert len(unique_rows([dict(scan), dict(scan)])) == 1


def test_the_delivered_pair_collapses_despite_differing_detail_codes():
    """The detail code cannot be the discriminator: it is what differs here."""
    rows = delivered_sample()["rows"]
    assert rows[0]["tranzakcioAzon"] != rows[1]["tranzakcioAzon"]
    assert len(unique_rows(rows)) == len(rows) - 1


def test_unique_rows_ignores_junk():
    assert unique_rows(None) == []
    assert unique_rows("not a list") == []
    assert unique_rows(["not-a-dict"]) == []


def test_build_history_orders_oldest_to_newest():
    history = build_history(DELIVERED_ROWS)
    assert len(history) == 5  # the duplicated handover counts once
    assert history[0]["raw_status"] == "Felvétel megtörtént"
    assert history[0]["status"] == ParcelStatus.REGISTERED
    assert history[0]["timestamp"] == "2026-10-11T16:30:00+02:00"
    assert history[-1]["status"] == ParcelStatus.DELIVERED


def test_build_history_caps_to_max_events():
    rows = [
        row("3", f"202610{day:02d}", "10:00:00", "Szállítás alatt")
        for day in range(1, 26)
    ]
    assert len(build_history(rows, max_events=20)) == 20


def test_build_history_handles_missing_and_malformed():
    assert build_history(None) == []
    assert build_history([{"tranzakcioKategoriaKod": "3"}]) == []  # no date
    assert build_history(["not-a-dict"]) == []


def test_build_history_falls_back_to_the_category_without_wording():
    history = build_history([row("3", "20261013", "10:00:00", "")])
    assert history[0]["raw_status"] == "3"


# ---------------------------------------------------------------------------
# normalize_parcel — the canonical contract
# ---------------------------------------------------------------------------

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]


def test_normalize_publishes_exactly_the_canonical_keys():
    """The aggregator and cross-carrier dashboards depend on this key set."""
    assert list(normalize_parcel(delivered_sample())) == CANONICAL_KEYS


def test_capabilities_are_known_values():
    """A typo here would silently misreport this carrier on the docs site."""
    assert CAPABILITIES <= KNOWN_CAPABILITIES
    assert PENDING_CAPABILITIES <= KNOWN_CAPABILITIES


def test_a_capability_is_never_both_populated_and_pending():
    """The docs site would have to pick one; "awaiting data" must not hide a confirmed field."""
    assert not CAPABILITIES & PENDING_CAPABILITIES


def test_capabilities_match_what_normalize_parcel_actually_returns():
    """Every declared CAPABILITIES entry must come true somewhere in a sample."""
    delivered = normalize_parcel(delivered_sample())
    active = normalize_parcel(active_sample())
    with_history = normalize_parcel(delivered_sample(), include_history=True)

    if "weight" in CAPABILITIES:
        assert delivered["weight"] is not None
    if "dimensions" in CAPABILITIES:
        assert delivered["dimensions"] is not None
    if "delivery_window" in CAPABILITIES:
        assert active["planned_from"] is not None or active["planned_to"] is not None
    if "pickup_point" in CAPABILITIES:
        assert normalize_parcel(pickup_sample())["pickup_point"] is not None
    if "url" in CAPABILITIES:
        assert delivered["url"] is not None
    if "history" in CAPABILITIES:
        assert with_history["history"] is not None


def test_pending_capabilities_are_still_none():
    """ "Awaiting data" must not quietly publish a half-mapped field."""
    parcel = normalize_parcel(delivered_sample())
    for field in PENDING_CAPABILITIES:
        assert parcel[field] is None


def test_normalize_delivered_parcel():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["carrier"] == "Magyar Posta"
    assert parcel["barcode"] == DELIVERED_CODE
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["raw_status"] == "Sikeresen kézbesítve háznál"
    assert parcel["delivered"] is True
    assert parcel["delivered_at"] == "2026-10-13T14:05:12+02:00"
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None
    assert parcel["history"] is None  # opt-in, default off


def test_normalize_takes_the_status_from_the_newest_row():
    """Rows arrive newest-first, so the shipment's status is the first one."""
    assert normalize_parcel(active_sample())["status"] == (
        ParcelStatus.OUT_FOR_DELIVERY
    )
    assert normalize_parcel(in_transit_sample())["status"] == ParcelStatus.IN_TRANSIT
    assert normalize_parcel(registered_sample())["status"] == ParcelStatus.REGISTERED


def test_normalize_history_is_opt_in():
    parcel = normalize_parcel(delivered_sample(), include_history=True)
    assert len(parcel["history"]) == 5
    assert parcel["history"][0]["status"] == ParcelStatus.REGISTERED


def test_normalize_delivered_at_is_the_handover_scan():
    """Not whichever row happens to be newest — a later scan can follow it."""
    rows = [
        row("9", "20261014", "07:00:00", "Ismeretlen esemény"),
        *DELIVERED_ROWS,
    ]
    parcel = normalize_parcel(payload(DELIVERED_CODE, rows))
    assert parcel["status"] == ParcelStatus.DELIVERED
    assert parcel["delivered_at"] == "2026-10-13T14:05:12+02:00"


def test_delivered_at_is_none_without_a_handover_scan():
    """``delivered`` can only come from a handover row, but never guess one."""
    assert _delivered_timestamp([]) is None


def test_normalize_never_surfaces_the_recipient_locality():
    """The rows carry a locality and country; neither is a parcel attribute."""
    parcel = normalize_parcel(delivered_sample(), include_history=True)
    assert parcel["sender"] is None
    assert parcel["receiver"] is None
    assert parcel["pickup_point"] is None
    published = {
        key: value for key, value in parcel.items() if key not in ("raw", "history")
    }
    assert "Budapest" not in str(published)
    assert "Budapest" not in str(parcel["history"])


def test_normalize_unknown_category_falls_back_then_warns(caplog):
    parcel = normalize_parcel(unknown_category_sample())
    assert parcel["status"] == ParcelStatus.IN_TRANSIT
    assert parcel["raw_status"] == "Ismeretlen esemény"

    only_unknown = normalize_parcel(
        payload(ACTIVE_CODE, [row("9", "20261013", "10:00:00", "Ismeretlen esemény")])
    )
    assert only_unknown["status"] == ParcelStatus.UNKNOWN
    assert "category=9" in caplog.text
    assert "issues/new" in caplog.text


def test_normalize_pending_placeholder():
    """A tracked-but-not-yet-scanned code still yields a full parcel dict."""
    parcel = normalize_parcel(pending_sample("RR900000003HU"))
    assert parcel["barcode"] == "RR900000003HU"
    assert parcel["status"] == ParcelStatus.UNKNOWN
    assert parcel["delivered"] is False
    assert parcel["raw_status"] is None
    assert parcel["weight"] is None
    assert parcel["dimensions"] is None
    assert parcel["history"] is None


def test_normalize_keeps_raw_payload():
    """``raw`` carries the full record — redaction is a diagnostics boundary."""
    raw = delivered_sample()
    parcel = normalize_parcel(raw)
    assert parcel["raw"] is raw
    assert len(parcel["raw"]["rows"]) == 6
    assert parcel["raw"]["rows"][0]["cimzettHelyiseg"] == "Budapest"


# ---------------------------------------------------------------------------
# sort_parcels_by_ts
# ---------------------------------------------------------------------------


def test_sort_parcels_ascending_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "planned_from": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "planned_from": None},
        {"barcode": "c", "planned_from": "2026-05-01T10:00:00Z"},
    ]
    ordered = [p["barcode"] for p in sort_parcels_by_ts(parcels, "planned_from")]
    assert ordered == ["c", "a", "b"]


def test_sort_parcels_descending_still_puts_unparseable_last():
    parcels = [
        {"barcode": "a", "delivered_at": "2026-05-02T10:00:00Z"},
        {"barcode": "b", "delivered_at": "nonsense"},
        {"barcode": "c", "delivered_at": "2026-05-01T10:00:00Z"},
    ]
    ordered = [
        p["barcode"]
        for p in sort_parcels_by_ts(parcels, "delivered_at", descending=True)
    ]
    assert ordered == ["a", "c", "b"]


# ---------------------------------------------------------------------------
# apply_delivered_filter
# ---------------------------------------------------------------------------


def _entry(filter_type: str, amount: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_DELIVERED_FILTER_TYPE: filter_type,
            CONF_DELIVERED_FILTER_AMOUNT: amount,
        },
        unique_id=DOMAIN,
    )


def _delivered_pair() -> list[dict]:
    now = datetime.now(UTC)
    return [
        {"barcode": "RECENT", "delivered_at": (now - timedelta(days=1)).isoformat()},
        {"barcode": "OLD", "delivered_at": (now - timedelta(days=30)).isoformat()},
    ]


def test_delivered_filter_by_days():
    kept = apply_delivered_filter(_delivered_pair(), _entry("days", 7))
    assert [p["barcode"] for p in kept] == ["RECENT"]


def test_delivered_filter_by_count():
    parcels = _delivered_pair()
    assert apply_delivered_filter(parcels, _entry("parcels", 1)) == parcels[:1]


def test_delivered_filter_keeps_unparseable_timestamp():
    """Better to show a parcel with a broken date than to silently drop it."""
    parcels = [{"barcode": "WEIRD", "delivered_at": "nonsense"}]
    assert apply_delivered_filter(parcels, _entry("days", 7)) == parcels
