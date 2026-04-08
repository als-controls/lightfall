# Logbook Image Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add image support to LUCID's logbook — clipboard paste, file picker button, and programmatic API — with bidirectional sync between client and server.

**Architecture:** New `image` fragment kind stored as files on disk (both client and server), referenced by UUID in fragment metadata. Dedicated REST endpoints for image upload/download. Sync protocol extended with image push/pull phases around the existing metadata sync.

**Tech Stack:** Litestar (backend API), SQLAlchemy + aiosqlite (backend DB), sqlite3 (client DB), PySide6 (UI), httpx (HTTP client), Pillow (image validation)

**Spec:** `docs/superpowers/specs/2026-04-07-logbook-images-design.md`

---

## File Map

### Backend (`lucid-logbook/src/lucid_logbook/`)

| File | Action | Responsibility |
|------|--------|---------------|
| `models.py` | Modify | Add `ImageRow` model + `ImageSchema`/`ImageCreate` pydantic schemas |
| `image_store.py` | Create | Server-side image file storage (save/load/delete) |
| `api.py` | Modify | Add `ImageController` with upload/download/delete endpoints; extend fragment delete to clean up images |
| `app.py` | Modify | Register `ImageController`, configure image storage path |

### Backend tests (`lucid-logbook/tests/`)

| File | Action | Responsibility |
|------|--------|---------------|
| `conftest.py` | Create | Shared fixtures (async client, test DB, temp image dir) |
| `test_image_api.py` | Create | Image upload/download/delete endpoint tests |
| `test_image_fragment_lifecycle.py` | Create | Fragment-with-image creation + cascade delete |

### Frontend (`ncs/src/lucid/logbook/`)

| File | Action | Responsibility |
|------|--------|---------------|
| `client.py` | Modify | Add `image_sync` table, `add_image()` method, image sync phases |
| `fragment_widgets.py` | Modify | Add `ImageFragmentWidget` class + `IMAGE` to `FragmentType` enum |
| `entry_widget.py` | Modify | Handle `IMAGE` fragment type in `_rebuild_fragments()` |
| `event_listener.py` | No change | (programmatic insertion goes through `LogbookClient.add_image()` directly) |

### Frontend UI (`ncs/src/lucid/ui/panels/`)

| File | Action | Responsibility |
|------|--------|---------------|
| `logbook_panel.py` | Modify | Add image button to toolbar, clipboard paste handler, wire up image fragment signals |

---

## Task 1: Backend — Image file storage utility

**Files:**
- Create: `lucid-logbook/src/lucid_logbook/image_store.py`
- Create: `lucid-logbook/tests/conftest.py`
- Create: `lucid-logbook/tests/test_image_store.py`

- [ ] **Step 1: Write test for saving an image**

```python
# tests/test_image_store.py
import pytest
from pathlib import Path
from lucid_logbook.image_store import ImageStore


@pytest.fixture
def store(tmp_path: Path) -> ImageStore:
    return ImageStore(storage_dir=tmp_path)


def test_save_image_creates_file(store: ImageStore, tmp_path: Path):
    png_bytes = _make_minimal_png()
    image_id = store.save(png_bytes, "image/png")

    saved = tmp_path / f"{image_id}.png"
    assert saved.exists()
    assert saved.read_bytes() == png_bytes


def _make_minimal_png() -> bytes:
    """1x1 white PNG."""
    import struct, zlib

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    header = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw = zlib.compress(b"\x00\xff\xff\xff")
    idat = _chunk(b"IDAT", raw)
    iend = _chunk(b"IEND", b"")
    return header + ihdr + idat + iend
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/PycharmProjects/ncs/lucid-logbook && python -m pytest tests/test_image_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lucid_logbook.image_store'`

- [ ] **Step 3: Implement ImageStore**

```python
# lucid-logbook/src/lucid_logbook/image_store.py
"""Server-side image file storage."""
from __future__ import annotations

import uuid
from pathlib import Path

ALLOWED_MIME_TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
}

MAX_IMAGE_SIZE = 20 * 1024 * 1024  # 20 MB


class ImageStoreError(Exception):
    pass


class ImageStore:
    """Stores and retrieves image files on disk."""

    def __init__(self, storage_dir: Path) -> None:
        self._dir = storage_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, data: bytes, mime_type: str) -> str:
        """Save image bytes, return image_id."""
        ext = ALLOWED_MIME_TYPES.get(mime_type)
        if ext is None:
            raise ImageStoreError(
                f"Unsupported mime type: {mime_type}. "
                f"Allowed: {', '.join(ALLOWED_MIME_TYPES)}"
            )
        if len(data) > MAX_IMAGE_SIZE:
            raise ImageStoreError(
                f"Image too large: {len(data)} bytes (max {MAX_IMAGE_SIZE})"
            )
        if len(data) < 8:
            raise ImageStoreError("Image data too small to be valid")

        image_id = str(uuid.uuid4())
        path = self._dir / f"{image_id}{ext}"
        path.write_bytes(data)
        return image_id

    def load(self, image_id: str) -> tuple[bytes, str]:
        """Load image bytes and mime type by image_id."""
        for mime_type, ext in ALLOWED_MIME_TYPES.items():
            path = self._dir / f"{image_id}{ext}"
            if path.exists():
                return path.read_bytes(), mime_type
        raise ImageStoreError(f"Image not found: {image_id}")

    def delete(self, image_id: str) -> bool:
        """Delete image file. Returns True if deleted, False if not found."""
        for ext in ALLOWED_MIME_TYPES.values():
            path = self._dir / f"{image_id}{ext}"
            if path.exists():
                path.unlink()
                return True
        return False

    def exists(self, image_id: str) -> bool:
        """Check if an image exists."""
        return any(
            (self._dir / f"{image_id}{ext}").exists()
            for ext in ALLOWED_MIME_TYPES.values()
        )
```

