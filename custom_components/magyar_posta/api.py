"""Magyar Posta public tracking API client.

The contract the coordinator relies on:

* ``async_get_parcel`` returns the raw per-parcel dict on success,
* returns ``None`` when the carrier says the tracking code is unknown or not
  yet scanned (a normal, expected state — never an error),
* raises :class:`MagyarPostaApiError` for anything else, with
  ``status_code`` set on a non-2xx response and ``retry_after`` set when the
  carrier's own ``Retry-After`` header on a 429 could be parsed as seconds —
  the coordinator's backoff reads both,
* lets ``aiohttp.ClientError`` propagate untouched — ``DataUpdateCoordinator``
  already wraps those into ``UpdateFailed``.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import CODE_KEY, ROWS_KEY, TRACKING_API_URL, TRACKING_LANGUAGE

_LOGGER = logging.getLogger(__name__)

# Tracking codes whose empty scan list has already been reported, so a code
# that stays empty is logged once per HA session instead of on every poll.
_empty_scan_lists_logged: set[str] = set()


class MagyarPostaApiError(Exception):
    """Raised when a Magyar Posta API call returns an unexpected response."""

    def __init__(
        self,
        detail: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        """Store the status code and the ``Retry-After`` header, if any."""
        super().__init__(f"Magyar Posta API request failed: {detail}")
        self.detail = detail
        self.status_code = status_code
        self.retry_after = retry_after


class MagyarPostaApiClient:
    """Client for the public Magyar Posta tracking endpoint.

    No authentication: the endpoint is keyed on the tracking code alone. It
    answers HTTP 200 with the submitted code as the single key, carrying
    either the parcel's scan rows or ``null``::

        {"<code>": [{...newest scan...}, {...}]}
        {"<code>": null}
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        """Initialise the client with an aiohttp session."""
        self._session = session

    async def async_get_parcel(self, tracking_code: str) -> dict[str, Any] | None:
        """Fetch one parcel's scan rows.

        Returns ``{CODE_KEY: code, ROWS_KEY: [...]}`` for a known parcel, or
        ``None`` when the endpoint reports the code as unknown — which is also
        what a not-yet-scanned parcel gets. Any other body shape or non-2xx
        status raises :class:`MagyarPostaApiError`; network errors propagate
        as ``aiohttp.ClientError``.
        """
        params = {
            "ragszam": tracking_code,
            "language": TRACKING_LANGUAGE,
            "registered": "true",
        }
        async with self._session.get(TRACKING_API_URL, params=params) as response:
            if response.status == 429:
                retry_after_header = response.headers.get("Retry-After")
                try:
                    retry_after = (
                        float(retry_after_header) if retry_after_header else None
                    )
                except ValueError:
                    retry_after = None  # an HTTP-date, not seconds; let the caller's own backoff handle it
                raise MagyarPostaApiError(
                    "HTTP 429", status_code=429, retry_after=retry_after
                )
            if response.status != 200:
                raise MagyarPostaApiError(
                    f"HTTP {response.status}", status_code=response.status
                )
            try:
                # content_type=None: every reply is labelled text/html while
                # carrying a JSON body, so the header cannot be trusted and
                # aiohttp would otherwise refuse to parse it.
                payload = await response.json(content_type=None)
            except ValueError as err:
                # A non-JSON body means the route itself changed — fail the
                # update rather than reading the fallback page.
                raise MagyarPostaApiError(f"unparseable body ({err})") from err

        if not isinstance(payload, dict):
            raise MagyarPostaApiError("unexpected body (not a JSON object)")

        rows = payload.get(tracking_code)
        if rows is None and len(payload) == 1:
            # The key is the server's own echo of the code; take the single
            # entry rather than reading a normalised echo as "not found".
            rows = next(iter(payload.values()))

        if rows is None:
            return None
        if not isinstance(rows, list):
            raise MagyarPostaApiError("unexpected body (scan rows are not a list)")
        if not rows:
            if tracking_code not in _empty_scan_lists_logged:
                _empty_scan_lists_logged.add(tracking_code)
                _LOGGER.warning(
                    "Magyar Posta returned an empty scan list for %s", tracking_code
                )
            return None

        return {CODE_KEY: tracking_code, ROWS_KEY: rows}
