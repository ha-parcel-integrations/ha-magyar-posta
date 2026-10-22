"""Constants for the Magyar Posta parcel tracker integration."""

from enum import StrEnum

from homeassistant.const import Platform

DOMAIN = "magyar_posta"


class ParcelStatus(StrEnum):
    """Carrier-agnostic parcel status.

    **Do not extend or rename these members.** Every integration in the parcel
    suite publishes exactly this vocabulary on the ``status`` field of each
    normalised parcel, so cross-carrier automations and the aggregator can
    target ``status: out_for_delivery`` regardless of carrier. Listed in
    roughly the order a parcel moves through.
    """

    REGISTERED = "registered"  # Sender announced the parcel; not handed over yet
    IN_TRANSIT = "in_transit"  # In the carrier's network
    OUT_FOR_DELIVERY = "out_for_delivery"  # On a delivery vehicle today
    AT_PICKUP_POINT = "at_pickup_point"  # Ready to collect at a pickup location
    DELIVERED = "delivered"  # Handed over
    RETURNING = "returning"  # Failed delivery, going back to sender
    PROBLEM = "problem"  # Carrier reports an exception/issue
    UNKNOWN = "unknown"  # Raw status we have not mapped yet


PLATFORMS = [Platform.BUTTON, Platform.CALENDAR, Platform.SENSOR]

# Every optional key the parcel contract defines. CAPABILITIES below must be a
# subset of this — it exists so a typo in CAPABILITIES fails a test instead of
# silently dropping a carrier off a table on the docs site.
KNOWN_CAPABILITIES = frozenset(
    {"weight", "dimensions", "delivery_window", "pickup_point", "url", "history"}
)

# Only the per-scan timeline is canonical here: the response carries no ETA
# window and no L/W/H triple (only a service size code), and recipient
# locality must not be surfaced as a parcel attribute.
CAPABILITIES = frozenset({"history", "weight", "pickup_point"})

# Fields whose support is not confirmed yet — typically a carrier built without
# a real parcel to check against. Leave this empty once the open questions are
# answered. A field is in exactly one of three states: in CAPABILITIES (seen
# populated), in PENDING_CAPABILITIES (docs site shows "awaiting data"), or in
# neither (the API never exposes it). Pending fields must still come back as a
# literal ``None`` from normalize_parcel() until they are confirmed and moved
# into CAPABILITIES. A multi-backend carrier declares
# PENDING_CAPABILITIES_BY_VARIANT with the same keys as its
# CAPABILITIES_BY_VARIANT instead (omit backends with nothing pending).
PENDING_CAPABILITIES: frozenset[str] = frozenset({"url"})

# If this carrier ever grows a second backend with a genuinely different
# payload shape (a country-specific API, not just a config option), replace
# the single CAPABILITIES above with a CAPABILITIES_BY_VARIANT dict instead:
#
#   CAPABILITIES_BY_VARIANT = {
#       "Germany": frozenset({"pickup_point", "url", "history"}),
#       "Other": frozenset({"weight", "dimensions", "delivery_window",
#                            "pickup_point", "url", "history"}),
#   }
#
# Key order is display order on the docs site's comparison table; label each
# key exactly as the carrier's own country/backend selector does. The docs
# site's generator accepts either shape — don't declare both. Do not add this
# preemptively: a single-backend carrier (the common case) keeps the flat
# CAPABILITIES above.

# The consumer tracking route. Keyless: no credential, cookie or CAPTCHA is
# involved, and the only input is the tracking code. Two behaviours the client
# has to absorb: every reply is served as ``text/html`` while carrying a JSON
# body, and an unknown or not-yet-scanned code answers HTTP 200 with the
# submitted code echoed as the single key and ``null`` as its value. Rate
# limiting has never been observed, so the suite's dynamic polling applies
# unchanged.
TRACKING_API_URL = "https://posta.hu/szolgaltatasok/PostaWeb/TrackingInfo"

# Hungarian wording. The route also accepts ``2``, but that it returns English
# is unconfirmed, so the known-good value is the one shipped.
TRACKING_LANGUAGE = "1"