- [ ] **Step 4: Write remaining tests**

```python
# Append to tests/test_image_store.py

def test_save_jpeg(store: ImageStore, tmp_path: Path):
    # Minimal JPEG: SOI + EOI markers
    jpeg_bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 20 + b"\xff\xd9"
    image_id = store.save(jpeg_bytes, "image/jpeg")
    assert (tmp_path / f"{image_id}.jpg").exists()


def test_save_rejects_unsupported_mime(store: ImageStore):
    with pytest.raises(Exception, match="Unsupported mime type"):
        store.save(b"data" * 10, "image/tiff")


def test_save_rejects_oversized(store: ImageStore):
    big = b"\x00" * (20 * 1024 * 1024 + 1)
    with pytest.raises(Exception, match="too large"):
        store.save(big, "image/png")


def test_load_returns_bytes_and_mime(store: ImageStore):
    png = _make_minimal_png()
    image_id = store.save(png, "image/png")
    data, mime = store.load(image_id)
    assert data == png
    assert mime == "image/png"


def test_load_missing_raises(store: ImageStore):
    with pytest.raises(Exception, match="not found"):
        store.load("nonexistent-id")


def test_delete_removes_file(store: ImageStore, tmp_path: Path):
    png = _make_minimal_png()
    image_id = store.save(png, "image/png")
    assert store.delete(image_id) is True
    assert not (tmp_path / f"{image_id}.png").exists()


def test_delete_missing_returns_false(store: ImageStore):
    assert store.delete("nonexistent-id") is False
```

- [ ] **Step 5: Run all tests**

Run: `cd ~/PycharmProjects/ncs/lucid-logbook && python -m pytest tests/test_image_store.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
cd ~/PycharmProjects/ncs/lucid-logbook
git add src/lucid_logbook/image_store.py tests/conftest.py tests/test_image_store.py
git commit -m "feat(logbook): add ImageStore for server-side image file storage"
```

---

## Task 2: Backend — Image upload/download/delete API endpoints

**Files:**
- Modify: `lucid-logbook/src/lucid_logbook/api.py` (add ImageController after SearchController, ~line 314)
- Modify: `lucid-logbook/src/lucid_logbook/app.py` (register ImageController, ~line 40)
- Create: `lucid-logbook/tests/test_image_api.py`

- [ ] **Step 1: Write test for image upload endpoint**

```python
# tests/test_image_api.py
import pytest
import struct
import zlib
from pathlib import Path
from litestar.testing import AsyncTestClient
from lucid_logbook.app import create_app


def _make_minimal_png() -> bytes:
    """1x1 white PNG."""
    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    header = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw = zlib.compress(b"\x00\xff\xff\xff")
    idat = _chunk(b"IDAT", raw)
    iend = _chunk(b"IEND", b"")
    return header + ihdr + idat + iend


@pytest.fixture
def image_dir(tmp_path: Path) -> Path:
    d = tmp_path / "images"
    d.mkdir()
    return d


@pytest.fixture
async def client(tmp_path: Path, image_dir: Path):
    import os
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    os.environ["IMAGE_STORAGE_DIR"] = str(image_dir)
    app = create_app()
    async with AsyncTestClient(app=app) as tc:
        yield tc


@pytest.mark.asyncio
async def test_upload_image(client, image_dir: Path):
    png = _make_minimal_png()
    resp = await client.post(
        "/logbook/images",
        files={"file": ("test.png", png, "image/png")},
        headers={"X-User-Id": "test-user"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "image_id" in body
    assert body["mime_type"] == "image/png"
    # File should exist on disk
    assert (image_dir / f"{body['image_id']}.png").exists()


@pytest.mark.asyncio
async def test_download_image(client, image_dir: Path):
    png = _make_minimal_png()
    upload = await client.post(
        "/logbook/images",
        files={"file": ("test.png", png, "image/png")},
        headers={"X-User-Id": "test-user"},
    )
    image_id = upload.json()["image_id"]

    resp = await client.get(
        f"/logbook/images/{image_id}",
        headers={"X-User-Id": "test-user"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == png


@pytest.mark.asyncio
async def test_download_missing_image(client):
    resp = await client.get(
        "/logbook/images/nonexistent",
        headers={"X-User-Id": "test-user"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_image(client, image_dir: Path):
    png = _make_minimal_png()
    upload = await client.post(
        "/logbook/images",
        files={"file": ("test.png", png, "image/png")},
        headers={"X-User-Id": "test-user"},
    )
    image_id = upload.json()["image_id"]

    resp = await client.delete(
        f"/logbook/images/{image_id}",
        headers={"X-User-Id": "test-user"},
    )
    assert resp.status_code == 204
    assert not (image_dir / f"{image_id}.png").exists()


@pytest.mark.asyncio
async def test_upload_rejects_unsupported_type(client):
    resp = await client.post(
        "/logbook/images",
        files={"file": ("test.bmp", b"fake-bmp-data-padding", "image/bmp")},
        headers={"X-User-Id": "test-user"},
    )
    assert resp.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/PycharmProjects/ncs/lucid-logbook && python -m pytest tests/test_image_api.py -v`
