from __future__ import annotations

from lightfall.scopes.local_files import load_local_state, local_chain


def _write(d, name, text):
    (d / name).write_text(text, encoding="utf-8")


def test_chain_from_bind():
    assert local_chain("branch:7.0.1.1") == ("facility", "beamline:7.0.1", "branch:7.0.1.1")
    assert local_chain("beamline:7.0.1") == ("facility", "beamline:7.0.1")
    assert local_chain(None) == ("facility",)


def test_no_files_means_no_state(tmp_path):
    assert load_local_state(tmp_path, "branch:7.0.1.1") is None


def test_reads_settings_and_locks(tmp_path):
    _write(tmp_path, "facility.yaml", "settings:\n  tiled_url: https://tiled\n")
    _write(tmp_path, "branch-7.0.1.1.yaml",
           "settings:\n  device_bcs_host: bcs011\nlocks:\n  device_bcs_host: fixed by staff\n")
    state = load_local_state(tmp_path, "branch:7.0.1.1")
    assert state.chain.chain == ("facility", "beamline:7.0.1", "branch:7.0.1.1")
    assert state.snapshots["facility"].settings == {"tiled_url": "https://tiled"}
    assert state.snapshots["branch:7.0.1.1"].locks["device_bcs_host"]["reason"] == "fixed by staff"
    assert state.snapshots["beamline:7.0.1"].settings == {}


def test_malformed_files_are_ignored(tmp_path):
    _write(tmp_path, "facility.yaml", "- just\n- a list\n")
    _write(tmp_path, "beamline-7.0.1.yaml", "settings: [1, 2]\n")
    _write(tmp_path, "branch-7.0.1.1.yaml", "settings: {a: [unclosed\n")
    state = load_local_state(tmp_path, "branch:7.0.1.1")
    assert state is not None
    assert all(not s.settings for s in state.snapshots.values())


def test_local_only_keys_in_files_are_dropped(tmp_path):
    _write(tmp_path, "facility.yaml", "settings:\n  proxy_host: x\n  claude_api_key: sk\n  theme: dark\n")
    assert load_local_state(tmp_path, None).snapshots["facility"].settings == {"theme": "dark"}
