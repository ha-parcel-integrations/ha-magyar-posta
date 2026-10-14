"""Tests for the Magyar Posta API client."""
import json
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.magyar_posta.api import (
    MagyarPostaApiClient,
    MagyarPostaApiError,
)

from .payloads import ACTIVE_CODE, DELIVERED_ROWS, wire

CODE = ACTIVE_CODE


def _session_returning(
    status: int, body: object = None, headers: dict | None = None
) -> MagicMock:
    response = AsyncMock()
    response.status = status
    response.headers = headers or {}
    if isinstance(body, str):
        response.json = AsyncMock(side_effect=json.JSONDecodeError("x", body, 0))
    else:
        response.json = AsyncMock(return_value=body)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=response)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.get = MagicMock(return_value=ctx)
    return session


async def test_get_parcel_returns_the_scan_rows():
    session = _session_returning(200, wire(CODE, DELIVERED_ROWS))
    client = MagyarPostaApiClient(session)

    parcel = await client.async_get_parcel(CODE)

    assert parcel["ragszam"] == CODE
    assert parcel["rows"] == DELIVERED_ROWS
    # the code, the language and the registered flag all reach the endpoint
    assert session.get.call_args.kwargs["params"] == {
        "ragszam": CODE,
        "language": "1",
        "registered": "true",
    }


async def test_get_parcel_reads_json_despite_an_html_content_type():
    """Every reply is labelled text/html while carrying a JSON body."""
    session = _session_returning(200, wire(CODE, DELIVERED_ROWS))
    client = MagyarPostaApiClient(session)

    await client.async_get_parcel(CODE)

    response = await session.get.return_value.__aenter__()
    response.json.assert_awaited_once_with(content_type=None)


async def test_get_parcel_returns_none_when_not_found():
    """An unknown code answers HTTP 200 with the code echoed and a null value."""
    client = MagyarPostaApiClient(
        _session_returning(200, wire("RR900000009HU", None))
    )
    assert await client.async_get_parcel("RR900000009HU") is None


async def test_get_parcel_reads_a_differently_echoed_key():
    """The key is the server's echo of the code, not our own spelling of it."""
    client = MagyarPostaApiClient(
        _session_returning(200, {"rr900000001hu": DELIVERED_ROWS})
    )
    parcel = await client.async_get_parcel(CODE)
    assert parcel["rows"] == DELIVERED_ROWS


async def test_get_parcel_returns_none_on_an_empty_scan_list():
    client = MagyarPostaApiClient(_session_returning(200, wire(CODE, [])))
    assert await client.async_get_parcel(CODE) is None


async def test_get_parcel_raises_when_the_rows_are_not_a_list():
    client = MagyarPostaApiClient(_session_returning(200, wire(CODE, {"x": 1})))
    with pytest.raises(MagyarPostaApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_error_status():
    client = MagyarPostaApiClient(_session_returning(500, {}))
    with pytest.raises(MagyarPostaApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.status_code == 500


async def test_get_parcel_raises_on_an_html_body():
    """A non-JSON body means the route changed — never read the page instead."""
    client = MagyarPostaApiClient(_session_returning(200, "<html>captcha</html>"))
    with pytest.raises(MagyarPostaApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_raises_on_non_object_body():
    client = MagyarPostaApiClient(_session_returning(200, ["not", "a", "dict"]))
    with pytest.raises(MagyarPostaApiError):
        await client.async_get_parcel(CODE)


async def test_get_parcel_carries_retry_after_from_a_429():
    client = MagyarPostaApiClient(
        _session_returning(429, {}, headers={"Retry-After": "90"})
    )
    with pytest.raises(MagyarPostaApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.status_code == 429
    assert err.value.retry_after == 90


async def test_get_parcel_ignores_an_http_date_retry_after():
    """A date is not seconds; the coordinator's own backoff takes over."""
    client = MagyarPostaApiClient(
        _session_returning(
            429, {}, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}
        )
    )
    with pytest.raises(MagyarPostaApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.retry_after is None


async def test_get_parcel_without_a_retry_after_header():
    client = MagyarPostaApiClient(_session_returning(429, {}))
    with pytest.raises(MagyarPostaApiError) as err:
        await client.async_get_parcel(CODE)
    assert err.value.retry_after is None


async def test_get_parcel_propagates_network_error():
    """ClientError is left alone — DataUpdateCoordinator already wraps it."""
    session = MagicMock()
    session.get = MagicMock(side_effect=aiohttp.ClientError("boom"))
    client = MagyarPostaApiClient(session)
    with pytest.raises(aiohttp.ClientError):
        await client.async_get_parcel(CODE)