Expected: FAIL — no ImageController registered, 404 on `/logbook/images`

- [ ] **Step 3: Add ImageController to api.py**

Add after the `SearchController` class (after line ~314 in `api.py`):

```python
# --- Add these imports at the top of api.py ---
from litestar.response import Response
from litestar.datastructures import UploadFile
from litestar.enums import RequestEncodingType
from litestar.params import Body
from lucid_logbook.image_store import ImageStore, ImageStoreError

# --- Add ImageController after SearchController ---

class ImageController(Controller):
    """Image upload/download/delete endpoints."""

    path = "/logbook/images"

    @post("/", status_code=201)
    async def upload_image(
        self,
        request: Request,
        data: UploadFile = Body(media_type=RequestEncodingType.MULTI_PART),
    ) -> dict:
        image_store: ImageStore = request.app.state.image_store
        content = await data.read()
        mime_type = data.content_type or "application/octet-stream"

        try:
            image_id = image_store.save(content, mime_type)
        except ImageStoreError as e:
            raise ValidationException(str(e))

        return {
            "image_id": image_id,
            "mime_type": mime_type,
            "size_bytes": len(content),
        }

    @get("/{image_id:str}")
    async def download_image(self, request: Request, image_id: str) -> Response:
        image_store: ImageStore = request.app.state.image_store

        try:
            data, mime_type = image_store.load(image_id)
        except ImageStoreError:
            raise NotFoundException(f"Image not found: {image_id}")

        return Response(
            content=data,
            media_type=mime_type,
            headers={"Cache-Control": "max-age=86400"},
        )

    @delete("/{image_id:str}", status_code=204)
    async def delete_image(self, request: Request, image_id: str) -> None:
        image_store: ImageStore = request.app.state.image_store
        deleted = image_store.delete(image_id)
        if not deleted:
            raise NotFoundException(f"Image not found: {image_id}")
```

- [ ] **Step 4: Register ImageController and configure image store in app.py**

Modify `app.py` `create_app()`:

```python
# Add import at top
from lucid_logbook.image_store import ImageStore

# In create_app(), add ImageController to route_handlers:
route_handlers=[health_check, LogbookController, SearchController, ImageController]

# Add image store setup before return:
image_dir = Path(os.environ.get("IMAGE_STORAGE_DIR", "./logbook_images"))
app.state.image_store = ImageStore(storage_dir=image_dir)
```

Also add `from pathlib import Path` and `import os` to imports if not already present, and import `ImageController` from `.api`.

- [ ] **Step 5: Run tests**

Run: `cd ~/PycharmProjects/ncs/lucid-logbook && python -m pytest tests/test_image_api.py -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
cd ~/PycharmProjects/ncs/lucid-logbook
git add src/lucid_logbook/api.py src/lucid_logbook/app.py tests/test_image_api.py
git commit -m "feat(logbook): add image upload/download/delete API endpoints"
```

---

## Task 3: Backend — Cascade image delete when image fragment is deleted

**Files:**
- Modify: `lucid-logbook/src/lucid_logbook/api.py` (~line 237, `delete_fragment` method)
- Create: `lucid-logbook/tests/test_image_fragment_lifecycle.py`

- [ ] **Step 1: Write test for cascade delete**

