"""Sample Magyar Posta API payloads shared by the test modules.

Kept in one module rather than inline in each test: when the payload shape
turns out to differ from what we assumed, there is then exactly one place to
fix. Every value here is made up; the shapes mirror real responses.

The carrier answers with the submitted code as the single key, carrying a flat
array of scan rows, newest row first, repeating the shipment context on each —
but **not completely**: the office and the weight are blank on many rows, the
delivered one among them, so the fixtures leave them blank there too.
:func:`wire` builds that body; the ``*_sample`` helpers build what the client
hands the coordinator.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from custom_components.magyar_posta.const import CODE_KEY, ROWS_KEY

_BUDAPEST = ZoneInfo("Europe/Budapest")

ACTIVE_CODE = "RR900000001HU"
DELIVERED_CODE = "RR900000002HU"


def row(
    category: object,
    date: str,
    time: str,
    description: str,
    detail: str | None = None,
    *,
    weight: str = "1048",
    office: str = "",
) -> dict:
    """One scan row, with the shipment context the carrier repeats on each.

    The epoch ``time`` is derived from the split pair so the two agree exactly,
    as they do on the wire — a fixture where they disagreed would hide which
    of the two the code actually reads.
    """
    scan = {
        "time": int(
            datetime.strptime(f"{date} {time}", "%Y%m%d %H:%M:%S")
            .replace(tzinfo=_BUDAPEST)
            .timestamp()
            * 1000
        ),
        "tranzakcioKategoriaKod": category,
        "tranzakcioTipusLeirasSimple": description,
        "tranzakcioValodiDatum": date,
        "tranzakcioValodiIdopont": time,
        "postaNev": office,
        "suly": weight,
        "cimzettHelyiseg": "Budapest",
        "cimzettOrszag": "HU",
    }
    if detail is not None:
        scan["tranzakcioAzon"] = detail
    return scan


def wire(code: str, rows: list[dict] | None) -> dict:
    """The response body as the endpoint sends it."""
    return {code: rows}


def payload(code: str, rows: list[dict]) -> dict:
    """What the client hands the coordinator for ``code``."""
    return {CODE_KEY: code, ROWS_KEY: rows}


# Newest first, as the carrier sends them. The two handover rows share one
# timestamp — a delivered parcel reports it twice, under two wordings.
# The handover is recorded twice at one moment, under two wordings and two
# detail codes, and the carrier leaves the weight off the first of them.
DELIVERED_ROWS = [
    row("5", "20261013", "14:05:12", "Sikeresen kézbesítve háznál", "KEZB", weight=""),
    row("5", "20261013", "14:05:12", "Sikeres kézbesítés", "KLKEZ"),
    row("4", "20261013", "08:12:40", "Kézbesítésre átadva", "IKRKI"),
    row("3", "20261012", "19:44:03", "Szállítás alatt", "ROJIC"),
    row("2", "20261012", "11:02:55", "Feldolgozás alatt", "ROEEC"),
    row("1", "20261011", "16:30:00", "Felvétel megtörtént", "FETKU"),
]


def delivered_sample(code: str = DELIVERED_CODE) -> dict:
    """A delivered parcel, including its duplicated handover row."""
    return payload(code, [dict(scan) for scan in DELIVERED_ROWS])


def active_sample(code: str = ACTIVE_CODE) -> dict:
    """A parcel with the courier."""
    return payload(code, [dict(scan) for scan in DELIVERED_ROWS[2:]])


def in_transit_sample(code: str = ACTIVE_CODE) -> dict:
    """A parcel still moving through the network."""
    return payload(code, [dict(scan) for scan in DELIVERED_ROWS[3:]])


def registered_sample(code: str = ACTIVE_CODE) -> dict:
    """A parcel that has only been accepted so far."""
    return payload(code, [dict(scan) for scan in DELIVERED_ROWS[5:]])


def customs_sample(code: str = ACTIVE_CODE) -> dict:
    """An inbound parcel walking through the customs steps."""
    return payload(
        code,
        [
            row("3", "20261013", "09:30:00", "Vámkezelés megtörtént"),
            row("3", "20261012", "17:15:00", "Sikeres fizetés"),
            row("3", "20261012", "16:55:00", "Online fizetés indítható"),
            row("3", "20261012", "08:40:00", "Adatkérés a címzettől"),
            row("1", "20261010", "12:00:00", "Felvétel megtörtént"),
        ],
    )


def pickup_sample(code: str = ACTIVE_CODE) -> dict:
    """A parcel waiting at a post office counter.

    The collection scan sits in the courier band and is told apart only by its
    detail code; the delivery mode still reads as home delivery, as it did on
    the real parcel this mirrors.
    """
    return payload(
        code,
        [
            row(
                "4",
                "20261013",
                "09:00:19",
                "Postán átvehető",
                "KLHKT",
                office="Nyíregyháza 1 posta|Nyíregyháza",
            ),
            row("3", "20261012", "19:44:03", "Szállítás alatt", "ROJIC"),
        ],
    )


def two_scans_one_moment_sample(code: str = ACTIVE_CODE) -> dict:
    """Two genuinely different scans sharing a moment and a category.

    Collapsing on moment-plus-category dropped the second of these silently.
    """
    return payload(
        code,
        [
            row("2", "20261012", "21:35:00", "Küldemény előkészítése", "8"),
            row("2", "20261012", "21:35:00", "Feldolgozás folyamatban", "3"),
            row("1", "20261011", "16:30:00", "Felvétel megtörtént", "FETKU"),
        ],
    )


def unknown_category_sample(code: str = ACTIVE_CODE) -> dict:
    """A newest scan whose category is outside the ordinal we know."""
    return payload(
        code,
        [
            row("9", "20261013", "10:00:00", "Ismeretlen esemény"),
            row("3", "20261012", "19:44:03", "Szállítás alatt"),
        ],
    )


def pending_sample(code: str = ACTIVE_CODE) -> dict:
    """A tracked code the carrier has no scans for yet."""
    return payload(code, [])
