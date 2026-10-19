"""Tests for CSMBackend, the happi ``_Backend`` on top of CSM's instantiable API.

All HTTP calls are mocked via ``pytest-httpx``; no live CSM instance is
required or contacted.
"""

from __future__ import annotations

import json

import httpx
import pytest
from happi.errors import DuplicateError, SearchError

from lightfall.happi_backend.csm import (
    DISPLAY_KEYS,
    CSMBackend,
    entry_to_happi_document,
    happi_name,
)

BASE_URL = "https://csm.test"


def _entry(**overrides):
    """Build a minimal, valid CSM instantiable dict, with overrides applied."""
    entry = {
        "id": 7,
        "name": "m1",
        "instantiators": [
            {
                "system": "python",
                "target": "ophyd.EpicsMotor",
                "args": ["{{prefix}}"],
                "kwargs": {"name": "{{name}}"},
            }
        ],
        "fields": {"prefix": "BL601:ACR1:m1", "axis": 3},
        "documentation": "a motor",
        "active": True,
        "origin": "manifest",
        "is_synced": True,
        "ioc_name": "ioc1",
        "beamline_name": "bl601",
        "ioc_runtime_status": "running",
    }
    entry.update(overrides)
    return entry


@pytest.fixture()
def backend() -> CSMBackend:
    return CSMBackend(url=BASE_URL, token="admin-jwt")


# ---------------------------------------------------------------------------
# Document mapping
# ---------------------------------------------------------------------------


def test_entry_to_happi_document_maps_python_recipe_and_fields():
    doc = entry_to_happi_document(_entry())

    assert doc["name"] == "m1"
    assert doc["_id"] == "m1"
    assert doc["device_class"] == "ophyd.EpicsMotor"
    # Templating passed through unrendered -- happi resolves {{...}} itself.
    assert doc["args"] == ["{{prefix}}"]
    assert doc["kwargs"] == {"name": "{{name}}"}
    # fields merged as top-level metadata
    assert doc["prefix"] == "BL601:ACR1:m1"
    assert doc["axis"] == 3
    assert doc["documentation"] == "a motor"
    assert doc["active"] is True
    # CSM bookkeeping preserved under csm_* keys
    assert doc["csm_id"] == 7
    assert doc["csm_origin"] == "manifest"
    assert doc["csm_is_synced"] is True
    assert doc["csm_ioc_name"] == "ioc1"
    assert doc["csm_beamline_name"] == "bl601"
    assert doc["csm_ioc_runtime_status"] == "running"


def test_entry_to_happi_document_no_python_recipe_is_kept_but_incomplete():
    """An entry with no python recipe is not dropped -- it just lacks device_class."""
    entry = _entry(instantiators=[{"system": "epics-boot", "target": "some.other.thing"}])
    doc = entry_to_happi_document(entry)

    assert doc["name"] == "m1"
    assert "device_class" not in doc
    assert "args" not in doc
    assert "kwargs" not in doc
    # everything else is still present
    assert doc["prefix"] == "BL601:ACR1:m1"


def test_entry_to_happi_document_name_cannot_be_shadowed_by_fields():
    entry = _entry(fields={"name": "not-the-real-name", "prefix": "x"})
    doc = entry_to_happi_document(entry)
    assert doc["name"] == "m1"
    assert doc["_id"] == "m1"


# ---------------------------------------------------------------------------
# Names: CSM's are labels, happi's must be identifiers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("csm_name", "expected"),
    [
        ("m1", "m1"),
        ("Det_Translate", "Det_Translate"),
        ("Det Translate", "det_translate"),
        ("M202 Mirror Jack", "m202_mirror_jack"),
        ("EPU (fake) servo-2", "epu_fake_servo_2"),
        ("7.0.1 slit", "_7_0_1_slit"),
        ("class", "class_"),
        ("???", "instantiable_9"),
    ],
)
def test_happi_name_is_an_identifier(csm_name, expected):
    assert happi_name(csm_name, 9) == expected


def test_a_labelled_csm_row_becomes_a_valid_ophyd_item():
    """The 7.0.1.1 regression: "Det Translate" came back as an InvalidResult."""
    from happi.item import OphydItem

    doc = entry_to_happi_document(_entry(name="Det Translate"))

    assert doc["name"] == doc["_id"] == "det_translate"
    assert doc["csm_name"] == "Det Translate"
    assert doc["display_name"] == "Det Translate"
    item = OphydItem(**{k: v for k, v in doc.items() if k not in ("_id", "type")})
    assert item.name == "det_translate"


def test_a_display_name_set_in_csm_wins_over_the_label():
    doc = entry_to_happi_document(
        _entry(name="Det Translate", display={"display_name": "Detector Z"})
    )
    assert doc["display_name"] == "Detector Z"


