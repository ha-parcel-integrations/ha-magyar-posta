"""Canonical parcel shape, status mapping and list helpers.

Everything in this module is a **pure function** — no I/O, no Home Assistant
objects beyond the config entry's options. That keeps the carrier-specific
mapping apart from the coordinator, and makes the mapping trivially
unit-testable without spinning up HA.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry

from .const import (
    CARRIER_TIMEZONE,
    CODE_KEY,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    GRAMS_PER_KILOGRAM,
    HISTORY_MAX_EVENTS,
    OFFICE_SEPARATOR,
    ROW_CATEGORY,
    ROW_DATE,
    ROW_DESCRIPTION,
    ROW_DETAIL,
    ROW_EPOCH,
    ROW_OFFICE,
    ROW_TIME,
    ROW_WEIGHT,
    ROWS_KEY,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# Where users report a status we do not map yet.
#
# The ``?template=`` parameter matters: without it the link opens a blank form,
# and the report comes back missing the version and the log line we need.
NEW_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-magyar-posta/issues/new"
    "?template=unrecognised_status.yml"
)

_CARRIER_TZ = ZoneInfo(CARRIER_TIMEZONE)

# The scan category is an ordinal the carrier walks a shipment through, and it
# carries the mapping — the wording beside it is free text. ``returning`` and
# ``problem`` are not reachable yet: a scan reporting one of those would arrive
# as a category outside this map, which warns rather than guesses.
_STATUS_MAP: dict[str, ParcelStatus] = {
    "1": ParcelStatus.REGISTERED,
    "2": ParcelStatus.IN_TRANSIT,
    "3": ParcelStatus.IN_TRANSIT,
    "4": ParcelStatus.OUT_FOR_DELIVERY,
    "5": ParcelStatus.DELIVERED,
}

# Band 4 is the one place the ordinal is not enough: it covers both "a courier
# is carrying it" and "it is waiting at a post office", which are different
# canonical statuses and different sensors. Only the detail code separates
# them, so it is consulted here and nowhere else.
DELIVERY_CATEGORY = "4"
PICKUP_DETAIL_CODES = frozenset({"KLHKT"})
COURIER_DETAIL_CODES = frozenset({"IKRKI", "KIJKO"})

# The band whose scans the carrier records twice — see ``unique_rows``.
DELIVERED_CATEGORY = "5"

# Categories and band-4 detail codes we have already warned about, so each is
# logged only once per HA session instead of on every poll. Both share one set,
# keyed distinctly, so there is a single thing to reset.
_unmapped_statuses_logged: set[str] = set()


def _warn_unmapped_status(code: str) -> None:
    """Log an unmapped carrier status once, with a copy-paste issue link."""
    if code in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(code)
    _LOGGER.warning(
        "Unrecognised Magyar Posta status — help us map it. Open an issue "
        "and paste this line: %s\n  category=%s → reported as 'unknown'",
        NEW_ISSUE_URL,
        code,
    )


def _warn_unknown_delivery_detail(detail: str | None) -> None:
    """Log an unrecognised band-4 detail code once, with an issue link.

    Band 4 is the only branch whose answer depends on something finer than the
    ordinal, so it is the only place a wrong answer can look plausible: an
    unknown code defaults to the courier reading, which drives the hot polling
    tier and the out-for-delivery event. A parcel held somewhere we do not know
    about yet would otherwise be announced as on its way with nothing said.
    """
    key = f"{DELIVERY_CATEGORY}/{detail}"
    if key in _unmapped_statuses_logged:
        return
    _unmapped_statuses_logged.add(key)
    _LOGGER.warning(
        "Unrecognised Magyar Posta delivery detail — help us map it. Open an "
        "issue and paste this line: %s\n  category=%s detail=%s → reported as "
        "'out_for_delivery'",
        NEW_ISSUE_URL,
        DELIVERY_CATEGORY,
        detail,
    )


def row_category(row: dict) -> str | None:
    """Return a row's scan category as a string, or ``None`` when absent.

    The carrier has been seen to send the ordinal both as a number and as a
    string, so it is compared as text.
    """
    value = row.get(ROW_CATEGORY)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def row_detail(row: dict) -> str | None:
    """Return a row's detail code as a string, or ``None`` when absent."""
    value = row.get(ROW_DETAIL)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def map_row_status(row: dict) -> ParcelStatus | None:
    """Map one scan row to a canonical status, or ``None``.

    Unmapped categories report ``None`` (rather than ``unknown``, so a consumer
    can tell "no mapping" from "mapped to unknown") and warn once.
    """
    category = row_category(row)
    if not category:
        return None
    if category == DELIVERY_CATEGORY:
        detail = row_detail(row)
        if detail in PICKUP_DETAIL_CODES:
            return ParcelStatus.AT_PICKUP_POINT
        if detail not in COURIER_DETAIL_CODES:
            _warn_unknown_delivery_detail(detail)
    mapped = _STATUS_MAP.get(category)
    if mapped is not None:
        return mapped
    _warn_unmapped_status(category)
    return None


