"""happi ``_Backend`` implementations that let happi read/write CSM.

This package holds implementations of happi's own storage abstraction
(``happi.backends.core._Backend`` -- the interface `happi.Client` calls into
to load, search, and persist device metadata). It has nothing to do with
:mod:`lightfall.devices.backends`, which holds Lightfall's *own*
``DeviceBackend`` classes (``happi.py``, ``bcs.py``, ``mock.py`` there) --
a separate abstraction Lightfall uses to pull device metadata into its own
device model. The two are easy to conflate because both are called
"backend"; they live in separate packages precisely so a reader never has
to guess which one a given module implements.

The relationship between the two: :mod:`lightfall.devices.backends.happi`
(``HappiBackend``, a Lightfall ``DeviceBackend``) already knows how to read
any happi database by constructing a ``happi.Client``. Point that client at
a :class:`lightfall.happi_backend.csm.CSMBackend` (a happi ``_Backend``) and
CSM's instantiable registry flows into Lightfall through the existing
``HappiBackend`` code path with zero changes to it.
"""

from lightfall.happi_backend.csm import CSMBackend

__all__ = ["CSMBackend"]