def test_lookups_by_happi_name_ask_csm_for_its_own_name(httpx_mock, backend: CSMBackend):
    httpx_mock.add_response(
        url=f"{BASE_URL}/api/instantiables?include_inactive=true",
        json=[_entry(name="Det Translate")],
    )
    httpx_mock.add_response(
        url=f"{BASE_URL}/api/instantiables/find?name=Det+Translate&include_inactive=true",
        json=_entry(name="Det Translate"),
    )
    assert [i["name"] for i in backend.all_items] == ["det_translate"]

    doc = backend.get_by_id("det_translate")
    assert doc is not None and doc["csm_id"] == 7


def test_two_csm_names_with_one_identifier_stay_apart(httpx_mock, backend: CSMBackend):
    httpx_mock.add_response(
        url=f"{BASE_URL}/api/instantiables?include_inactive=true",
        json=[_entry(name="M202 Mirror"), _entry(id=8, name="m202_mirror ")],
    )
    names = [i["name"] for i in backend.all_items]
    assert names == ["m202_mirror", "m202_mirror_8"]


# ---------------------------------------------------------------------------
# all_items / find / find_regex / find_range
# ---------------------------------------------------------------------------


def test_all_items(httpx_mock, backend: CSMBackend):
    httpx_mock.add_response(
        url=f"{BASE_URL}/api/instantiables?include_inactive=true",
        json=[_entry(), _entry(id=8, name="m2")],
    )
    items = backend.all_items
    assert [i["name"] for i in items] == ["m1", "m2"]