```python
# tests/test_image_fragment_lifecycle.py
import pytest
import struct
import zlib
from pathlib import Path
from litestar.testing import AsyncTestClient
from lucid_logbook.app import create_app


def _make_minimal_png() -> bytes:
    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    header = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw = zlib.compress(b"\x00\xff\xff\xff")
    idat = _chunk(b"IDAT", raw)
    iend = _chunk(b"IEND", b"")
    return header + ihdr + idat + iend


@pytest.fixture
async def client(tmp_path: Path):
    import os
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path}/test.db"
    os.environ["IMAGE_STORAGE_DIR"] = str(image_dir)
    app = create_app()
    async with AsyncTestClient(app=app) as tc:
        yield tc


@pytest.fixture
def image_dir(tmp_path: Path) -> Path:
    return tmp_path / "images"


@pytest.mark.asyncio
async def test_deleting_image_fragment_removes_image_file(client, image_dir: Path):
    headers = {"X-User-Id": "test-user"}

    # Upload image
    png = _make_minimal_png()
    upload = await client.post(
        "/logbook/images",
        files={"file": ("test.png", png, "image/png")},
        headers=headers,
    )
    image_id = upload.json()["image_id"]
    assert (image_dir / f"{image_id}.png").exists()

    # Create entry
    entry_resp = await client.post("/logbook/entries", json={}, headers=headers)
    entry_id = entry_resp.json()["id"]

    # Create image fragment referencing the image
    frag_resp = await client.post(
        f"/logbook/entries/{entry_id}/fragments",
        json={
            "kind": "image",
            "content": "A test caption",
            "data": {
                "image_id": image_id,
                "filename": "test.png",
                "mime_type": "image/png",
                "size_bytes": len(png),
            },
        },
        headers=headers,
    )
    assert frag_resp.status_code == 201
    fragment_id = frag_resp.json()["id"]

    # Delete fragment — should also delete the image file
    del_resp = await client.delete(
        f"/logbook/fragments/{fragment_id}",
        headers=headers,
    )
    assert del_resp.status_code == 204
    assert not (image_dir / f"{image_id}.png").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/PycharmProjects/ncs/lucid-logbook && python -m pytest tests/test_image_fragment_lifecycle.py -v`
Expected: FAIL — fragment delete currently rejects non-text kinds (`kind != "text"` check at line ~251 of api.py), and does not clean up image files.

- [ ] **Step 3: Modify delete_fragment to handle image fragments**

In `api.py`, update the `delete_fragment` method in `LogbookController`. The current code rejects deletion of non-text fragments. Change it to allow deletion of `image` kind fragments too, and clean up the image file:

```python
# In LogbookController.delete_fragment (around line 237-254):
# Replace the kind check and add image cleanup

@delete("/fragments/{fragment_id:uuid}", status_code=204)
async def delete_fragment(
    self, request: Request, db_session: AsyncSession, fragment_id: UUID
) -> None:
    result = await db_session.execute(
        select(FragmentRow).where(FragmentRow.id == fragment_id)
    )
    fragment = result.scalar_one_or_none()
    if fragment is None:
        raise NotFoundException(f"Fragment {fragment_id} not found")
    if fragment.kind not in ("text", "image"):
        raise ValidationException("Only text and image fragments can be deleted")

    # Clean up image file if this is an image fragment
    if fragment.kind == "image" and fragment.data and "image_id" in fragment.data:
        image_store: ImageStore = request.app.state.image_store
        image_store.delete(fragment.data["image_id"])

    await db_session.delete(fragment)
    await db_session.commit()
```

- [ ] **Step 4: Run tests**

Run: `cd ~/PycharmProjects/ncs/lucid-logbook && python -m pytest tests/test_image_fragment_lifecycle.py tests/test_image_api.py tests/test_image_store.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
cd ~/PycharmProjects/ncs/lucid-logbook
git add src/lucid_logbook/api.py tests/test_image_fragment_lifecycle.py
git commit -m "feat(logbook): cascade image file delete when image fragment is deleted"
```

---

## Task 4: Frontend — Add IMAGE fragment type and local image storage helpers

**Files:**
- Modify: `ncs/src/lucid/logbook/fragment_widgets.py` (~line 52, FragmentType enum)
- Modify: `ncs/src/lucid/logbook/client.py` (~line 42, schema; new methods after line ~482)

- [ ] **Step 1: Add IMAGE to FragmentType enum**

In `fragment_widgets.py`, extend the enum at ~line 52:

```python
class FragmentType(str, Enum):
    TEXT = "text"
    READONLY = "readonly"
    IMAGE = "image"
```

- [ ] **Step 2: Add image_sync table to client schema**

In `client.py`, add to `_SCHEMA` (around line 42-74), after the `fragment` table definition:

```sql
CREATE TABLE IF NOT EXISTS image_sync (
    image_id TEXT PRIMARY KEY,
    local_path TEXT NOT NULL,
    sync_status TEXT NOT NULL DEFAULT 'pending_upload'
);
```

- [ ] **Step 3: Add local image directory helper to LogbookClient**

In `client.py`, add a property and helper methods to `LogbookClient` (after the `_db_path` setup in `__init__`, around line 270):

