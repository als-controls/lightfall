# Logbook Image Support

Add image support to LUCID's logbook system — both backend (lucid-logbook) and frontend (LUCID Qt app). Users can paste images from clipboard or add them via a button, and code can insert images programmatically.

## Use Cases

1. **Clipboard paste** — user pastes a screenshot (plot, camera view, error dialog) into the logbook as a visual note.
2. **Programmatic insertion** — code (e.g., a Bluesky plan) attaches an image (e.g., scan results plot) to the current logbook entry.

## Constraints

- Supported formats: PNG, JPEG, GIF
- Max image size: 20MB per image
- Images stored as files on disk (not in the database)
- Bidirectional sync between client and server

## Data Model

### New fragment kind: `image`

Alongside existing `text` and `readonly` kinds:

```
Fragment (kind=image):
  content: str          # caption (optional, user-editable)
  subtype: str          # e.g. "bluesky_plan", "clipboard", "screenshot"
  data: {
    "image_id": "uuid",
    "filename": "scan_42.png",
    "mime_type": "image/png",
    "width": 1024,
    "height": 768,
    "size_bytes": 245000
  }
```

### Local image storage

Path: `~/.lucid/logbook/images/{image_id}.{ext}`

### Server image storage

Configurable directory, default: `./logbook_images/{image_id}.{ext}`

### Local DB addition: `image_sync` table

```sql
CREATE TABLE IF NOT EXISTS image_sync (
    image_id TEXT PRIMARY KEY,
    local_path TEXT NOT NULL,
    sync_status TEXT NOT NULL  -- 'pending_upload', 'pending_download', 'synced'
);
```

Tracks whether each image file has been uploaded to or downloaded from the server.

## Backend API

New endpoints on lucid-logbook (Litestar):

### `POST /logbook/images`

- Multipart upload: file + JSON metadata (mime_type, width, height)
- Validates format (PNG/JPEG/GIF) and size (<=20MB)
- Validates image is readable (header check)
- Saves file to server image directory, returns `image_id`
- Auth: same Keycloak/header middleware as existing endpoints

### `GET /logbook/images/{image_id}`

- Returns image bytes with correct `Content-Type` header
- Auth: user must own the logbook that contains the referencing fragment

### `DELETE /logbook/images/{image_id}`

- Removes file from disk
- Called during image fragment deletion cleanup

### Image creation flow

1. Client uploads image to `POST /images` -> gets `image_id`
2. Client creates fragment with `kind=image` via existing `POST /fragments`, referencing `image_id` in `data`

### Image deletion flow

When a fragment with `kind=image` is deleted, the backend also deletes the associated image file. Handled in the existing fragment delete logic.

## Sync Protocol

### Current sync phases

1. Push metadata (pending entries + fragments -> server)
2. Pull metadata (fetch remote entries + fragments -> local)

### Extended sync phases

```
1. Push images      (upload files for pending_upload image_sync rows)
2. Push metadata    (entries + fragments, including new image fragments)
3. Pull metadata    (fetch remote entries + fragments)
4. Pull images      (download files for image fragments missing locally)
```

### Push images

- Query `image_sync` for `pending_upload` rows
- Upload each to `POST /images`
- Mark as `synced` on success

Images upload before metadata push so the server always has the file before any fragment references it.

### Pull images

- After pulling fragment metadata, scan for `kind=image` fragments where `image_id` has no local file
- Insert `pending_download` rows into `image_sync`
- Download from `GET /images/{image_id}`
- Save to local image directory, mark `synced`

### Failure handling

- Image upload/download failures do not block metadata sync
- Failed images remain in their pending state, retried on the next sync cycle
- UI shows a placeholder ("image syncing...") until the file arrives locally

## Frontend

### ImageFragmentWidget

- Thumbnail display: scaled to max 400px width, maintaining aspect ratio
- Caption below thumbnail: editable, stored in fragment `content`
- Click on thumbnail: opens full-size image in a `QDialog` with scroll area
- Color accent bar based on `subtype`, consistent with existing fragment widgets
- Placeholder state: spinner/message while image is `pending_download`

### Add Image button

- QtAwesome icon button (`fa5s.image`) in the entry toolbar
- Opens `QFileDialog` filtered to `*.png *.jpg *.jpeg *.gif`
- Validates size <= 20MB before accepting
- Creates image fragment at the end of the current entry

### Clipboard paste

- When entry widget area has focus and user pastes, check `QApplication.clipboard().mimeData().hasImage()`
- If image data present: convert `QImage` to PNG bytes, save locally, create image fragment
- If clipboard has text: existing paste behavior unchanged
- No confirmation dialog; undo via fragment delete

### Programmatic API

New method on `LogbookClient`:

```python
def add_image(
    self,
    image: bytes | str | Path | QImage | QPixmap,
    caption: str = "",
    subtype: str = "clipboard",
    entry_id: str | None = None,  # None = current entry
) -> str:  # returns fragment_id
```

- Normalizes all input types to PNG bytes
- Saves to local image directory (`~/.lucid/logbook/images/`)
- Creates image fragment in local DB with `sync_status='pending'`
- Inserts `pending_upload` row in `image_sync`
- Returns the new fragment ID

### Not in scope

- Drag-and-drop from file explorer
- SVG, TIFF, or other format support
- Image editing/annotation
- Range header support for large downloads
