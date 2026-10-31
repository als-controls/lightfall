"""safe_extract refuses anything that could land outside its folder or exceed what was declared."""
from __future__ import annotations

import stat
import zipfile

import pytest

from lightfall.content.extract import UnsafeBundle, safe_extract


def _zip(path, members):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for m in members:
            if isinstance(m, zipfile.ZipInfo):
                zf.writestr(m, b"target")
            else:
                zf.writestr(m[0], m[1])
    return path


@pytest.fixture
def dest(tmp_path):
    d = tmp_path / "out" / "item"
    d.mkdir(parents=True)
    return d


def _outside(tmp_path, dest):
    return [p for p in tmp_path.rglob("*")
            if p.is_file() and dest not in p.parents and p.suffix != ".zip"]


def test_extracts_nested_files(tmp_path, dest):
    z = _zip(tmp_path / "b.zip", [("a/__init__.py", b"x = 1"), ("a/t/x.html", b"<p>"),
                                  ("a/empty/", b"")])
    assert safe_extract(z, dest, max_files=10, max_bytes=100) == 2
    assert (dest / "a" / "__init__.py").read_bytes() == b"x = 1"
    assert (dest / "a" / "t" / "x.html").read_bytes() == b"<p>"


def _symlink():
    info = zipfile.ZipInfo("link")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    return info


@pytest.mark.parametrize("members", [
    [("../evil.py", b"x")],
    [("/abs.py", b"x")],
    [("C:/x.py", b"x")],
    [("a\\..\\..\\b.py", b"x")],
    [_symlink()],
    [("A.py", b"1"), ("a.py", b"2")],
], ids=["dotdot", "absolute", "drive", "backslash-dotdot", "symlink", "case-duplicate"])
def test_unsafe_members_are_refused(tmp_path, dest, members):
    z = _zip(tmp_path / "b.zip", members)
    with pytest.raises(UnsafeBundle):
        safe_extract(z, dest, max_files=10, max_bytes=100)
    assert _outside(tmp_path, dest) == []


def test_more_files_than_declared(tmp_path, dest):
    z = _zip(tmp_path / "b.zip", [("a.py", b"1"), ("b.py", b"2")])
    with pytest.raises(UnsafeBundle):
        safe_extract(z, dest, max_files=1, max_bytes=100)


def test_more_bytes_than_declared(tmp_path, dest):
    z = _zip(tmp_path / "b.zip", [("a.py", b"123456")])
    with pytest.raises(UnsafeBundle):
        safe_extract(z, dest, max_files=10, max_bytes=5)


def test_not_a_zip(tmp_path, dest):
    z = tmp_path / "b.zip"
    z.write_bytes(b"not a zip at all")
    with pytest.raises(UnsafeBundle):
        safe_extract(z, dest, max_files=10, max_bytes=100)


def test_corrupt_member_is_unsafe(tmp_path, dest):
    import zipfile as zf_mod

    z = _zip(tmp_path / "b.zip", [("a.py", b"print('hello world')" * 200)])
    raw = bytearray(z.read_bytes())
    with zf_mod.ZipFile(z) as zf:
        info = zf.infolist()[0]
    start = info.header_offset + 30 + len(info.filename) + 10
    for i in range(start, start + 40):
        raw[i] ^= 0xFF
    z.write_bytes(bytes(raw))
    with pytest.raises(UnsafeBundle):
        safe_extract(z, dest, max_files=10, max_bytes=10**6)