```python
@property
def _image_dir(self) -> Path:
    """Local image storage directory."""
    d = Path.home() / ".lucid" / "logbook" / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d

def _save_image_locally(self, image_id: str, data: bytes, mime_type: str) -> Path:
    """Save image bytes to local storage, return the file path."""
    from lucid_logbook.image_store import ALLOWED_MIME_TYPES

    ext = ALLOWED_MIME_TYPES.get(mime_type, ".png")
    path = self._image_dir / f"{image_id}{ext}"
    path.write_bytes(data)
    return path

def _get_local_image_path(self, image_id: str) -> Path | None:
    """Find a locally stored image by ID, or None if not present."""
    from lucid_logbook.image_store import ALLOWED_MIME_TYPES

    for ext in ALLOWED_MIME_TYPES.values():
        path = self._image_dir / f"{image_id}{ext}"
        if path.exists():
            return path
    return None
```

Add `from pathlib import Path` to imports if not present.

- [ ] **Step 4: Add add_image() method to LogbookClient**

Add after the `list_fragments()` method (~line 482):

```python
def add_image(
    self,
    image: bytes | str | Path | QImage | QPixmap,
    caption: str = "",
    subtype: str = "clipboard",
    entry_id: str | None = None,
) -> str:
    """Add an image fragment to the logbook.

    Args:
        image: Image as raw bytes, file path, QImage, or QPixmap.
        caption: Optional caption text.
        subtype: Fragment subtype for styling.
        entry_id: Target entry ID. None = current entry.

    Returns:
        The new fragment ID.
    """
    from PySide6.QtGui import QImage, QPixmap
    from PySide6.QtCore import QBuffer, QIODevice

    # Normalize input to PNG bytes
    if isinstance(image, QPixmap):
        image = image.toImage()
    if isinstance(image, QImage):
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buf, "PNG")
        data = bytes(buf.data())
        mime_type = "image/png"
    elif isinstance(image, (str, Path)):
        path = Path(image)
        data = path.read_bytes()
        suffix = path.suffix.lower()
        mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif"}
        mime_type = mime_map.get(suffix, "image/png")
    elif isinstance(image, bytes):
        data = image
        # Detect from magic bytes
        if data[:4] == b"\xff\xd8\xff\xe0" or data[:4] == b"\xff\xd8\xff\xe1":
            mime_type = "image/jpeg"
        elif data[:4] == b"GIF8":
            mime_type = "image/gif"
        else:
            mime_type = "image/png"
    else:
        raise TypeError(f"Unsupported image type: {type(image)}")

    image_id = str(uuid.uuid4())

    # Save to local disk
    local_path = self._save_image_locally(image_id, data, mime_type)

    # Get image dimensions
    qimg = QImage()
    qimg.loadFromData(data)
    width = qimg.width() if not qimg.isNull() else 0
    height = qimg.height() if not qimg.isNull() else 0

    from lucid_logbook.image_store import ALLOWED_MIME_TYPES
    ext = ALLOWED_MIME_TYPES.get(mime_type, ".png")
    filename = f"{image_id}{ext}"

    # Create image fragment in local DB
    fragment_id = self.add_fragment(
        entry_id=entry_id or self._current_entry_id,
        kind="image",
        subtype=subtype,
        content=caption,
        data={
            "image_id": image_id,
            "filename": filename,
            "mime_type": mime_type,
            "width": width,
            "height": height,
            "size_bytes": len(data),
        },
    )

    # Track for sync
    with self._db:
        self._db.execute(
            "INSERT INTO image_sync (image_id, local_path, sync_status) VALUES (?, ?, ?)",
            (image_id, str(local_path), "pending_upload"),
        )

    self.schedule_sync()
    return fragment_id
```

- [ ] **Step 5: Verify it loads without errors**

Run: `cd ~/PycharmProjects/ncs && python -c "from lucid.logbook.fragment_widgets import FragmentType; print(FragmentType.IMAGE)"`
Expected: `image`

- [ ] **Step 6: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs
git add src/lucid/logbook/fragment_widgets.py src/lucid/logbook/client.py
git commit -m "feat(logbook): add IMAGE fragment type, local storage helpers, and add_image() API"
```

---

## Task 5: Frontend — Extend sync protocol for image push/pull

**Files:**
- Modify: `ncs/src/lucid/logbook/client.py` (~line 77, `_run_sync` function)

- [ ] **Step 1: Add image push phase to _run_sync()**

In `client.py`, the `_run_sync()` function (line ~77) handles push then pull. Add image push **before** metadata push. Insert after the function opens the DB connection but before the existing push logic:

```python
# --- Image push phase (before metadata push) ---
# Upload pending images to server
cursor = db.execute(
    "SELECT image_id, local_path FROM image_sync WHERE sync_status = 'pending_upload'"
)
pending_images = cursor.fetchall()
for image_id, local_path in pending_images:
    path = Path(local_path)
    if not path.exists():
        # Local file gone — mark synced to avoid retrying
        db.execute(
            "UPDATE image_sync SET sync_status = 'synced' WHERE image_id = ?",
            (image_id,),
        )
        continue
    try:
        with open(path, "rb") as f:
            resp = http.post(
                f"{base_url}/logbook/images",
                files={"file": (path.name, f, _mime_from_ext(path.suffix))},
                headers=headers,
            )
        if resp.status_code == 201:
            db.execute(
                "UPDATE image_sync SET sync_status = 'synced' WHERE image_id = ?",
                (image_id,),
            )
    except Exception:
        pass  # Retry next cycle
