from __future__ import annotations

from pathlib import Path

from app.db import Database
from app.ids import is_valid_id


def _register(db: Database, unique: str = "uniq-1", **kw: object):
    params = dict(
        file_id=f"file-{unique}",
        file_unique_id=unique,
        file_name="livro.pdf",
        file_size=1234,
        chat_id=-100123,
        message_id=10,
    )
    params.update(kw)
    return db.register(**params)  # type: ignore[arg-type]


def test_register_and_get(tmp_path: Path) -> None:
    db = Database(tmp_path / "a.db")
    record, created = _register(db)
    assert created
    assert is_valid_id(record.id)
    fetched = db.get(record.id)
    assert fetched == record
    assert fetched.file_name == "livro.pdf"
    assert fetched.chat_id == -100123 and fetched.message_id == 10
    assert fetched.created_at


def test_duplicate_file_unique_id_returns_existing(tmp_path: Path) -> None:
    db = Database(tmp_path / "a.db")
    first, created1 = _register(db, "same")
    second, created2 = _register(db, "same", file_id="outro-file-id", message_id=99)
    assert created1 and not created2
    assert second.id == first.id
    assert db.count() == 1


def test_get_missing_returns_none(tmp_path: Path) -> None:
    db = Database(tmp_path / "a.db")
    assert db.get("naoexiste") is None
    assert db.get_by_unique_id("naoexiste") is None


def test_update_location_and_delete(tmp_path: Path) -> None:
    db = Database(tmp_path / "a.db")
    record, _ = _register(db, chat_id=None, message_id=None)
    db.update_location(record.id, -100999, 55)
    updated = db.get(record.id)
    assert updated is not None and updated.chat_id == -100999 and updated.message_id == 55
    assert db.delete(record.id)
    assert db.get(record.id) is None
    assert not db.delete(record.id)


def test_recent_and_count(tmp_path: Path) -> None:
    db = Database(tmp_path / "a.db")
    ids = [_register(db, f"u{i}")[0].id for i in range(5)]
    assert db.count() == 5
    recent = db.recent(3)
    assert [r.id for r in recent] == list(reversed(ids))[:3]


def test_data_persists_after_reopen(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "persist.db"
    db = Database(path)
    record, _ = _register(db)
    db.close()
    reopened = Database(path)
    assert reopened.get(record.id) == record
    assert reopened.get_by_unique_id("uniq-1") == record