# The wire shape has no envelope of its own, so the client wraps the scan rows
# in one: ``ROWS_KEY`` carries the array untouched, newest row first, and
# ``CODE_KEY`` the code the rows belong to — which is also the key the response
# itself was returned under.
CODE_KEY = "ragszam"
ROWS_KEY = "rows"

# Per-scan fields of one row. The rows repeat the shipment context alongside
# these, but not completely: the office, the weight and the delivery mode are
# blank on many rows, so a shipment-level value is the newest non-empty one
# rather than whatever row 0 happens to carry.
ROW_CATEGORY = "tranzakcioKategoriaKod"
ROW_DETAIL = "tranzakcioAzon"
ROW_DESCRIPTION = "tranzakcioTipusLeirasSimple"
ROW_DATE = "tranzakcioValodiDatum"
ROW_TIME = "tranzakcioValodiIdopont"
ROW_EPOCH = "time"
ROW_OFFICE = "postaNev"
ROW_WEIGHT = "suly"

# The office field names the facility that made the scan, not a destination, so
# it only means a pickup point on a collection scan. Its value is
# ``<office>|<town>``; the town repeats what the office name already says.
OFFICE_SEPARATOR = "|"

# Shipment weight arrives in whole grams.
GRAMS_PER_KILOGRAM = 1000

# Each row also carries an epoch-millisecond ``time`` that agrees with the split
# local date and time. It is preferred because it is unambiguous: the split
# pair sends no offset at all, so it cannot say which of the two 02:30s it
# means on the night the clocks go back.
CARRIER_TIMEZONE = "Europe/Budapest"

# Tracked parcels live in the config entry options as a list of
# ``{tracking_code}`` dicts — this carrier has no account or parcel feed, so the
# user enters the codes themselves. Kept as dicts so future per-parcel fields
# slot in without an options migration.
CONF_PARCELS = "parcels"
CONF_TRACKING_CODE = "tracking_code"

# Delivered-parcels retention: keep delivered parcels visible for the last N
# days, or keep only the N most recent — identical across the suite.
CONF_DELIVERED_FILTER_TYPE = "delivered_filter_type"
CONF_DELIVERED_FILTER_AMOUNT = "delivered_filter_amount"
DEFAULT_DELIVERED_FILTER_TYPE = "days"
DEFAULT_DELIVERED_FILTER_AMOUNT = 7

# Dynamic, status-driven polling — unconditional across the suite, no
# user-facing interval option (see CLAUDE.md's "Dynamic polling"
# section for the full algorithm and the reasoning behind it).
#
# Quiet window: no polling between these local hours except the two anchors
# below, for overnight / end-of-day catch-up.
QUIET_WINDOW_START_HOUR = 0
QUIET_WINDOW_END_HOUR = 6

# Cadence while polling is active (minutes). Hot = at least one tracked,
# not-yet-delivered parcel is out_for_delivery within HOT_LOOKAHEAD_HOURS of
# its planned_from (or has no planned_from at all); mid = anything else still
# in flight (registered, in_transit, at_pickup_point, unknown, problem,
# returning).
HOT_INTERVAL_MINUTES = 15
MID_INTERVAL_MINUTES = 45
HOT_LOOKAHEAD_HOURS = 1

# Small, stable per-install offset added to every computed interval so
# different installs don't all hit an anchor or tier boundary at the same
# second. Deterministic (hash of the config entry id), not random.
STAGGER_MINUTES = 7

# Per-parcel status history is opt-in and off by default, identical across the
# suite. Keep it off by default even when — as here — the timeline arrives in
# the same response and costs no extra request: it is a large attribute, and on
# carriers that need a second call per parcel the cost is real.
CONF_INCLUDE_HISTORY = "include_history"
DEFAULT_INCLUDE_HISTORY = False

# Cap each parcel's history to the most recent N events so the attribute stays
# well under HA's ~16 KB state-attribute limit.
HISTORY_MAX_EVENTS = 20