db.commit()
```

Add this helper at the module level (above `_run_sync`):

```python
def _mime_from_ext(ext: str) -> str:
    return {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif"}.get(
        ext.lower(), "image/png"
    )
```

- [ ] **Step 2: Add image pull phase to _run_sync()**

After the existing pull logic in `_run_sync()`, add:

```python
# --- Image pull phase (after metadata pull) ---
# Find image fragments that don't have a local file
cursor = db.execute(
    "SELECT id, data FROM fragment WHERE kind = 'image' AND sync_status != 'deleted'"
)
for frag_id, data_json in cursor.fetchall():
    import json as _json
    data = _json.loads(data_json) if data_json else {}
    image_id = data.get("image_id")
    if not image_id:
        continue

    # Check if we already have it locally
    existing = db.execute(
        "SELECT sync_status FROM image_sync WHERE image_id = ?", (image_id,)
    ).fetchone()
    if existing and existing[0] == "synced":
        continue
    # Check if file exists on disk already
    from lucid_logbook.image_store import ALLOWED_MIME_TYPES
    mime_type = data.get("mime_type", "image/png")
    ext = ALLOWED_MIME_TYPES.get(mime_type, ".png")
    image_dir = Path.home() / ".lucid" / "logbook" / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    local_path = image_dir / f"{image_id}{ext}"

    if local_path.exists():
        # File exists, ensure tracked
        db.execute(
            "INSERT OR REPLACE INTO image_sync (image_id, local_path, sync_status) VALUES (?, ?, 'synced')",
            (image_id, str(local_path)),
        )
        continue

    # Download from server
    try:
        resp = http.get(f"{base_url}/logbook/images/{image_id}", headers=headers)
        if resp.status_code == 200:
            local_path.write_bytes(resp.content)
            db.execute(
                "INSERT OR REPLACE INTO image_sync (image_id, local_path, sync_status) VALUES (?, ?, 'synced')",
                (image_id, str(local_path)),
            )
    except Exception:
        # Track as pending download for retry
        db.execute(
            "INSERT OR IGNORE INTO image_sync (image_id, local_path, sync_status) VALUES (?, ?, 'pending_download')",
            (image_id, str(local_path)),
        )
db.commit()
```

- [ ] **Step 3: Test sync manually**

Run: `cd ~/PycharmProjects/ncs && python -c "from lucid.logbook.client import LogbookClient; print('sync module loads OK')"`
Expected: `sync module loads OK`

- [ ] **Step 4: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs
git add src/lucid/logbook/client.py
git commit -m "feat(logbook): extend sync protocol with image push/pull phases"
```

---

## Task 6: Frontend — ImageFragmentWidget

**Files:**
- Modify: `ncs/src/lucid/logbook/fragment_widgets.py` (add new class after `ReadonlyFragmentWidget`, ~line 619)

- [ ] **Step 1: Add ImageFragmentWidget class**

Insert after `ReadonlyFragmentWidget` (around line 619), before `CollapsibleGroup`:

