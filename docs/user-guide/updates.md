# Updates and Source Checkouts

Two status bar indicators keep an installation current. Both stay hidden when
there is nothing to report, so on an up-to-date machine you won't see them.

## Who sees them

The indicators appear only for someone who is **logged in** with a role that
holds the `ADMIN_SYSTEM` permission (today: ADMIN and DEVELOPER). In offline
mode, as a guest, or without that permission, no checks run and both
indicators stay hidden. Logging in starts the checks; logging out stops them.

## The updates indicator (⬆ N updates)

Lightfall looks at every installed Lightfall package: core `lightfall`, its
libraries (`lightfall-utils`, `lightfall-settings`), and the `lightfall-*`
plugins.

- **Source (editable) installs** are checked against their git remote. A
  checkout counts as having an update when it is on its default branch
  (usually `master`) and is behind the remote.
- **PyPI installs** are checked against the package index
  (`lightfall.updates.pypi.DEFAULT_INDEX_URL`, public PyPI for now).
  Pre-releases are only offered if you already run one.

The first check runs about 30 seconds after login, then every hour. Local git
state is refreshed every 5 minutes. A repo whose remote can't be reached, for
example because the machine has no credentials for it, is left out without a
warning.

Click the indicator to open the update dialog. It lists each update with its
current and available version. Tick the ones you want and press **Update
selected**:

- Source checkouts are fast-forwarded (`git pull --ff-only`). They are
  reinstalled with `pip install -e` only if the update changed
  `pyproject.toml`.
- PyPI packages are upgraded together in one `pip install --upgrade`.
- Each row shows **Updated** or **Failed: …**, and **Show log** displays the
  commands and their output.

When something changed, Lightfall asks whether to restart now. If you choose
**Later**, the indicator shows **⟳ Restart pending** until you restart.

### What blocks an update

A row can't be selected when its checkout:

- has uncommitted changes to tracked files (untracked files are ignored),
- is on a branch other than its default, or has a detached HEAD, or
- has unpushed commits (updates only ever fast-forward).

The whole dialog is blocked while a scan is running or queued. The blockers
are checked again at the moment you press **Update selected**.

**Known limitation:** the scan check only sees the Lightfall instance you are
working in. If two instances share one environment, updating from one does not
know about scans running in the other.

## The git-state indicator (⎇ N repos)

For source installs, this indicator shows when any Lightfall checkout is on a
feature branch or a detached HEAD, has uncommitted changes, or has unpushed
commits. The tooltip has one line per repo, and clicking it opens a read-only
list with an **Open folder** button for each checkout. It never changes a
repo.
