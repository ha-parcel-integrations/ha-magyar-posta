"""The parcel contract's checks, run over every normalised sample."""

import pytest

from custom_components.magyar_posta.const import ParcelStatus
from custom_components.magyar_posta.parcels import (
    _CARRIER_TZ,
    normalize_parcel,
    parse_iso,
    planned_for_status,
    planned_window,
)

from .payloads import (
    active_sample,
    customs_sample,
    delivered_sample,
    payload,
    pickup_sample,
    registered_sample,
    row,
)

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "contents",
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


def _collected() -> dict:
    sample = pickup_sample()
    sample["rows"] = [
        row("5", "20261014", "10:00:00", "Sikeres kézbesítés", "KLKEZ"),
        *sample["rows"],
    ]
    return sample


SAMPLES = {
    "delivered": delivered_sample,
    "active": active_sample,
    "registered": registered_sample,
    "customs": customs_sample,
    "at_pickup_point": pickup_sample,
    "collected": _collected,
    "pending": lambda: payload("RR900000009HU", []),
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return normalize_parcel(SAMPLES[request.param](), include_history=True)


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_delivered_follows_status(parcel):
    assert parcel["delivered"] is (parcel["status"] is ParcelStatus.DELIVERED)


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_planned_window_is_ordered_and_anchored(parcel):
    if parcel["planned_to"] is not None:
        assert parcel["planned_from"] is not None
        assert parse_iso(parcel["planned_to"]) > parse_iso(parcel["planned_from"])


def test_planned_is_empty_when_the_parcel_is_done_or_waiting(parcel):
    if parcel["status"] in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    if not parcel["pickup"]:
        assert parcel["pickup_point"] is None


def test_a_collected_pickup_parcel_stays_pickup():
    parcel = normalize_parcel(_collected())
    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["delivered"] is True
    assert parcel["pickup"] is True
    assert parcel["pickup_point"] == "Nyíregyháza 1 posta"
    assert parcel["delivered_at"] == "2026-10-14T10:00:00+02:00"


def test_a_courier_delivery_is_not_pickup():
    parcel = normalize_parcel(delivered_sample())
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None


def test_a_moment_ends_at_the_end_of_that_day():
    start, end = planned_window("2026-04-29T22:30:00Z", None, zone=_CARRIER_TZ)
    assert start == "2026-04-29T22:30:00Z"
    assert end == "2026-04-30T23:59:59+02:00"


def test_a_date_covers_the_whole_day():
    start, end = planned_window("2026-04-29", None, zone=_CARRIER_TZ)
    assert start == "2026-04-29T00:00:00+02:00"
    assert end == "2026-04-29T23:59:59+02:00"


def test_planned_for_status_empties_a_finished_parcel():
    window = ("2026-04-29T00:00:00+02:00", "2026-04-29T23:59:59+02:00")
    assert planned_for_status(ParcelStatus.DELIVERED, *window) == (None, None)
    assert planned_for_status(ParcelStatus.IN_TRANSIT, *window) == window