```python
class ImageFragmentWidget(QFrame):
    """Displays an image fragment with thumbnail and caption."""

    delete_requested = Signal(str)  # fragment_id
    caption_changed = Signal(str, str)  # fragment_id, new_caption
    claude_requested = Signal(str)  # fragment_id

    THUMBNAIL_MAX_WIDTH = 400

    def __init__(self, data: FragmentData, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data = data
        self._image_id = data.metadata.get("image_id", "")
        self._setup_ui()
        self._load_image()

    def _setup_ui(self) -> None:
        self.setObjectName("imageFragment")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)

        # Accent bar styling (same pattern as ReadonlyFragmentWidget)
        accent = SUBTYPE_COLORS.get(self._data.subtype, "#607d8b")
        self.setStyleSheet(
            f"""
            QFrame#imageFragment {{
                border-left: 3px solid {accent};
                background: rgba(255, 255, 255, 0.03);
                border-radius: 4px;
                margin: 2px 0;
            }}
            """
        )

        # Thumbnail label
        self._thumbnail = QLabel()
        self._thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumbnail.setCursor(Qt.CursorShape.PointingHandCursor)
        self._thumbnail.mousePressEvent = self._on_thumbnail_clicked
        layout.addWidget(self._thumbnail)

        # Caption (editable)
        self._caption = QLineEdit(self._data.content)
        self._caption.setPlaceholderText("Add a caption...")
        self._caption.setStyleSheet("QLineEdit { background: transparent; border: none; color: #aaa; }")
        self._caption.editingFinished.connect(self._on_caption_edited)
        layout.addWidget(self._caption)

        # Overlay for hover actions
        self._overlay = FragmentOverlay(self)
        self._overlay.delete_clicked.connect(lambda: self.delete_requested.emit(self._data.id))
        self._overlay.claude_clicked.connect(lambda: self.claude_requested.emit(self._data.id))

        # Placeholder for syncing state
        self._placeholder = QLabel("Image syncing...")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet("color: #888; padding: 20px;")
        self._placeholder.setVisible(False)
        layout.addWidget(self._placeholder)

    def _load_image(self) -> None:
        """Load image from local storage."""
        from lucid.logbook.client import LogbookClient

        client = LogbookClient.instance()
        path = client._get_local_image_path(self._image_id)

        if path is None:
            self._thumbnail.setVisible(False)
            self._placeholder.setVisible(True)
            return

        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self._thumbnail.setText("Failed to load image")
            return

        self._full_pixmap = pixmap
        scaled = pixmap.scaledToWidth(
            min(self.THUMBNAIL_MAX_WIDTH, pixmap.width()),
            Qt.TransformationMode.SmoothTransformation,
        )
        self._thumbnail.setPixmap(scaled)
        self._thumbnail.setVisible(True)
        self._placeholder.setVisible(False)

    def _on_thumbnail_clicked(self, event) -> None:
        """Open full-size image in a dialog."""
        if not hasattr(self, "_full_pixmap"):
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(self._data.content or "Image")
        dialog.setMinimumSize(400, 300)
        layout = QVBoxLayout(dialog)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        label = QLabel()
        label.setPixmap(self._full_pixmap)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll.setWidget(label)
        layout.addWidget(scroll)

        dialog.resize(
            min(self._full_pixmap.width() + 40, 1200),
            min(self._full_pixmap.height() + 40, 800),
        )
        dialog.exec()

    def _on_caption_edited(self) -> None:
        new_caption = self._caption.text()
        if new_caption != self._data.content:
            self._data.content = new_caption
            self.caption_changed.emit(self._data.id, new_caption)

    def refresh_image(self) -> None:
        """Reload the image (e.g., after sync downloads it)."""
        self._load_image()

    @property
    def fragment_data(self) -> FragmentData:
        return self._data
```

- [ ] **Step 2: Add required imports at the top of fragment_widgets.py**

Ensure these are imported (add any that are missing):

```python
from PySide6.QtWidgets import QLineEdit, QDialog, QScrollArea
from PySide6.QtGui import QPixmap
```

- [ ] **Step 3: Verify the widget loads**

Run: `cd ~/PycharmProjects/ncs && python -c "from lucid.logbook.fragment_widgets import ImageFragmentWidget; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs
git add src/lucid/logbook/fragment_widgets.py
git commit -m "feat(logbook): add ImageFragmentWidget with thumbnail, caption, and full-size viewer"
```

---

## Task 7: Frontend — Wire ImageFragmentWidget into EntryWidget

**Files:**
- Modify: `ncs/src/lucid/logbook/entry_widget.py` (~line 394, `_rebuild_fragments`)

- [ ] **Step 1: Import ImageFragmentWidget**

At the top of `entry_widget.py`, add to the imports from `fragment_widgets`:

```python
from lucid.logbook.fragment_widgets import (
    FragmentData,
    FragmentType,
    TextFragmentWidget,
    ReadonlyFragmentWidget,
    ImageFragmentWidget,
    CollapsibleGroup,
)
```

- [ ] **Step 2: Handle IMAGE type in _rebuild_fragments()**

In the `_rebuild_fragments()` method (~line 394-426), find the block that creates widgets based on fragment type. Add a branch for `IMAGE`:

```python
# Inside the loop that creates fragment widgets:
if frag.fragment_type == FragmentType.IMAGE:
    widget = ImageFragmentWidget(frag)
    widget.delete_requested.connect(
        lambda fid: self.fragment_deleted.emit(self._entry.id, fid)
    )
    widget.caption_changed.connect(
        lambda fid, content: self.fragment_changed.emit(self._entry.id, fid, content)
    )
    widget.claude_requested.connect(
        lambda fid: self.claude_requested.emit(self._entry.id, fid)
    )
```

- [ ] **Step 3: Add image_added signal**

Add a new signal to `EntryWidget` (around line 150-156):

```python
image_added = Signal(str, str)  # entry_id, fragment_id
```

- [ ] **Step 4: Verify it loads**

