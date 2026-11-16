"""odata_query against a made-up API: the limits hold, and failures come back as reasons."""

import httpx
import pytest

from tools.odata import BASE_URL, MAX_ROWS, Refused, odata_fields, odata_query


def api(*responses: httpx.Response):
    """A fake `get` that answers with the given responses in turn and records the calls."""
    calls, queue = [], list(responses)

    def get(url: str, params: dict[str, str]) -> httpx.Response:
        calls.append((url, params))
        return queue.pop(0)

    return get, calls


def rows(*values: dict) -> httpx.Response:
    return httpx.Response(200, json={"value": list(values)})


def test_a_query_sends_odata_options_and_returns_rows():
    get, calls = api(rows({"Id": 7, "Name": "חוק"}))
    result = odata_query("KNS_Bill", filter="KnessetNum eq 26", orderby="Id desc", top=5, get=get)
    assert result == [{"Id": 7, "Name": "חוק"}]
    assert calls == [
        (BASE_URL + "KNS_Bill", {"$top": "5", "$filter": "KnessetNum eq 26", "$orderby": "Id desc"})
    ]


def test_only_listed_tables_and_at_most_max_rows():
    get, calls = api(rows())
    with pytest.raises(Refused, match="Unknown table"):
        odata_query("KNS_Secret", get=get)
    odata_query("KNS_Bill", top=10_000, get=get)
    assert calls[0][1]["$top"] == str(MAX_ROWS)


def test_a_firewall_answer_is_retried_then_succeeds():
    get, calls = api(httpx.Response(474, text="access denied"), rows({"Id": 1}))
    waits = []
    assert odata_query("KNS_Bill", get=get, sleep=waits.append) == [{"Id": 1}]
    assert len(calls) == 2 and waits == [2]


def test_a_blocked_api_gives_up_with_a_reason():
    get, calls = api(*[httpx.Response(474, text="access denied")] * 4)
    with pytest.raises(Refused, match="not answering"):
        odata_query("KNS_Bill", get=get, sleep=lambda _: None)
    assert len(calls) == 4


def test_a_bad_filter_is_not_retried_and_shows_the_api_message():
    get, calls = api(httpx.Response(400, text="Could not find a property named 'Nope'"))
    with pytest.raises(Refused, match="Nope"):
        odata_query("KNS_Bill", filter="Nope eq 1", get=get)
    assert len(calls) == 1


def test_fields_come_from_a_row():
    get, _ = api(rows({"@odata.etag": "x", "Id": 1, "KnessetNum": 26}))
    assert odata_fields("KNS_Bill", get=get) == ["Id", "KnessetNum"]
