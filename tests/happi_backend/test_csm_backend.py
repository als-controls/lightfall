"""Tests for CSMBackend, the happi ``_Backend`` on top of CSM's instantiable API.

All HTTP calls are mocked via ``pytest-httpx``; no live CSM instance is
required or contacted.
"""

from __future__ import annotations

import httpx
import pytest
from happi.errors import DuplicateError, SearchError

from lightfall.happi_backend.csm import CSMBackend, entry_to_happi_document

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


def test_save_update_patches_existing(httpx_mock, backend: CSMBackend):
    httpx_mock.add_response(
        url=f"{BASE_URL}/api/instantiables/find?name=m1&include_inactive=true",
        json=_entry(id=7),
    )
    httpx_mock.add_response(
        url=f"{BASE_URL}/api/instantiables/7", method="PATCH", status_code=200, json=_entry()
    )
    backend.save("m1", {"prefix": "new-prefix"}, insert=False)


def test_save_update_missing_raises_search_error(httpx_mock, backend: CSMBackend):
    httpx_mock.add_response(
        url=f"{BASE_URL}/api/instantiables/find?name=nope&include_inactive=true",
        status_code=404,
    )
    with pytest.raises(SearchError):
        backend.save("nope", {"prefix": "x"}, insert=False)


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def test_delete_existing(httpx_mock, backend: CSMBackend):
    httpx_mock.add_response(
        url=f"{BASE_URL}/api/instantiables/find?name=m1&include_inactive=true",
        json=_entry(id=7),
    )
    httpx_mock.add_response(url=f"{BASE_URL}/api/instantiables/7", method="DELETE", status_code=204)
    backend.delete("m1")


def test_delete_missing_raises_search_error(httpx_mock, backend: CSMBackend):
    httpx_mock.add_response(
        url=f"{BASE_URL}/api/instantiables/find?name=nope&include_inactive=true",
        status_code=404,
    )
    with pytest.raises(SearchError):
        backend.delete("nope")


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