Run: `cd ~/PycharmProjects/ncs && python -c "from lucid.logbook.entry_widget import EntryWidget; print('OK')"`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs
git add src/lucid/logbook/entry_widget.py
git commit -m "feat(logbook): wire ImageFragmentWidget into EntryWidget fragment rendering"
```

---

## Task 8: Frontend — Add Image button and clipboard paste in LogbookPanel

**Files:**
- Modify: `ncs/src/lucid/ui/panels/logbook_panel.py`

- [ ] **Step 1: Add image button to the panel toolbar**

Find where the toolbar/buttons are set up in `logbook_panel.py`. Add an "Add Image" button using qtawesome:

```python
import qtawesome as qta
from PySide6.QtWidgets import QFileDialog

# In the toolbar setup area:
self._add_image_btn = QPushButton()
self._add_image_btn.setIcon(qta.icon("fa5s.image", color="#aaa"))
self._add_image_btn.setToolTip("Add image to entry")
self._add_image_btn.setFixedSize(28, 28)
self._add_image_btn.clicked.connect(self._on_add_image_clicked)
# Add to toolbar layout alongside existing buttons
```

- [ ] **Step 2: Implement the add-image-from-file handler**

```python
def _on_add_image_clicked(self) -> None:
    """Open file dialog, add selected image as a fragment."""
    if not self._current_entry_id:
        return

    path, _ = QFileDialog.getOpenFileName(
        self,
        "Select Image",
        "",
        "Images (*.png *.jpg *.jpeg *.gif);;All Files (*)",
    )
    if not path:
        return

    from pathlib import Path as P
    file_path = P(path)
    if file_path.stat().st_size > 20 * 1024 * 1024:
        from pyqttoast import Toast, ToastPreset
        toast = Toast(self)
        toast.setTitle("Image too large")
        toast.setText("Maximum image size is 20 MB.")
        toast.applyPreset(ToastPreset.ERROR)
        toast.show()
        return

    frag_id = self._client.add_image(
        image=path,
        caption="",
        subtype="clipboard",
        entry_id=self._current_entry_id,
    )
    self._refresh_current_entry()
```

- [ ] **Step 3: Add clipboard paste handler**

Override or connect to the key press event on the entry widget area to intercept Ctrl+V when clipboard has image data:

```python
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

def _install_paste_handler(self) -> None:
    """Install event filter on entry widget to catch image paste."""
    self._entry_widget.installEventFilter(self)

def eventFilter(self, obj, event) -> bool:
    """Intercept Ctrl+V when clipboard has an image."""
    if (
        obj is self._entry_widget
        and isinstance(event, QKeyEvent)
        and event.matches(QKeySequence.StandardKey.Paste)
    ):
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()
        if mime and mime.hasImage():
            self._paste_image_from_clipboard()
            return True  # consumed
    return super().eventFilter(obj, event)

def _paste_image_from_clipboard(self) -> None:
    """Grab image from clipboard, add as fragment."""
    if not self._current_entry_id:
        return

    clipboard = QApplication.clipboard()
    qimage = clipboard.image()
    if qimage.isNull():
        return

    frag_id = self._client.add_image(
        image=qimage,
        caption="",
        subtype="clipboard",
        entry_id=self._current_entry_id,
    )
    self._refresh_current_entry()
```

Call `self._install_paste_handler()` during panel initialization (after `_entry_widget` is created).

Add `from PySide6.QtGui import QKeySequence` to imports.

- [ ] **Step 4: Wire up caption changes to client**

Connect `ImageFragmentWidget.caption_changed` through to the client. This is handled by the existing `_on_fragment_changed` handler since `caption_changed` emits `fragment_changed` with the caption as content, and the existing handler calls `client.update_fragment(fragment_id, content=content)`.

No additional wiring needed — the signals connected in Task 7 already route through `fragment_changed`.

- [ ] **Step 5: Verify it loads**

Run: `cd ~/PycharmProjects/ncs && python -c "from lucid.ui.panels.logbook_panel import LogbookPanel; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
cd ~/PycharmProjects/ncs/ncs
git add src/lucid/ui/panels/logbook_panel.py
git commit -m "feat(logbook): add image button and clipboard paste support to LogbookPanel"
```

---

## Task Summary

| Task | Component | What it builds |
|------|-----------|---------------|
| 1 | Backend | `ImageStore` — file save/load/delete with validation |
| 2 | Backend | REST endpoints — upload, download, delete images |
| 3 | Backend | Cascade delete — image file cleaned up when fragment deleted |
| 4 | Frontend | `IMAGE` fragment type, local storage helpers, `add_image()` API |
| 5 | Frontend | Sync protocol — image push before metadata, image pull after |
| 6 | Frontend | `ImageFragmentWidget` — thumbnail + caption + full-size viewer |
| 7 | Frontend | Wire `ImageFragmentWidget` into `EntryWidget` rendering |
| 8 | Frontend | Add Image button (qtawesome) + clipboard paste in `LogbookPanel` |