def test_find_builds_name_and_field_params(httpx_mock, backend: CSMBackend):
    def _callback(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        assert params.get("name") == "m1"
        assert params.get("field.axis") == "3"
        assert params.get("include_inactive") == "true"
        return httpx.Response(200, json=[_entry()])

    httpx_mock.add_callback(_callback)
    results = list(backend.find({"name": "m1", "axis": 3}))
    assert len(results) == 1
    assert results[0]["name"] == "m1"


def test_find_regex_sets_regex_param(httpx_mock, backend: CSMBackend):
    def _callback(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        assert params.get("regex") == "true"
        assert params.get("field.prefix") == "BL601.*"
        return httpx.Response(200, json=[_entry()])

    httpx_mock.add_callback(_callback)
    results = list(backend.find_regex({"prefix": "BL601.*"}))
    assert len(results) == 1


def test_find_range_sets_range_param(httpx_mock, backend: CSMBackend):
    def _callback(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        assert params.get("range") == "axis:1:5"
        return httpx.Response(200, json=[_entry()])

    httpx_mock.add_callback(_callback)
    results = list(backend.find_range("axis", start=1, stop=5, to_match={}))
    assert len(results) == 1


def test_find_range_unbounded_stop(httpx_mock, backend: CSMBackend):
    def _callback(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        assert params.get("range") == "axis:1:"
        return httpx.Response(200, json=[])

    httpx_mock.add_callback(_callback)
    list(backend.find_range("axis", start=1, to_match={}))


# ---------------------------------------------------------------------------
# get_by_id
# ---------------------------------------------------------------------------


def test_get_by_id_hit(httpx_mock, backend: CSMBackend):
    httpx_mock.add_response(
        url=f"{BASE_URL}/api/instantiables/find?name=m1&include_inactive=true",
        json=_entry(),
    )
    doc = backend.get_by_id("m1")
    assert doc is not None
    assert doc["name"] == "m1"


def test_get_by_id_miss_returns_none(httpx_mock, backend: CSMBackend):
    httpx_mock.add_response(
        url=f"{BASE_URL}/api/instantiables/find?name=nope&include_inactive=true",
        status_code=404,
    )
    assert backend.get_by_id("nope") is None


def test_get_by_id_ambiguous_raises_search_error(httpx_mock, backend: CSMBackend):
    httpx_mock.add_response(
        url=f"{BASE_URL}/api/instantiables/find?name=dup&include_inactive=true",
        status_code=409,
    )
    with pytest.raises(SearchError):
        backend.get_by_id("dup")


# ---------------------------------------------------------------------------
# save (insert vs update)
# ---------------------------------------------------------------------------


def test_save_insert_posts_new_entry(httpx_mock, backend: CSMBackend):
    httpx_mock.add_response(
        url=f"{BASE_URL}/api/instantiables/find?name=m1&include_inactive=true",
        status_code=404,
    )

    def _callback(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content)
        assert body["name"] == "m1"
        assert body["instantiators"][0]["target"] == "ophyd.EpicsMotor"
        assert body["fields"]["prefix"] == "BL601:ACR1:m1"
        return httpx.Response(201, json=_entry())

    httpx_mock.add_callback(_callback, url=f"{BASE_URL}/api/instantiables", method="POST")

    backend.save(
        "m1",
        {
            "device_class": "ophyd.EpicsMotor",
            "args": ["{{prefix}}"],
            "kwargs": {"name": "{{name}}"},
            "prefix": "BL601:ACR1:m1",
        },
        insert=True,
    )


def test_save_insert_duplicate_raises(httpx_mock, backend: CSMBackend):
    httpx_mock.add_response(
        url=f"{BASE_URL}/api/instantiables/find?name=m1&include_inactive=true",
        json=_entry(),
    )
    with pytest.raises(DuplicateError):
        backend.save("m1", {"prefix": "x"}, insert=True)


def test_save_update_non_display_never_patches(httpx_mock, backend: CSMBackend):
    """Lightfall may not edit what an instantiable is; no PATCH is ever sent."""
    httpx_mock.add_response(
        url=f"{BASE_URL}/api/instantiables/find?name=m1&include_inactive=true",
        json=_entry(id=7),
    )
    with pytest.raises(PermissionError, match="prefix"):
        backend.save("m1", {**entry_to_happi_document(_entry(id=7)), "prefix": "new"}, insert=False)


def test_save_update_missing_raises_search_error(httpx_mock, backend: CSMBackend):
    httpx_mock.add_response(
        url=f"{BASE_URL}/api/instantiables/find?name=nope&include_inactive=true",
        status_code=404,
    )
    with pytest.raises(SearchError):
        backend.save("nope", {"prefix": "x"}, insert=False)


# ---------------------------------------------------------------------------
# status -> exception mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status_code", [401, 403])
def test_permission_denied_maps_to_permission_error(httpx_mock, backend: CSMBackend, status_code):
    httpx_mock.add_response(
        url=f"{BASE_URL}/api/instantiables?include_inactive=true", status_code=status_code
    )
    with pytest.raises(PermissionError):
        list(backend.all_items)


def test_unexpected_status_is_not_remapped(httpx_mock, backend: CSMBackend):
    httpx_mock.add_response(
        url=f"{BASE_URL}/api/instantiables?include_inactive=true", status_code=500
    )
    with pytest.raises(httpx.HTTPStatusError):
        list(backend.all_items)


# ---------------------------------------------------------------------------
# Session-token auth, base filters, happi container type (2026-10-14)
# ---------------------------------------------------------------------------


def test_document_carries_happi_container_type():
    assert entry_to_happi_document(_entry())["type"] == "OphydItem"
    no_python = _entry(instantiators=[{"system": "labview", "target": "X"}])
    assert entry_to_happi_document(no_python)["type"] == "HappiItem"


def test_happi_client_search_builds_valid_results(httpx_mock):
    import happi

    httpx_mock.add_response(json=[_entry()])
    client = happi.Client(database=CSMBackend(url=BASE_URL, token="t"))
    results = client.search()
    assert len(results) == 1
    assert type(results[0]).__name__ == "SearchResult"
    assert results[0].metadata["prefix"] == "BL601:ACR1:m1"


class _RotatingApikey(httpx.Auth):
    def __init__(self, secrets):
        self._secrets = iter(secrets)

    def auth_flow(self, request):
        request.headers["Authorization"] = f"Apikey {next(self._secrets)}"
        yield request


def test_auth_applies_per_request(httpx_mock):
    seen = []

    def _callback(request):
        seen.append(request.headers.get("Authorization"))
        return httpx.Response(200, json=[])

    httpx_mock.add_callback(_callback, is_reusable=True)
    backend = CSMBackend(url=BASE_URL, auth=_RotatingApikey(["k1", "k2"]))
    list(backend.all_items)
    list(backend.all_items)
    assert seen == ["Apikey k1", "Apikey k2"]


def test_token_and_auth_are_exclusive():
    with pytest.raises(ValueError):
        CSMBackend(url=BASE_URL, token="t", auth=_RotatingApikey(["k"]))


def test_base_params_merge_into_searches(httpx_mock):
    def _callback(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        assert params["beamline_id"] == "12"
        assert params["ioc_id"] == "34"
        # base include_inactive wins over the search's own "true"
        assert params["include_inactive"] == "false"
        return httpx.Response(200, json=[_entry()])

    httpx_mock.add_callback(_callback, is_reusable=True)
    backend = CSMBackend(
        url=BASE_URL,
        token="t",
        base_params={"beamline_id": 12, "ioc_id": 34, "include_inactive": "false"},
    )
    assert len(list(backend.all_items)) == 1
    assert len(list(backend.find({}))) == 1


def test_caller_params_win_over_base_params_except_include_inactive(httpx_mock):
    def _callback(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        assert params["name"] == "m1"  # explicit lookup beats base name filter
        return httpx.Response(200, json=[_entry()])

    httpx_mock.add_callback(_callback)
    backend = CSMBackend(url=BASE_URL, token="t", base_params={"name": "m.*", "regex": "true"})
    list(backend.find({"name": "m1"}))


def test_401_message_points_at_the_mint(httpx_mock, backend: CSMBackend):
    httpx_mock.add_response(status_code=401)
    with pytest.raises(PermissionError, match=r"Was a CSM key minted at login\?"):
        list(backend.all_items)


def test_403_message_names_reader_role(httpx_mock, backend: CSMBackend):
    httpx_mock.add_response(status_code=403)
    with pytest.raises(PermissionError, match=r"no reader role for this user \(403\)"):
        list(backend.all_items)


def test_transport_is_used():
    seen = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=[])

    backend = CSMBackend(url=BASE_URL, token="t", transport=httpx.MockTransport(_handler))
    list(backend.all_items)
    assert seen and seen[0].startswith(f"{BASE_URL}/api/instantiables")


# ---------------------------------------------------------------------------
# Display-only write-back (2026-10-14, Part B)
# ---------------------------------------------------------------------------


def test_display_keys_match_csm():
    assert DISPLAY_KEYS == {
        "display_name",
        "icon_override",
        "group",
        "synoptic",
        "lightfall_scope",
        "scope_metadata",
    }


def test_document_merges_display():
    doc = entry_to_happi_document(_entry(display={"synoptic": {"x": 1}, "group": "slits"}))
    assert doc["synoptic"] == {"x": 1} and doc["group"] == "slits"


def test_save_update_display_only_puts_display(httpx_mock, backend: CSMBackend):
    httpx_mock.add_response(
        url=f"{BASE_URL}/api/instantiables/find?name=m1&include_inactive=true",
        json=_entry(display={"group": "a"}),
        is_reusable=True,
    )
    seen = {}

    def _put(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_entry())

    httpx_mock.add_callback(_put, method="PUT", url=f"{BASE_URL}/api/instantiables/7/display")
    current = entry_to_happi_document(_entry(display={"group": "a"}))
    post = {**current, "synoptic": {"x": 5}, "group": None}
    post.pop("group")
    backend.save("m1", post, insert=False)
    assert seen["body"] == {"display": {"synoptic": {"x": 5}, "group": None}}


def test_save_update_non_display_change_refused(httpx_mock, backend: CSMBackend):
    httpx_mock.add_response(
        url=f"{BASE_URL}/api/instantiables/find?name=m1&include_inactive=true", json=_entry()
    )
    post = {**entry_to_happi_document(_entry()), "prefix": "OTHER:m1"}
    with pytest.raises(PermissionError, match="prefix"):
        backend.save("m1", post, insert=False)


def test_save_update_without_changes_sends_no_put(httpx_mock, backend: CSMBackend):
    httpx_mock.add_response(
        url=f"{BASE_URL}/api/instantiables/find?name=m1&include_inactive=true", json=_entry()
    )
    backend.save("m1", entry_to_happi_document(_entry()), insert=False)  # only the GET happened


def test_save_insert_sends_display_separately(httpx_mock, backend: CSMBackend):
    httpx_mock.add_response(
        url=f"{BASE_URL}/api/instantiables/find?name=scope_x&include_inactive=true", status_code=404
    )
    seen = {}

    def _post(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(201, json=_entry(name="scope_x"))

    httpx_mock.add_callback(_post, method="POST", url=f"{BASE_URL}/api/instantiables")
    backend.save(
        "scope_x",
        {
            "name": "scope_x",
            "device_class": "lightfall.scope",
            "args": [],
            "kwargs": {},
            "active": False,
            "lightfall_scope": "bl",
            "scope_metadata": {"p": 1},
        },
        insert=True,
    )
    assert seen["body"]["display"] == {"lightfall_scope": "bl", "scope_metadata": {"p": 1}}
    assert "lightfall_scope" not in seen["body"]["fields"]


def test_delete_refused_without_request(backend: CSMBackend):
    with pytest.raises(PermissionError, match="does not delete"):
        backend.delete("m1")


def test_proxy_reaches_the_http_client(monkeypatch):
    captured = {}
    real_client = httpx.Client

    def _client(**kwargs):
        captured.update(kwargs)
        return real_client(**{k: v for k, v in kwargs.items() if k != "proxy"})

    monkeypatch.setattr(httpx, "Client", _client)
    CSMBackend(url=BASE_URL, token="t", proxy="socks5://localhost:1080")
    assert captured["proxy"] == "socks5://localhost:1080"
