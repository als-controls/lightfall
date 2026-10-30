"""One full update check: every source repo and index-installed package."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from lightfall.updates import git as git_mod
from lightfall.updates.discovery import discover_installed, group_by_repo
from lightfall.updates.git import GitError, RepoState, read_repo_state
from lightfall.updates.pypi import DEFAULT_INDEX_URL, PypiState, latest_version
from lightfall.utils.logging import logger


@dataclass(frozen=True)
class CheckResult:
    """What one check found. Unreachable repos and packages are simply absent."""

    repos: tuple[RepoState, ...] = ()
    pypi: tuple[PypiState, ...] = ()

    EMPTY: ClassVar[CheckResult]

    @property
    def repo_updates(self) -> list[RepoState]:
        return [r for r in self.repos if r.has_update]

    @property
    def pypi_updates(self) -> list[PypiState]:
        return [p for p in self.pypi if p.has_update]

    @property
    def update_count(self) -> int:
        return len(self.repo_updates) + len(self.pypi_updates)

    @property
    def noteworthy_repos(self) -> list[RepoState]:
        return [r for r in self.repos if r.noteworthy]


CheckResult.EMPTY = CheckResult()


def run_check(fetch: bool, index_url: str = DEFAULT_INDEX_URL) -> CheckResult:
    """Check every installed Lightfall distribution (blocking; run off the GUI thread)."""
    installed = discover_installed()
    repos: list[RepoState] = []
    if not git_mod.GIT_MISSING:
        for root, dists in group_by_repo(installed).items():
            try:
                repos.append(read_repo_state(root, dists, fetch=fetch))
            except GitError as e:
                logger.debug("Update check: skipping {}: {}", root, e)
    pypi: list[PypiState] = []
    if fetch:
        for item in installed:
            if item.kind == "pypi":
                pypi.append(PypiState(item.name, item.version,
                                      latest_version(item.name, item.version, index_url)))
    pypi = [p for p in pypi if p.latest is not None]
    return CheckResult(tuple(repos), tuple(pypi))
