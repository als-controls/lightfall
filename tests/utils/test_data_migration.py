from pathlib import Path
from lightfall.utils.data_migration import migrate_legacy_data_dir


def test_migrates_when_only_legacy_exists(tmp_path):
    home = tmp_path
    legacy = home / "lucid"
    legacy.mkdir()
    (legacy / "plans").mkdir()
    (legacy / "plans" / "scan.py").write_text("# plan")

    moved = migrate_legacy_data_dir(home)

    assert moved is True
    assert (home / "lightfall" / "plans" / "scan.py").read_text() == "# plan"
    assert not legacy.exists()


def test_no_op_when_new_exists(tmp_path):
    home = tmp_path
    (home / "lucid").mkdir()
    (home / "lightfall").mkdir()

    moved = migrate_legacy_data_dir(home)

    assert moved is False
    assert (home / "lucid").exists()  # left untouched; new dir wins


def test_no_op_when_nothing_exists(tmp_path):
    assert migrate_legacy_data_dir(tmp_path) is False
