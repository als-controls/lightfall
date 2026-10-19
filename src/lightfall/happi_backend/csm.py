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

* ``name`` -> happi's ``name`` *and* ``_id`` (happi identifies items by name),
  made a Python identifier by :func:`happi_name`: CSM names are labels ("Det
  Translate") and happi's containers refuse anything else, so every such row
  came back from ``Client.search()`` as an ``InvalidResult``. CSM's own name
  is kept as ``csm_name``, and it is the ``display_name`` unless the row's
  display metadata sets one.
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
CSM's read routes (``GET /api/instantiables``, ``/find``, ``/choices``)
require any authenticated user with a reader role (admin, maintainer, or
reader); writes (create, update, delete) require admin -- see
``app/routers/instantiables.py`` in the CSM repo. There is no anonymous
route. Lightfall authenticates with the personal CSM API key it mints at
login (auth-v2: ``Authorization: Apikey <secret>`` via ``auth=``); the
Keycloak bearer is used only for that mint, so CSM must accept Lightfall's
client id ``LUCID`` in its ``KEYCLOAK_ACCEPTED_CLIENT_IDS``.

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

To use it as a Lightfall device backend, see
:class:`lightfall.devices.backends.csm.CSMDeviceBackend`.

Configuration is deliberately minimal: a base URL and an (optional, but in
practice required by the server) bearer token. Both may also be supplied
via the ``LIGHTFALL_CSM_URL`` / ``LIGHTFALL_CSM_TOKEN`` environment
variables through :meth:`CSMBackend.from_env`, for parity with how happi
itself favors environment-driven configuration (``$HAPPI_CFG``).
"""

from __future__ import annotations

import keyword
import os
import re
from collections.abc import Iterator
from typing import Any

import httpx
from happi.backends.core import ItemMeta, ItemMetaGen, _Backend
from happi.errors import DuplicateError, SearchError
from loguru import logger

__all__ = ["DISPLAY_KEYS", "CSMBackend", "happi_name"]

#: What Lightfall may change on a CSM instantiable: how it is shown, never what it is.
#: Must equal CSM's app.services.instantiable_display.DISPLAY_KEYS.
DISPLAY_KEYS: frozenset[str] = frozenset(
    {"display_name", "icon_override", "group", "synoptic", "lightfall_scope", "scope_metadata"}
)

#: Keys happi or this module add to a document that are not CSM data, ignored when
#: deciding whether an update touches anything beyond display metadata.
_BOOKKEEPING_KEYS: frozenset[str] = frozenset({"_id", "type", "creation", "last_edit"})


def happi_name(csm_name: str, csm_id: Any = None) -> str:
    """The happi name for a CSM instantiable: its CSM name made an identifier.

    happi's containers require ``name`` to be a Python identifier that is not
    a keyword; CSM's names are human labels. Runs of anything but letters,
    digits and ``_`` become one ``_``, the result is lower-cased, and a
    leading digit gets a ``_`` in front, so ``"Det Translate"`` is
    ``"det_translate"``. A name that is already an identifier is kept as it
    is, so a row Lightfall itself created round-trips unchanged.

    Args:
        csm_name: The instantiable's name in CSM.
        csm_id: Its CSM id, for a name with nothing usable in it.

    Returns:
        A valid, non-keyword Python identifier.
    """
    if csm_name.isidentifier() and not keyword.iskeyword(csm_name):
        return csm_name
    name = re.sub(r"[^0-9A-Za-z_]+", "_", csm_name).strip("_").lower()
    if not name:
        return f"instantiable_{csm_id}"
    if name[0].isdigit():
        name = f"_{name}"
    if keyword.iskeyword(name):
        name = f"{name}_"
    return name


def _blank_as_none(value: Any) -> Any:
    """Treat empty values as absent.

    happi writes Lightfall's standard device fields (``beamline``, ``location``,
    ...) as ``""``/``None`` even where CSM never had them; writing nothing over
    nothing is not a change.
    """
    return None if value in (None, "", [], {}) else value


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
    # happi picks the container class from "type"; without it every entry
    # comes back from Client.search() as an InvalidResult.
    doc["type"] = "OphydItem" if python_recipe is not None else "HappiItem"

    doc["documentation"] = entry.get("documentation")
    doc["active"] = entry.get("active", True)

    doc["csm_id"] = entry.get("id")
    doc["csm_origin"] = entry.get("origin")
    doc["csm_is_synced"] = entry.get("is_synced")
    doc["csm_ioc_name"] = entry.get("ioc_name")
    doc["csm_beamline_name"] = entry.get("beamline_name")
    doc["csm_ioc_runtime_status"] = entry.get("ioc_runtime_status")

    # Display-only metadata (CSM instantiable_display) sits at top level like fields.
    doc.update(entry.get("display") or {})

    # CSM's name is a label; happi's must be an identifier (see happi_name).
    # The label stays as the name people see unless a display name is set.
    doc["csm_name"] = entry["name"]
    if not doc.get("display_name"):
        doc["display_name"] = entry["name"]

    # Set identity keys last so nothing above (in particular a stray
    # "name" inside `fields`) can shadow them.
    doc["name"] = doc["_id"] = happi_name(entry["name"], entry.get("id"))
    return doc


class CSMBackend(_Backend):
    """happi ``_Backend`` backed by CSM's instantiable registry HTTP API.

    Parameters
    ----------
    url : str
        Base URL of the CSM API, e.g. ``"https://csm.example.org"``. The
        ``/api/instantiables`` suffix is added automatically.
    token : str, optional
        Static bearer token (tests, scripts). Mutually exclusive with
        ``auth``. Read from ``LIGHTFALL_CSM_TOKEN`` by
        :meth:`from_env` if not given directly.
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
    auth : httpx.Auth, optional
        Per-request authentication, e.g. Lightfall's
        ``ServiceKeyAuth("csm:<url>")``, which reads the minted key on every
        request. Mutually exclusive with ``token``.
    base_params : dict, optional
        CSM search filters (e.g. ``beamline_id``, ``ioc_id``) merged into
        every search; see :meth:`_merge_params`.
    transport : httpx.BaseTransport, optional
        Transport for the constructed ``httpx.Client`` (mainly for tests).
    proxy : str, optional
        Proxy URL for the constructed ``httpx.Client`` (e.g. Lightfall's SOCKS
        proxy for *.lbl.gov hosts reached from off-site).
    """

    def __init__(
        self,
        url: str,
        token: str | None = None,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
        cfg_path: str | None = None,
        *,
        auth: httpx.Auth | None = None,
        base_params: dict[str, Any] | None = None,
        transport: httpx.BaseTransport | None = None,
        proxy: str | None = None,
    ) -> None:
        del cfg_path  # unused; see class docstring
        if token and auth is not None:
            raise ValueError("Pass either token= or auth=, not both")
        self._base_url = url.rstrip("/")
        self._instantiables_url = f"{self._base_url}/api/instantiables"
        self._base_params: dict[str, Any] = dict(base_params or {})
        # happi name -> CSM name, for every document this backend has handed
        # out. Lookups by happi name (get_by_id, find on "name") must ask CSM
        # for the CSM name, and happi_name() cannot be inverted.
        self._csm_names: dict[str, str] = {}
        if client is not None:
            self._client = client
        else:
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            self._client = httpx.Client(
                headers=headers, auth=auth, timeout=timeout, transport=transport, proxy=proxy
            )

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
        if response.status_code == 401:
            raise PermissionError(
                f"CSM rejected Lightfall's key (401) on {context}. Was a CSM key minted at login? "
                f"Look in the log for 'mint failed for service=csm:'. {response.text}"
            )
        if response.status_code == 403:
            raise PermissionError(
                f"CSM: no reader role for this user (403) on {context}. {response.text}"
            )
        if response.status_code in (404, 409):
            raise SearchError(f"CSM could not {context}: {response.status_code} {response.text}")
        response.raise_for_status()

    def _get_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        """Issue a GET against CSM and return the parsed JSON body."""
        response = self._client.get(f"{self._base_url}{path}", params=params)
        self._raise_for_status(response, context=f"GET {path}")
        return response.json()

    def _merge_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """Lay a search's own params over this backend's base filters.

        The caller's keys win (an explicit ``name`` lookup beats a base name
        filter), except ``include_inactive``: the base value, when set, is the
        row's configured scope and overrides the ``"true"`` every happi search
        method sends.
        """
        merged = {**self._base_params, **params}
        if "include_inactive" in self._base_params:
            merged["include_inactive"] = self._base_params["include_inactive"]
        return merged

    def _iter_search(self, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        """Run a search against ``/api/instantiables`` and yield happi documents.

        Args:
            params: Query parameters for the search endpoint.

        Yields:
            One happi document per matching instantiable.
        """
        entries = self._get_json("/api/instantiables", params=self._merge_params(params))
        for entry in entries:
            yield self._document(entry)

    def _document(self, entry: dict[str, Any]) -> dict[str, Any]:
        """Translate one entry and remember which CSM name its happi name stands for.

        Two CSM names can make the same identifier ("M202 Mirror" and
        "m202_mirror"); the second one seen takes its CSM id as a suffix, so
        happi never sees two items under one name.
        """
        doc = entry_to_happi_document(entry)
        known = self._csm_names.get(doc["name"])
        if known is not None and known != entry["name"]:
            doc["name"] = doc["_id"] = f"{doc['name']}_{entry.get('id')}"
            logger.warning(
                "CSM instantiables {!r} and {!r} share the happi name; the second is {!r}",
                known,
                entry["name"],
                doc["name"],
            )
        self._csm_names[doc["name"]] = entry["name"]
        return doc

    def _csm_name(self, happi_id: str) -> str:
        """The CSM name behind a happi name this backend has handed out, else the name itself."""
        return self._csm_names.get(happi_id, happi_id)

    def _find_one(self, csm_name: str) -> httpx.Response:
        """``GET /find`` for one CSM name, inactive rows included."""
        return self._client.get(
            f"{self._instantiables_url}/find",
            params={"name": csm_name, "include_inactive": "true"},
        )

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
            id_: The happi ``_id``: :func:`happi_name` of the CSM name.

        Returns:
            The matching happi document, or ``None`` if there is no match.

        Raises:
            SearchError: If more than one CSM instantiable shares this name
                (CSM's ``/find`` endpoint 409s on ambiguity).
        """
        # A name this backend has not handed out is asked for as it is: a scope
        # row's or a new row's name is already the CSM name.
        response = self._find_one(self._csm_name(id_))
        if response.status_code == 404:
            return None
        self._raise_for_status(response, context=f"get_by_id {id_!r}")
        return self._document(response.json())

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
        yield from self._iter_search(self._to_field_params(self._by_csm_name(to_match)))

    def _by_csm_name(self, to_match: dict[str, Any]) -> dict[str, Any]:
        """``to_match`` with a happi name swapped for the CSM name it stands for."""
        return {
            key: self._csm_name(value)
            if key in ("name", "_id") and isinstance(value, str)
            else value
            for key, value in to_match.items()
        }

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
        params = self._to_field_params(self._by_csm_name(to_match))
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

        On insert, this issues ``POST /api/instantiables`` (CSM forces
        ``origin=manual``; its create gate applies), with display keys sent
        separately as ``display``. On update, only :data:`DISPLAY_KEYS` may
        change: those go to ``PUT /api/instantiables/{id}/display``, and any
        other difference raises before anything is sent.

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
            "name",
            "_id",
            "type",
            "device_class",
            "args",
            "kwargs",
            "documentation",
            "active",
            "csm_id",
            "csm_origin",
            "csm_is_synced",
            "csm_ioc_name",
            "csm_beamline_name",
            "csm_ioc_runtime_status",
            "csm_name",
        }
        fields = {
            key: value
            for key, value in post.items()
            if key not in reserved and key not in DISPLAY_KEYS and key not in _BOOKKEEPING_KEYS
        }
        body = {
            "name": _id,
            "instantiators": instantiators,
            "fields": fields,
            "documentation": post.get("documentation"),
            "active": post.get("active", True),
            "display": {key: post[key] for key in DISPLAY_KEYS if key in post},
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
        changed = {
            key
            for key in set(post) | set(existing_doc)
            if key not in _BOOKKEEPING_KEYS
            and not key.startswith("csm_")
            and _blank_as_none(post.get(key)) != _blank_as_none(existing_doc.get(key))
        }
        not_display = sorted(changed - DISPLAY_KEYS)
        if not_display:
            raise PermissionError(
                f"CSM rows are read-only except display metadata; refused change to {not_display}"
            )
        if not changed:
            return
        patch = {key: _blank_as_none(post.get(key)) for key in sorted(changed)}
        self.put_display(existing_doc, patch)

    def put_display(self, doc: dict[str, Any], patch: dict[str, Any]) -> None:
        """Send ``patch`` (display keys only; ``None`` removes) to ``doc``'s instantiable."""
        not_display = sorted(set(patch) - DISPLAY_KEYS)
        if not_display:
            raise PermissionError(f"Not display metadata: {not_display}")
        response = self._client.put(
            f"{self._instantiables_url}/{doc['csm_id']}/display", json={"display": patch}
        )
        self._raise_for_status(response, context=f"update display of {doc.get('name')}")

    def delete(self, _id: str) -> None:
        """Refuse: Lightfall never deletes CSM instantiables.

        Args:
            _id: happi ``_id`` == CSM instantiable name.

        Raises:
            PermissionError: Always; nothing is sent to CSM.
        """
        raise PermissionError("Lightfall does not delete CSM instantiables")
