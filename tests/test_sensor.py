"""Tests for Magyar Posta sensor property logic."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

from custom_components.magyar_posta.const import ParcelStatus
from custom_components.magyar_posta.sensor import (
    MagyarPostaAwaitingPickupSensor,
    MagyarPostaDeliveredParcelsSensor,
    MagyarPostaEnRouteToPickupPointSensor,
    MagyarPostaIncomingParcelsSensor,
    MagyarPostaLastUpdateSensor,
    MagyarPostaNextDeliverySensor,
    MagyarPostaParcelSensor,
)


def _entry(entry_id: str = "e1") -> MagicMock:
    entry = MagicMock()
    entry.entry_id = entry_id
    return entry


def _coordinator(data: list[dict], delivered: list[dict] | None = None) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = data
    coordinator.delivered = delivered if delivered is not None else []
    return coordinator


def _parcel(
    barcode: str,
    status: ParcelStatus = ParcelStatus.IN_TRANSIT,
    pickup: bool = False,
    planned_from: str | None = None,
) -> dict:
    return {
        "carrier": "Magyar Posta",
        "barcode": barcode,
        "sender": "Sender",
        "receiver": None,
        "status": status,
        "pickup": pickup,
        "planned_from": planned_from,
    }


def test_incoming_counts_and_lists():
    coordinator = _coordinator([_parcel("A"), _parcel("B")])
    sensor = MagyarPostaIncomingParcelsSensor(
        coordinator, _entry(), lambda _: None, set()
    )
    assert sensor.native_value == 2
    assert len(sensor.extra_state_attributes["parcels"]) == 2


def test_parcel_sensor_status_and_attributes():
    parcel = _parcel("A", status=ParcelStatus.OUT_FOR_DELIVERY)
    sensor = MagyarPostaParcelSensor(_coordinator([parcel]), _entry(), "A")
    assert sensor.native_value == ParcelStatus.OUT_FOR_DELIVERY
    assert sensor.extra_state_attributes["barcode"] == "A"


def test_parcel_sensor_missing_barcode():
    sensor = MagyarPostaParcelSensor(_coordinator([_parcel("A")]), _entry(), "OTHER")
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_next_delivery_picks_earliest():
    coordinator = _coordinator(
        [
            _parcel("A", planned_from="2026-05-02T10:00:00Z"),
            _parcel("B", planned_from="2026-05-01T10:00:00Z"),
        ]
    )
    sensor = MagyarPostaNextDeliverySensor(coordinator, _entry())
    assert sensor.native_value == datetime(2026, 5, 1, 10, 0, tzinfo=UTC)
    assert sensor.extra_state_attributes["barcode"] == "B"


def test_next_delivery_none_without_moments():
    sensor = MagyarPostaNextDeliverySensor(_coordinator([_parcel("A")]), _entry())
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_next_delivery_skips_unparseable_moment():
    coordinator = _coordinator(
        [
            _parcel("A", planned_from="not-a-date"),
            _parcel("B", planned_from="2026-05-01T10:00:00Z"),
        ]
    )
    sensor = MagyarPostaNextDeliverySensor(coordinator, _entry())
    assert sensor.extra_state_attributes["barcode"] == "B"


def test_en_route_and_awaiting_pickup_split():
    parcels = [
        _parcel("EN_ROUTE", pickup=True),
        _parcel("READY", pickup=True, status=ParcelStatus.AT_PICKUP_POINT),
        _parcel("HOME"),
    ]
    en_route = MagyarPostaEnRouteToPickupPointSensor(_coordinator(parcels), _entry())
    awaiting = MagyarPostaAwaitingPickupSensor(_coordinator(parcels), _entry())
    assert en_route.unique_id == "e1_en_route_to_pickup_point"
    assert awaiting.unique_id == "e1_awaiting_pickup"
    assert [p["barcode"] for p in en_route.extra_state_attributes["parcels"]] == [
        "EN_ROUTE"
    ]
    assert [p["barcode"] for p in awaiting.extra_state_attributes["parcels"]] == [
        "READY"
    ]
    assert en_route.native_value == 1
    assert awaiting.native_value == 1


def test_pickup_sensors_zero_without_data():
    coordinator = _coordinator([])
    coordinator.data = None
    assert (
        MagyarPostaEnRouteToPickupPointSensor(coordinator, _entry()).native_value == 0
    )
    assert MagyarPostaAwaitingPickupSensor(coordinator, _entry()).native_value == 0


def test_delivered_sensor():
    coordinator = _coordinator(
        [], delivered=[_parcel("D", status=ParcelStatus.DELIVERED)]
    )
    sensor = MagyarPostaDeliveredParcelsSensor(coordinator, _entry())
    assert sensor.native_value == 1
    assert sensor.extra_state_attributes["parcels"][0]["barcode"] == "D"


def test_last_update_sensor():
    coordinator = _coordinator([])
    moment = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)
    coordinator.last_success_time = moment
    sensor = MagyarPostaLastUpdateSensor(coordinator, _entry())
    assert sensor.native_value == moment
