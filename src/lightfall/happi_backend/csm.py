"""CSM-backed happi storage backend.

This module implements happi's own ``_Backend`` interface
(``happi.backends.core._Backend``) on top of CSM's ``/api/instantiables``
HTTP API, so anything that speaks happi -- ``happi.Client``, and by
extension Lightfall's existing ``lightfall.devices.backends.happi.HappiBackend``
-- can read (and write) CSM's device instantiation registry as if it were
any other happi database.

This is NOT a Lightfall ``DeviceBackend`` (see ``lightfall.devices.backends``
for that, separate, abstraction). It is a plugin for *happi itself*. Point
``happi.Client(database=CSMBackend(...))`` -- or Lightfall's
``HappiBackend(client=...)`` -- at an instance of this class and CSM
devices flow into Lightfall through the existing happi device backend with
no changes to that code at all.

Document mapping
-----------------
A CSM instantiable looks like::

    {"id": 7, "name": "m1",
     "instantiators": [{"system": "python", "target": "ophyd.EpicsMotor",
                        "args": ["{{prefix}}"], "kwargs": {"name": "{{name}}"}}],
     "fields": {"prefix": "BL601:ACR1:m1", "axis": 3},
     "documentation": "...", "active": true, "origin": "manifest",
     "is_synced": true, "ioc_name": "...", "beamline_name": "...",
     "ioc_runtime_status": "..."}

and is translated into a happi document as follows:

* ``name`` -> happi's ``name`` *and* ``_id`` (happi identifies items by name).
* The recipe whose ``system == "python"`` (if any) supplies
  ``device_class`` (from ``target``), ``args``, and ``kwargs``. The
  ``{{...}}`` placeholders inside those recipes are happi's own templating
  syntax -- deliberately copied by CSM -- and are passed through verbatim
  for happi to resolve; this backend never renders them.
* Everything under ``fields`` is merged in as top-level document keys. This
  is what puts ``prefix`` (and anything else, e.g. ``axis``) where happi's
  ``OphydItem`` container expects to find it.
* ``documentation`` and ``active`` map straight across.
* ``id``, ``origin``, ``is_synced``, ``ioc_name``, ``beamline_name``, and
  ``ioc_runtime_status`` are preserved under CSM-prefixed keys
  (``csm_id``, ``csm_origin``, ...) as extra metadata. happi tolerates
  arbitrary extra keys on a document, and this context is genuinely useful
  when a user is looking at a device that came from CSM.

An entry with no ``python`` recipe (e.g. a LabVIEW-only or EPICS-only
instantiable) has no ``device_class``. This backend does NOT skip such
entries: silently hiding a device because it happens to lack one recipe
type is a more surprising failure mode than surfacing a document that
happi cannot instantiate. The row is still visible to ``all_items``/``find``
etc. with all of its other metadata; only ``device_class``/``args``/
``kwargs`` are simply absent, exactly as they would be for a hand-edited
happi JSON entry missing those keys. Constructing a device from such an
entry raises the same error happi already raises for any container that
is missing ``device_class``.

Authentication
---------------
CSM's ``/api/instantiables`` routes (search, find, choices, create, update,
delete) are *all* gated behind ``require_admin`` -- see
``app/routers/instantiables.py`` in the CSM repo. There is no anonymous or
read-only route. A client of this backend MUST supply a bearer token for a
CSM user with the admin role; there is no API-key path available on these
routes (CSM's ``X-API-Key`` header is a separate mechanism scoped to PC
registration, not instantiables). Be honest with yourself about this: if
you don't have an admin JWT, this backend cannot be used at all, not even
for read-only access.

Configuration
-------------
happi normally selects a backend either by direct construction
(``happi.Client(database=SomeBackend(...))``) or via a ``happi.cfg`` file
whose ``backend`` key is looked up in ``happi.backends.BACKENDS`` -- a
dict hard-coded in happi itself (``json``, ``mongodb``, ``qs``, ``multi``).
CSM's backend is not one of those built-in keys and happi provides no
entrypoint hook to add to that dict, so the ``happi.cfg`` route is not
available without monkeypatching happi internals, which this module
deliberately does not do. The supported route is direct construction::

    from lightfall.happi_backend.csm import CSMBackend
    import happi

    backend = CSMBackend(url="https://csm.example.org", token="<admin JWT>")
    client = happi.Client(database=backend)

or, to reuse Lightfall's existing happi device backend unchanged::

    from lightfall.devices.backends.happi import HappiBackend
    happi_client = happi.Client(database=CSMBackend(url=..., token=...))
    backend = HappiBackend(client=happi_client)

Configuration is deliberately minimal: a base URL and an (optional, but in
practice required by the server) bearer token. Both may also be supplied
via the ``LIGHTFALL_CSM_URL`` / ``LIGHTFALL_CSM_TOKEN`` environment
variables through :meth:`CSMBackend.from_env`, for parity with how happi
itself favors environment-driven configuration (``$HAPPI_CFG``).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from typing import Any

import httpx
from happi.backends.core import ItemMeta, ItemMetaGen, _Backend
from happi.errors import DuplicateError, SearchError
from loguru import logger

__all__ = ["CSMBackend"]


def _first_python_recipe(instantiators: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """Return the first instantiator recipe whose ``system`` is ``"python"``.

    Args:
        instantiators: The ``instantiators`` list from a CSM instantiable,
            or ``None``.

    Returns:
        The matching recipe dict, or ``None`` if there isn't one.
    """
    for recipe in instantiators or []:
        if recipe.get("system") == "python":
            return recipe
    return None


def entry_to_happi_document(entry: dict[str, Any]) -> dict[str, Any]:
    """Translate one CSM instantiable into a happi document.

    See the module docstring for the full field-by-field mapping, including
    the deliberate decision to keep entries with no ``python`` recipe
    (rather than silently dropping them) and to pass ``{{...}}`` recipe
    templating through unrendered.

    Args:
        entry: A single instantiable as returned by CSM's
            ``/api/instantiables`` endpoints.

    Returns:
        A flat dict suitable for happi's containers (``OphydItem`` and
        friends), with CSM's own bookkeeping preserved under
        ``csm_*`` keys.
    """
    doc: dict[str, Any] = dict(entry.get("fields") or {})

    python_recipe = _first_python_recipe(entry.get("instantiators"))
    if python_recipe is not None:
        doc["device_class"] = python_recipe.get("target")
        doc["args"] = python_recipe.get("args", [])
        doc["kwargs"] = python_recipe.get("kwargs", {})

    doc["documentation"] = entry.get("documentation")
    doc["active"] = entry.get("active", True)

    doc["csm_id"] = entry.get("id")
    doc["csm_origin"] = entry.get("origin")
    doc["csm_is_synced"] = entry.get("is_synced")
    doc["csm_ioc_name"] = entry.get("ioc_name")
    doc["csm_beamline_name"] = entry.get("beamline_name")
    doc["csm_ioc_runtime_status"] = entry.get("ioc_runtime_status")

    # Set identity keys last so nothing above (in particular a stray
    # "name" inside `fields`) can shadow them.
    doc["name"] = entry["name"]
    doc["_id"] = entry["name"]
    return doc


class CSMBackend(_Backend):
    """happi ``_Backend`` backed by CSM's instantiable registry HTTP API.

    Parameters
    ----------
    url : str
        Base URL of the CSM API, e.g. ``"https://csm.example.org"``. The
        ``/api/instantiables`` suffix is added automatically.
    token : str, optional
        Bearer token for a CSM user with the admin role. All of CSM's
        instantiables routes require this -- see the module docstring.
        Read from ``LIGHTFALL_CSM_TOKEN`` by :meth:`from_env` if not given
        directly.
    timeout : float, optional
        Per-request timeout in seconds, passed to ``httpx``. Defaults to 10.
    client : httpx.Client, optional
        Reuse an existing ``httpx.Client`` (mainly for tests). If not
        given, one is constructed from ``url``/``token``/``timeout``.
    cfg_path : str, optional
        Accepted, but unused, purely so this class's constructor is
        signature-compatible with how happi's ``Client.from_config`` calls
        backends (``backend(**cfg_section, cfg_path=cfg)``) should CSM ever
        be registered into ``happi.backends.BACKENDS``. See the module
        docstring for why that route isn't wired up today.
    """

    def __init__(
        self,
        url: str,
        token: str | None = None,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
        cfg_path: str | None = None,
    ) -> None:
        del cfg_path  # unused; see class docstring
        self._base_url = url.rstrip("/")
        self._instantiables_url = f"{self._base_url}/api/instantiables"
        if client is not None:
            self._client = client
        else:
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            self._client = httpx.Client(headers=headers, timeout=timeout)

    @classmethod
    def from_env(cls, **kwargs: Any) -> CSMBackend:
        """Build a :class:`CSMBackend` from ``LIGHTFALL_CSM_URL``/``_TOKEN``.

        Args:
            **kwargs: Forwarded to the constructor, taking precedence over
                the environment (e.g. pass ``timeout=`` explicitly).

        Returns:
            A configured :class:`CSMBackend`.

        Raises:
            RuntimeError: If ``LIGHTFALL_CSM_URL`` is not set and ``url``
                was not passed explicitly.
        """
        url = kwargs.pop("url", None) or os.environ.get("LIGHTFALL_CSM_URL")
        if not url:
            raise RuntimeError(
                "CSMBackend.from_env() requires LIGHTFALL_CSM_URL to be set "
                "(or url= to be passed explicitly)"
            )
        token = kwargs.pop("token", None) or os.environ.get("LIGHTFALL_CSM_TOKEN")
        return cls(url=url, token=token, **kwargs)

    # -- HTTP helpers --------------------------------------------------

    def _raise_for_status(self, response: httpx.Response, *, context: str) -> None:
        """Translate a non-2xx CSM response into the matching happi exception.

        Args:
            response: The completed HTTP response.
            context: Short human-readable description of the operation,
                used only in error messages (e.g. ``"save m1"``).

        Raises:
            PermissionError: On 401/403 -- CSM rejected the credentials or
                role.
            SearchError: On 404/409 -- the target item doesn't exist, or
                (409) the request was ambiguous.
            httpx.HTTPStatusError: For any other non-2xx status; this is
                deliberately not remapped, as happi's interface makes no
                promise about it.
        """
        if response.status_code in (401, 403):
            raise PermissionError(
                f"CSM refused to {context}: {response.status_code} {response.text}"
            )
        if response.status_code in (404, 409):
            raise SearchError(f"CSM could not {context}: {response.status_code} {response.text}")
        response.raise_for_status()

    def _get_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        """Issue a GET against CSM and return the parsed JSON body."""
        response = self._client.get(f"{self._base_url}{path}", params=params)
        self._raise_for_status(response, context=f"GET {path}")
        return response.json()

    def _iter_search(self, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Run a search against ``/api/instantiables`` and yield happi documents.

        Args:
            params: Query parameters for the search endpoint.

        Yields:
            One happi document per matching instantiable.
        """
        entries = self._get_json("/api/instantiables", params=params)
        for entry in entries:
            yield entry_to_happi_document(entry)

    # -- _Backend interface ----------------------------------------------

    @property
    def all_items(self) -> list[ItemMeta]:
        """Return every instantiable in CSM (including inactive ones) as happi docs."""
        return list(self._iter_search({"include_inactive": "true"}))

    def clear_cache(self) -> None:
        """No-op: this backend holds no local cache, every call hits CSM."""

    def get_by_id(self, id_: str) -> ItemMeta | None:
        """Look up one instantiable by name.

        Args:
            id_: The happi ``_id`` == CSM instantiable ``name``.

        Returns:
            The matching happi document, or ``None`` if there is no match.

        Raises:
            SearchError: If more than one CSM instantiable shares this name
                (CSM's ``/find`` endpoint 409s on ambiguity).
        """
        response = self._client.get(
            f"{self._instantiables_url}/find",
            params={"name": id_, "include_inactive": "true"},
        )
        if response.status_code == 404:
            return None
        self._raise_for_status(response, context=f"get_by_id {id_!r}")
        return entry_to_happi_document(response.json())

    @staticmethod
    def _to_field_params(to_match: dict[str, Any]) -> dict[str, Any]:
        """Split a happi ``to_match`` dict into CSM's ``name`` + ``field.<key>`` params.

        ``name`` is CSM's own column and gets its own query parameter;
        every other key is assumed to live in CSM's flat ``fields``
        namespace and is sent as ``field.<key>``. Keys this backend
        synthesizes as metadata (``device_class``, ``args``, ``kwargs``,
        ``active``, ``csm_*``, ...) are not part of CSM's searchable
        ``fields`` and will simply fail to match anything server-side --
        this mirrors the real constraint that only ``fields`` is indexed
        for search on the CSM side.

        Args:
            to_match: happi-style key/value search criteria.

        Returns:
            Query parameters for CSM's ``/api/instantiables`` endpoint.
        """
        params: dict[str, Any] = {"include_inactive": "true"}
        for key, value in to_match.items():
            if key in ("name", "_id"):
                params["name"] = value
            else:
                params[f"field.{key}"] = value
        return params

    def find(self, to_match: dict[str, Any]) -> ItemMetaGen:
        """Find every instantiable matching ``to_match`` exactly.

        Args:
            to_match: happi-style key/value search criteria (as passed by
                ``happi.Client.search``/``find_item``).

        Yields:
            Matching happi documents.
        """
        yield from self._iter_search(self._to_field_params(to_match))

    def find_range(
        self,
        key: str,
        *,
        start: float,
        stop: float | None = None,
        to_match: dict[str, Any],
    ) -> ItemMetaGen:
        """Find instantiables where ``start <= entry[key] < stop``.

        Args:
            key: The field to range-filter on.
            start: Inclusive lower bound.
            stop: Exclusive upper bound; ``None`` means unbounded above.
            to_match: Additional exact-match criteria.

        Yields:
            Matching happi documents.
        """
        params = self._to_field_params(to_match)
        params["range"] = f"{key}:{start}:{stop if stop is not None else ''}"
        yield from self._iter_search(params)

    def find_regex(
        self,
        to_match: dict[str, Any],
        *,
        flags: int = re.IGNORECASE,
    ) -> ItemMetaGen:
        """Find instantiables where each value in ``to_match`` matches as a regex.

        Args:
            to_match: happi-style key/value criteria, values are regex
                patterns.
            flags: Accepted for interface compatibility with
                ``happi._Backend.find_regex``. CSM's regex search is
                evaluated server-side and its case-sensitivity is not
                independently controllable per request, so flags other
                than the default are not honoured -- this is logged once
                per call rather than silently ignored.

        Yields:
            Matching happi documents.
        """
        if flags != re.IGNORECASE:
            logger.warning(
                "CSMBackend.find_regex: flags={!r} requested, but CSM's regex search "
                "has no client-controllable case-sensitivity; server default applies",
                flags,
            )
        params = self._to_field_params(to_match)
        params["regex"] = "true"
        yield from self._iter_search(params)

    def save(self, _id: str, post: dict[str, Any], insert: bool = True) -> None:
        """Create or update a CSM instantiable.

        On insert, this issues ``POST /api/instantiables``; CSM forces
        ``origin=manual`` server-side regardless of what's in ``post``. On
        update, this issues ``PATCH /api/instantiables/{id}``, which
        de-syncs a manifest-origin row exactly as an edit through the CSM
        UI would.

        Args:
            _id: happi ``_id`` == CSM instantiable name.
            post: happi document fields to write. ``device_class``,
                ``args``, and ``kwargs`` (if present) are folded back into
                a single ``python`` instantiator recipe; every other key
                is written to CSM's ``fields`` namespace.
            insert: ``True`` to create a new entry, ``False`` to update an
                existing one.

        Raises:
            DuplicateError: ``insert`` is ``True`` but an instantiable
                named ``_id`` already exists.
            SearchError: ``insert`` is ``False`` but no instantiable named
                ``_id`` exists.
            PermissionError: CSM rejected the request (401/403).
        """
        instantiators = []
        device_class = post.get("device_class")
        if device_class is not None:
            instantiators.append(
                {
                    "system": "python",
                    "target": device_class,
                    "args": post.get("args", []),
                    "kwargs": post.get("kwargs", {}),
                }
            )
        reserved = {
            "name", "_id", "device_class", "args", "kwargs", "documentation", "active",
            "csm_id", "csm_origin", "csm_is_synced", "csm_ioc_name", "csm_beamline_name",
            "csm_ioc_runtime_status",
        }
        fields = {key: value for key, value in post.items() if key not in reserved}
        body = {
            "name": _id,
            "instantiators": instantiators,
            "fields": fields,
            "documentation": post.get("documentation"),
            "active": post.get("active", True),
        }

        if insert:
            existing = self.get_by_id(_id)
            if existing is not None:
                raise DuplicateError(f"Item {_id} already exists")
            response = self._client.post(self._instantiables_url, json=body)
            self._raise_for_status(response, context=f"insert {_id}")
            return

        existing_doc = self.get_by_id(_id)
        if existing_doc is None:
            raise SearchError(f"No item found {_id}")
        csm_id = existing_doc["csm_id"]
        response = self._client.patch(f"{self._instantiables_url}/{csm_id}", json=body)
        self._raise_for_status(response, context=f"update {_id}")

    def delete(self, _id: str) -> None:
        """Delete a CSM instantiable by happi ``_id`` (== CSM name).

        Args:
            _id: happi ``_id`` == CSM instantiable name.

        Raises:
            SearchError: No instantiable named ``_id`` exists.
            PermissionError: CSM rejected the request (401/403).
        """
        existing_doc = self.get_by_id(_id)
        if existing_doc is None:
            raise SearchError(f"ID not found in database: {_id!r}")
        csm_id = existing_doc["csm_id"]
        response = self._client.delete(f"{self._instantiables_url}/{csm_id}")
        self._raise_for_status(response, context=f"delete {_id}")
