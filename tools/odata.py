"""odata_query: read the live Knesset API, for facts newer than the last data load.

The warehouse is loaded once a day, so anything from the last hours is only in the API.
The agent may read a fixed list of tables, a few rows at a time. Requests are GET only.
Column names here are the source's own (Id, Name, KnessetNum, LastUpdatedDate...), not gold's:
odata_fields shows them.
"""

import time
from collections.abc import Callable
from typing import Any

import httpx

BASE_URL = "https://knesset.gov.il/OdataV4/ParliamentInfo/"
MAX_ROWS = 50
TIMEOUT_SECONDS = 30
RETRY_WAITS = (2, 4, 8)  # seconds; the site's firewall answers bursts with codes 470-499
# The tables the agent may read, with what each row is
TABLES = {
    "KNS_Bill": "a bill",
    "KNS_PlenumVote": "a plenum vote, with its totals",
    "KNS_PlenumSession": "a plenum session",
    "KNS_CommitteeSession": "a committee session",
    "KNS_Committee": "a committee",
    "KNS_Query": "a parliamentary query",
    "KNS_Agenda": "a motion for the agenda",
    "KNS_Person": "a person",
    "KNS_PersonToPosition": "a person's post: MK, minister, committee member...",
    "KNS_Faction": "a faction",
    "KNS_IsraelLaw": "a law in force",
}

Get = Callable[[str, dict[str, str]], httpx.Response]


class Refused(ValueError):
    """The request breaks a rule or the API said no; the message tells the agent why."""


def http_get(url: str, params: dict[str, str]) -> httpx.Response:
    return httpx.get(url, params=params, timeout=TIMEOUT_SECONDS, follow_redirects=True)


def odata_query(
    table: str,
    filter: str | None = None,  # noqa: A002 (OData's own word for it)
    orderby: str | None = None,
    select: str | None = None,
    top: int = 20,
    get: Get = http_get,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict[str, Any]]:
    """Up to MAX_ROWS rows of one table, straight from the API."""
    if table not in TABLES:
        raise Refused(f"Unknown table {table!r}. Tables: {', '.join(TABLES)}.")
    params = {"$top": str(max(1, min(top, MAX_ROWS)))}
    params |= {
        f"${name}": value
        for name, value in (("filter", filter), ("orderby", orderby), ("select", select))
        if value
    }
    for wait in (*RETRY_WAITS, None):
        response = get(BASE_URL + table, params)
        if response.status_code == 200:
            return response.json()["value"]
        if response.status_code == 400:  # a wrong column or a malformed filter
            raise Refused(f"The API rejected the request: {response.text[:300]}")
        if wait is None or not (response.status_code == 429 or 470 <= response.status_code < 600):
            break
        sleep(wait)
    raise Refused(f"The Knesset API is not answering (HTTP {response.status_code}). Say so.")


def odata_fields(table: str, get: Get = http_get) -> list[str]:
    """The column names of a table, read from one of its rows."""
    rows = odata_query(table, top=1, get=get)
    return [name for name in rows[0] if not name.startswith("@")] if rows else []
