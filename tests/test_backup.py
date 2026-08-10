"""Tests for the local snapshot backup system."""
import datetime
import shutil

from assistant import backup


def _fake_data(tmp_path):
    data = tmp_path / "data"
    (data / "chats").mkdir(parents=True)
    (data / "memory.json").write_text('{"name": {"value": "Janak"}}', encoding="utf-8")
    (data / "chats" / "abc.json").write_text("{}", encoding="utf-8")
    return data


def test_create_backup_copies_data(tmp_path, monkeypatch):
    data = _fake_data(tmp_path)
    monkeypatch.setattr(backup, "DATA_DIR", data)
    monkeypatch.setattr(backup, "BACKUP_ROOT", tmp_path / "backups")
    msg = backup.create_backup()
    assert "Backed up" in msg
    dirs = list((tmp_path / "backups").iterdir())
    assert len(dirs) == 1
    snap = dirs[0]
    assert (snap / "memory.json").exists()
    assert (snap / "chats" / "abc.json").exists()
    assert snap.read_bytes is not None


def test_backup_if_due_creates_one_per_day(tmp_path, monkeypatch):
    data = _fake_data(tmp_path)
    monkeypatch.setattr(backup, "DATA_DIR", data)
    monkeypatch.setattr(backup, "BACKUP_ROOT", tmp_path / "backups")
    first = backup.backup_if_due()
    assert first is not None and "Backed up" in first
    # same day → no-op
    assert backup.backup_if_due() is None
    assert len(backup.list_backups()) == 1


def test_prune_keeps_newest(tmp_path, monkeypatch):
    data = _fake_data(tmp_path)
    monkeypatch.setattr(backup, "DATA_DIR", data)
    monkeypatch.setattr(backup, "BACKUP_ROOT", tmp_path / "backups")
    monkeypatch.setattr(backup, "MAX_BACKUPS", 3)
    for _ in range(5):
        backup.create_backup()
    backups = backup.list_backups()
    assert len(backups) == 3
    # the newest survives
    assert backups[0] == max(backups)


def test_latest_backup_time_and_size(tmp_path, monkeypatch):
    data = _fake_data(tmp_path)
    monkeypatch.setattr(backup, "DATA_DIR", data)
    monkeypatch.setattr(backup, "BACKUP_ROOT", tmp_path / "backups")
    assert backup.latest_backup_time() is None
    backup.create_backup()
    ts = backup.latest_backup_time()
    assert ts is not None
    assert datetime.datetime.fromisoformat(ts)  # parses
    assert backup.size_bytes() > 0


def test_restore_backup(tmp_path, monkeypatch):
    data = _fake_data(tmp_path)
    monkeypatch.setattr(backup, "DATA_DIR", data)
    monkeypatch.setattr(backup, "BACKUP_ROOT", tmp_path / "backups")
    backup.create_backup()
    name = backup.list_backups()[0]

    # mutate the live data
    (data / "memory.json").write_text('{"corrupted": true}', encoding="utf-8")
    msg = backup.restore_backup(name)
    assert "Restored" in msg
    restored = (data / "memory.json").read_text(encoding="utf-8")
    assert "Janak" in restored  # back to the backed-up content


def test_restore_unknown_backup_is_safe(tmp_path, monkeypatch):
    data = _fake_data(tmp_path)
    monkeypatch.setattr(backup, "DATA_DIR", data)
    monkeypatch.setattr(backup, "BACKUP_ROOT", tmp_path / "backups")
    msg = backup.restore_backup("does_not_exist")
    assert "No backup named" in msg
    assert (data / "memory.json").exists()  # untouched


def test_no_data_dir_returns_message(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "DATA_DIR", tmp_path / "nope")
    monkeypatch.setattr(backup, "BACKUP_ROOT", tmp_path / "backups")
    assert "No data directory" in backup.create_backup()