def map_parcel_status(rows: list[dict] | None) -> ParcelStatus:
    """Map the newest scan that maps at all; else ``unknown``.

    ``rows`` is expected newest-first, as the carrier sends them. Falling back
    to an older scan keeps the parcel useful when the carrier introduces a
    category we do not know; the warning from ``map_row_status`` is how it
    gets mapped.
    """
    for row in rows or []:
        mapped = map_row_status(row)
        if mapped is not None:
            return mapped
    return ParcelStatus.UNKNOWN


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO 8601 string to an aware datetime, or ``None`` on failure.

    Naive values are treated as UTC so a list always sorts without crashing on
    a mixed set.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_row_datetime(row: dict) -> datetime | None:
    """Return a row's scan moment as an aware datetime, or ``None``.

    Each row carries the moment twice: an epoch-millisecond ``time``, and a
    ``YYYYMMDD`` date plus an ``HH:MM:SS`` local time with no offset at all.
    The epoch is preferred because it is unambiguous — the split pair cannot
    say which of the two 02:30s it means on the night the clocks go back. Both
    are rendered in the carrier's zone, which is what the split pair meant.

    A row with neither has no usable moment; one with a date but no time is
    anchored at midnight rather than dropped.
    """
    epoch = row.get(ROW_EPOCH)
    if epoch is not None and not isinstance(epoch, bool):
        try:
            return datetime.fromtimestamp(int(epoch) / 1000, tz=_CARRIER_TZ)
        except (OSError, OverflowError, TypeError, ValueError):
            pass
    date = str(row.get(ROW_DATE) or "").strip()
    if not date:
        return None
    clock = str(row.get(ROW_TIME) or "").strip() or "00:00:00"
    try:
        parsed = datetime.strptime(f"{date} {clock}", "%Y%m%d %H:%M:%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=_CARRIER_TZ)


def row_timestamp(row: dict) -> str | None:
    """Return a row's scan moment as an ISO 8601 string, or ``None``."""
    parsed = parse_row_datetime(row)
    return parsed.isoformat() if parsed else None


def unique_rows(rows: object) -> list[dict]:
    """Drop rows that repeat a scan already seen, keeping the carrier's order.

    A delivered parcel reports its handover twice at one moment, under two
    wordings — the second being the internal system's record of the first —
    which would otherwise double up in the history and in the event count.

    Two scans can also genuinely share a moment **and** a category and still be
    different events, so identity depends on the band:

    - in the delivered band, the moment alone — the pair differs in its detail
      code, which is therefore no use as a discriminator there;
    - everywhere else, the moment plus the detail code and wording, so a repeat
      the carrier sends twice collapses while two distinct steps recorded at
      one second both survive. A moment-plus-category rule dropped the second
      of those without a trace.
    """
    seen: set[tuple] = set()
    unique: list[dict] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        moment = (str(row.get(ROW_DATE) or ""), str(row.get(ROW_TIME) or ""))
        category = row_category(row)
        if category == DELIVERED_CATEGORY:
            key: tuple = moment
        else:
            key = (*moment, category, row_detail(row), row.get(ROW_DESCRIPTION))
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def build_history(
    rows: list[dict] | None, *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict]:
    """Build the canonical ``history`` list from the carrier's scan rows.

    Each entry is ``{timestamp, status, raw_status}`` — identical across all
    suite carriers, and top-level (not under ``raw``) so it survives the
    aggregator's ``strip_raw()``. ``rows`` is expected newest-first, as the
    carrier sends them; the result is oldest → newest and capped to the most
    recent ``max_events``. Rows without a usable scan moment are dropped —
    there is nothing to place them in the timeline by.
    """
    dated: list[tuple[datetime, dict]] = []
    for row in reversed(unique_rows(rows)):
        parsed = parse_row_datetime(row)
        if parsed is None:
            continue
        dated.append(
            (
                parsed,
                {
                    "timestamp": parsed.isoformat(),
                    "status": map_row_status(row),
                    "raw_status": row.get(ROW_DESCRIPTION) or row_category(row),
                },
            )
        )
    dated.sort(key=lambda item: item[0])
    return [entry for _, entry in dated][-max_events:]


def parcel_weight(rows: list[dict]) -> float | None:
    """Return the shipment weight in kilograms, or ``None``.

    The rows carry whole grams, but not all of them: the weight is blank on
    some scans — the delivered one among them — and is re-measured in transit,
    so the newest row that states one wins rather than row 0.
    """
    for row in rows:
        grams = str(row.get(ROW_WEIGHT) or "").strip()
        if not grams:
            continue
        try:
            return int(grams) / GRAMS_PER_KILOGRAM
        except ValueError:
            continue
    return None


def pickup_point(rows: list[dict]) -> str | None:
    """Return the post office holding the parcel, or ``None``.

    Only a collection scan names a pickup point. Every other scan's office
    field is the depot that handled the parcel, and the delivery mode beside it
    is no help either — it stated home delivery on a parcel that ended up
    waiting at a counter. The value is ``<office>|<town>`` and the town only
    repeats what the office name already carries.
    """
    for row in rows:
        if map_row_status(row) is not ParcelStatus.AT_PICKUP_POINT:
            continue
        office = str(row.get(ROW_OFFICE) or "").strip()
        if office:
            return office.split(OFFICE_SEPARATOR)[0].strip() or None
    return None


def tracking_url(tracking_code: str | None) -> str | None:
    """Return the consumer tracking deep-link for a parcel.

    The carrier's own tracking page takes its code through an interactive form
    rather than a link, so there is no deep link to publish.
    """
    return None


def _delivered_timestamp(rows: list[dict]) -> str | None:
    """Return the moment of the newest scan that reports a handover."""
    for row in rows:
        if map_row_status(row) is ParcelStatus.DELIVERED:
            return row_timestamp(row)
    return None


def normalize_parcel(raw: dict, *, include_history: bool = False) -> dict:
    """Return a carrier-agnostic parcel dict with the payload under ``raw``.

    The **keys of the returned dict are the contract**: every carrier in the
    suite returns exactly these, in this order, and the aggregator and
    cross-carrier dashboards depend on it. A key the carrier does not expose
    is a literal ``None``, never omitted.

    The payload is a flat array of scan rows, newest first — so the shipment's
    status is the newest row's and the timeline is all of them. The context the
    rows repeat is not complete on every row, so shipment-level values take the
    newest row that states one. The recipient locality and country the rows
    carry are deliberately not surfaced as attributes.
    """
    tracking_code = raw.get(CODE_KEY) or None
    rows = unique_rows(raw.get(ROWS_KEY))
    newest = rows[0] if rows else {}
    status = map_parcel_status(rows)
    delivered = status is ParcelStatus.DELIVERED
    at_pickup_point = status is ParcelStatus.AT_PICKUP_POINT

    return {
        "carrier": "Magyar Posta",
        "barcode": tracking_code,
        "sender": None,
        "receiver": None,
        "status": status,
        "raw_status": newest.get(ROW_DESCRIPTION) or row_category(newest),
        "delivered": delivered,
        "delivered_at": _delivered_timestamp(rows) if delivered else None,
        "planned_from": None,
        "planned_to": None,
        "pickup": at_pickup_point,
        "pickup_point": pickup_point(rows) if at_pickup_point else None,
        "url": tracking_url(tracking_code),
        "weight": parcel_weight(rows),
        "dimensions": None,
        "history": build_history(rows) if include_history else None,
        "raw": raw,
    }


def sort_parcels_by_ts(
    parcels: list[dict], key_field: str, *, descending: bool = False
) -> list[dict]:
    """Return normalised parcels sorted by the ISO timestamp at ``key_field``.

    The suite's sort contract: incoming/outgoing ascending on ``planned_from``,
    delivered descending on ``delivered_at``. Parcels whose value is missing or
    unparseable always sort to the end, regardless of ``descending``.
    """
    with_ts: list[tuple[datetime, dict]] = []
    without_ts: list[dict] = []
    for parcel in parcels:
        parsed = parse_iso(parcel.get(key_field))
        if parsed is None:
            without_ts.append(parcel)
        else:
            with_ts.append((parsed, parcel))
    with_ts.sort(key=lambda item: item[0], reverse=descending)
    return [parcel for _, parcel in with_ts] + without_ts


def apply_delivered_filter(parcels: list[dict], entry: ConfigEntry) -> list[dict]:
    """Trim the delivered list per the entry's retention option.

    ``parcels`` must already be sorted newest-first. ``days`` keeps deliveries
    from the last N days (an unparseable ``delivered_at`` is kept rather than
    silently dropped); the ``parcels`` type keeps the N most recent. Parcels
    stay *tracked* either way — this only controls what the delivered sensor
    shows.
    """
    options = entry.options
    filter_type = options.get(
        CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
    )
    amount = int(
        options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
    )
    if filter_type == "days":
        cutoff = datetime.now(timezone.utc) - timedelta(days=amount)
        return [
            parcel
            for parcel in parcels
            if (parsed := parse_iso(parcel.get("delivered_at"))) is None
            or parsed >= cutoff
        ]
    return parcels[:amount]
