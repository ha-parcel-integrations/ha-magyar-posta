# Working in this repository

Home Assistant custom integration for **Magyar Posta** parcel tracking.
Distributed via HACS; not part of HA core. One carrier in the
[ha-parcel-integrations](https://github.com/ha-parcel-integrations) suite,
**generated from ha-carrier-template** — everything outside *Carrier-specific
notes* is suite-wide; when in doubt check the template or a sibling repo.
No DTO layer.

API mechanics — endpoints, parameters, status vocabularies — live in the
private `carrier-research/magyar-posta/api/` and are **never** copied here.

## Shared conventions — fetch when relevant

Suite-wide rules live in
[`.github/CONVENTIONS.md`](https://github.com/ha-parcel-integrations/.github/blob/main/CONVENTIONS.md)
and are **not** repeated here. Don't fetch it every session — fetch it **before**
you act in one of these areas:

| Before you … | Fetch `CONVENTIONS.md` § |
|---|---|
| touch entities, sensors, config/options flow, coordinator, diagnostics, translations | *Home Assistant developer docs* (its table points on to the canonical HA page — don't rely on memory) |
| add/rename a parcel field, a `ParcelStatus`, or a bus event; change the sort/first-refresh; touch unmapped-status logging | *Parcel contract* — exact key set, units, sort, events + suppression; `test_parcels.py::test_normalize_publishes_exactly_the_canonical_keys` guards the key set |
| change which optional field this carrier populates vs. always returns `None` | Update `const.py`'s `CAPABILITIES` in the same commit — it feeds the comparison table on the docs site, so a field that starts (or stops) coming back non-null and isn't reflected there is a wrong claim on the website, not just a stale comment. If this carrier has more than one backend (a country-specific transport, not just a config option) with genuinely different field support, `CAPABILITIES` should be a `CAPABILITIES_BY_VARIANT` dict instead — one frozenset per backend, so a field only some backends populate doesn't get silently intersected away or overclaimed for the rest. A field you could not verify (no real parcel yet) goes in `PENDING_CAPABILITIES` instead of being left out — the site then says "awaiting data" rather than claiming the API never exposes it; move it into `CAPABILITIES` once confirmed |
| ship anything while below 1.0.0 (unconfirmed data) | *Pre-1.0 releases* — one-shot WARNINGs for every guessed shape/code |
| consider "fixing" a lint/pattern the skill flags (poll interval, inline client, sync requests) | *Deliberate skill divergences* — likely intentional, don't re-flag |
| commit, bump, tag, release, or write release notes; add a feature without a test | *Workflow / Commits / Versioning / Testing* |

**Suite-wide tripwires, kept inline on purpose:**
- **First refresh in `__init__.py`, before `async_forward_entry_setups`** — from
  a forwarded platform HA can't catch `ConfigEntryNotReady` and half-sets-up the
  entry. Runtime-only; tests don't catch a regression.
- **Setup stale-entity sweep is scoped to `domain == "sensor"` and skips
  `non_parcel_unique_ids`** — else it deletes the refresh button / the
  summary+diagnostic sensors. Add a new non-parcel sensor's unique_id to the set.
- **Per-parcel sensors are removed by the summary sensor** via
  `entity_registry.async_remove` (self-removal races and leaves ghosts).
- **The optional pickup-point summaries are a pair.** A carrier that can tell
  a parcel is destined for a pickup point exposes
  `en_route_to_pickup_point` for `pickup is true` before it arrives; one that
  can reach `ParcelStatus.AT_PICKUP_POINT` also exposes `awaiting_pickup`.
  See *Parcel contract* in `CONVENTIONS.md`. Say "pickup point", not
  "ServicePoint"/"parcel shop"/"locker", for the generic concept; the
  example carrier demonstrates both canonical sensors.

## Carrier-specific notes

Decisions and traps that survived the build:

- **The response is not an events envelope.** The endpoint answers with the
  submitted code as the single key, carrying a flat array of scan rows, newest
  row first, each repeating the shipment context — though not completely, see
  below. `api.py` wraps that array in a `{CODE_KEY, ROWS_KEY}` dict because the
  coordinator works on one dict per parcel; `raw` therefore carries the
  complete array, untrimmed.
- **The content type is a lie.** Every reply is labelled `text/html` while
  carrying JSON, so the body is what decides — never the header. A body that
  genuinely is not JSON means the route changed, and fails the update rather
  than falling back to reading a page.
- **Not found is semantic, never a status code.** An unknown or not-yet-scanned
  code answers HTTP 200 with the code echoed and `null` as its value. So is an
  empty scan array. Both become a pending placeholder, not an error.
- **Status maps off the scan category ordinal** (1 accepted, 2 processing, 3 in
  transit, 4 with the courier, 5 delivered). The wording beside it is free
  text and is never a mapping input. Bands 2 and 3 are both `in_transit`.
- **Band 4 is the one place the ordinal is not enough.** It covers both "a
  courier is carrying it" (`IKRKI`, `KIJKO`) and "it is waiting at a post
  office counter" (`KLHKT`) — different canonical statuses feeding different
  sensors. Only `tranzakcioAzon` separates them, so it is consulted there and
  nowhere else. A real parcel went to `KLHKT` and was collected the next day;
  reading the ordinal alone announced it as out for delivery.
- **The delivery mode is not a pickup signal.** `kezbesitesimodLeiras` read
  "Háznál kézbesítés" on that very parcel — it is the *intended* mode, not
  where the parcel ended up. Only the collection scan is evidence.
- **`returning` and `problem` are unreachable.** No scan has been seen that
  means either, and guessing would be worse than falling back: an unrecognised
  category takes the newest older scan that does map, and only then `unknown`
  plus the one-shot warning. Customs steps map as `in_transit` through the
  ordinal — including `UGYFELRE_VAR_EGYEDI`, which waits on the recipient and
  held one parcel for 17 days. That it is not `problem` is a decision
  (2026-10-14): its ordinal never diverges, and the suite maps customs as
  transit. It is the mapping a reviewer will want to re-litigate.
- **`en_route_to_pickup_point` is always empty, `awaiting_pickup` is not.**
  Nothing in the payload says a parcel is *destined* for a counter before it
  arrives there, so `pickup` only turns true on the collection scan itself.
  Keep both: the pair is the suite's contract, and removing the first would
  have to be undone the moment the carrier starts announcing it earlier.
- **`pickup_point` is the office that made the collection scan**, read off
  `postaNev` and cut at its `|` (the half after it only repeats the town the
  name already carries). Every other scan's `postaNev` is the depot that
  handled the parcel, so it is read on a collection scan and nowhere else.
- **De-duplication depends on the band.** A delivered parcel reports its
  handover twice at one timestamp under two wordings *and two detail codes*,
  so the delivered band collapses on the moment alone. Everywhere else the key
  keeps the detail code and wording: two genuinely different steps can share a
  second (one real parcel reported two at `21:35:00`, both in band 2, and a
  moment-plus-category rule dropped the second without a trace), while a scan
  the carrier repeats verbatim still collapses. `delivered_at` comes from the
  newest row that maps to delivered, not from whichever row is newest.
- **Every row carries its moment twice** — an epoch-millisecond `time` and a
  `YYYYMMDD` + `HH:MM:SS` local pair with no offset. They agree, and the epoch
  is read: the split pair cannot say which of the two 02:30s it means on the
  night the clocks go back. The split pair is the fallback, rendered in
  `CARRIER_TIMEZONE`; a row with a date but no time is anchored at midnight,
  and one with neither has no place in the timeline.
- **Shipment context is not complete on every row.** `postaNev`, `suly` and
  `kezbesitesimodLeiras` are blank on many scans — the delivered one among
  them — so a shipment-level value takes the newest row that states one, never
  row 0. `suly` is whole grams and the carrier re-measures in transit (3530 →
  3551 on one parcel), so the newest statement wins.
- **`None` on purpose:** no ETA is reported at all (so `planned_from` /
  `planned_to` stay empty and the calendar only fills if that ever changes), no
  sender or receiver, and no `dimensions` — `meretKod` is a size code, not an
  L/W/H triple. Rows do carry the recipient's locality and country — both are
  redacted in diagnostics and deliberately not surfaced as parcel attributes.
  `url` stays in `PENDING_CAPABILITIES`: the tracking page takes its code
  through a form rather than a link, so there is no deep link to publish.

## Options and reloads

For code-based carriers, the options flow starts with exactly `Parcels` and
`Settings` — or, where the carrier supports outgoing parcels, `Incoming
parcels` / `Outgoing parcels` / `Settings` (see the next section).
`Parcels` is one editable multi-code list; `Settings` is
a flat form — some carriers use one sectioned form
(`data_entry_flow.section`) instead; both are generator variants, not carrier
decisions. Changes apply without a restart. Two models, **do not mix them**:
- **Account-less carriers** (the default, and the `--auth byo-key` build) apply
  changes live: an update listener calls `async_request_refresh()`, so
  added/removed parcel sensors appear immediately (this is also the resume
  path after polling has fully suspended — see "Dynamic polling" below).
- **Account-based carriers** call `async_schedule_reload` on submit and register
  **no** update listener. Combining a listener with a reload-on-update flow is
  deprecated, an error in HA 2026.12+.

## Auth models

This repo is the keyless build: the config flow asks for nothing but the
tracking codes themselves, there is no credential to store, and there is no
reauth flow. The template's other two builds (`--auth byo-key`, which adds a
user-supplied API key, and `--auth credentials`, which replaces user-entered
codes with an account feed) do not apply here — see `ha-carrier-template` if
you ever need them.

## Incoming and outgoing parcels

**Add outgoing support whenever the carrier lets a consumer send a parcel** —
a C2C shipment, a locker drop-off, a marketplace or returns label. It is not
an optional extra to bolt on later: without it a parcel the user sent counts
towards `incoming_active` and sits on their dashboard next to the ones they
are waiting for. A carrier that genuinely has no consumer-sending surface is
exempt — say so in *Carrier-specific notes* so the gap reads as a decision.

Where the direction comes from has exactly two answers, and which one applies
follows from the carrier, not from taste:

- **Account-based** — the account feed distinguishes them, so derive it and
  never ask the user. A shipment matching neither side logs a one-shot
  warning and defaults to incoming rather than disappearing from every list.
  References: `ha-ppl-cz` (one call, split on a field), `ha-dhl-nl` (a
  separate "sent" endpoint).
- **Account-less** — the payload cannot reveal it (the user's own parcel and
  a stranger's look alike, and there is no account identity to compare a
  party against), so the **user declares it per parcel**: two menu entries in
  the options flow, each the same multi-code list, plus a `direction` field on
  `track_parcel`. Store it as `CONF_DIRECTION` on the `CONF_PARCELS` dicts,
  defaulting to incoming, so entries written before the option existed need no
  migration. Re-filing a code under the other direction moves it instead of
  erroring — that is the correction path. Reference: `ha-packeta`.
  **Never infer direction from free-text event wording**: a handover sentence
  that happens to name the drop-off point is not a structured field, says
  nothing before handover, and silently ties the split to one locale.

Above that split the shape is identical either way, and is suite-wide:
`coordinator.outgoing` / `coordinator.delivered_outgoing` alongside `data` /
`delivered`; `outgoing_parcels` + `outgoing_delivered_parcels` summary
sensors (their unique_ids belong in `non_parcel_unique_ids`); per-parcel
sensors spawned for **both** directions; the
`<domain>_outgoing_parcel_status_changed` / `_outgoing_parcel_delivered`
event pair, with **no** `registered` and no delivery-time event for outgoing;
`awaiting_pickup`, `next_delivery` and the calendar staying incoming-only;
and both new lists in `diagnostics.py`. The aggregator needs no change — it
buckets on the sensor suffix and the event prefix. Also set
`directions: incoming+outgoing` for the carrier in the docs site's
`data/carriers.yml`.

## Tracking-code validation

In code-based carriers (account-based sources have no code entry),
`valid_tracking_code` in `config_flow.py` accepts every non-empty code — no
format regex. This is a suite-wide convention, not a per-carrier TODO: real
tracking-number formats vary too much across carriers, and are often not
fully confirmed even for this one, to gate on a guessed shape. A too-strict
regex risks rejecting a genuinely valid code; an actually-bad code just comes
back "not found" on the next poll, which is a far cheaper failure mode. Do
not add one back in, even once the format is confirmed.

## Dynamic polling

There is no user-facing polling interval — this is a deliberate suite-wide
choice, not a gap. `coordinator.py`'s `_hottest_tier_minutes` /
`_next_update_interval` recompute `update_interval` at the end of every
refresh. The reference carrier in `ha-carrier-template` (its `coordinator.py`)
is the canonical implementation every carrier mirrors; the design rationale (quiet window, tiers, stagger,
backoff, delivered-skip) is spelled out below.

- **Quiet window:** no polling 00:00–06:00 local time, except two daily
  anchors (~00:00 and ~06:00) for overnight / end-of-day catch-up.
- **Tiers while polling:** *hot* (15 min) when a tracked, not-yet-delivered
  parcel is `out_for_delivery` within an hour of its `planned_from` (or has no
  `planned_from` at all); *mid* (45 min) for anything else still in flight —
  `problem`/`returning` included, deliberately not hot. Account-based carriers
  never fully stop even with nothing hot or in transit: the mid-tier poll is
  also how a new shipment gets discovered.
- **Full stop (account-less carriers only):** `update_interval = None` when
  nothing is tracked or every tracked parcel is delivered. Resumes the moment
  a parcel is added back, via the options-flow refresh above.
- **Stagger:** a small, stable per-install offset (hash of the config entry
  id) is added to every computed interval so installs don't all hit an anchor
  or tier boundary at the same second.
- **429 backoff:** a 429 anywhere in a poll raises `UpdateFailed` with
  `retry_after` — the carrier's own `Retry-After` header if present, otherwise
  an exponential backoff tracked per-coordinator. `api.py`'s
  `…ApiError.status_code` / `.retry_after` carry this from the HTTP layer.
- **Delivered codes are skipped from the fetch (account-less carriers only):**
  once a tracking code's payload comes back `delivered`, `coordinator.py`
  excludes it from the next cycle's fetch — its payload can never change
  again. `self._delivered_codes` (keyed on the tracking code, not the barcode)
  is rebuilt from each cycle's results and intersected with the tracked set on
  untrack. The code stays in the options list, keeps its sensor and its
  cached payload, and still shows under the retention window — it just costs
  no more requests. `coordinator.delivered_codes` surfaces the count in
  diagnostics. Account-based carriers have nothing to skip here — one account
  call already returns everything, so they either have no `delivered_codes` or
  it is always empty.

A carrier that genuinely throttles or soft-bans traffic harder than the 429
backoff handles is a documented, local divergence from this in that one
repo's own `CLAUDE.md` — not a generator flag.

## Module layout

| File | Carrier-specific? |
|---|---|
| `api.py` (HTTP client, error types; `account/` and `tracking/` packages instead when a repo has two sources) | **yes** |
| `const.py` (domain, URLs, `ParcelStatus`, option keys) | partly (URLs) |
| `parcels.py` (status map, `normalize_parcel`, history, sort, filters — pure, no I/O) | partly (the status map, `normalize_parcel`) |
| `coordinator.py` (fetch, cache, event firing) | mostly not |
| `config_flow.py` | partly (code validation; key/credential validation on `--auth byo-key`/`credentials`) |
| `sensor.py` / `button.py` / `calendar.py` / `device_trigger.py` | no |
| `device.py` (shared device-info helper) | no |
| `diagnostics.py` | partly (`TO_REDACT`) |
| `services.py` (`track_parcel` / `untrack_parcel`, account-less only) | no |

`parcels.py` is deliberately free of I/O and HA objects so the per-carrier part
stays unit-testable without Home Assistant. Config: `ConfigEntry.runtime_data`
(typed, no `hass.data`), `PARALLEL_UPDATES = 0`, coordinator takes
`config_entry=entry`. `aiohttp.ClientError` is caught **per parcel** in the gather
loop (one bad parcel doesn't fail the poll) but **not** around the whole update
(the coordinator wraps that). Entities: `has_entity_name` + `translation_key`,
`icons.json`, translated units, `_attr_attribution`, `_unrecorded_attributes` on
anything with a parcel list or `raw`. Over-redact diagnostics — they get pasted
into public issues.

## Running tests

```
python -m pytest tests/ --cov=custom_components.magyar_posta
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README + this file + `docs/` in the same
commit; the API reference lives in your own private research notes, never in
this repo.
