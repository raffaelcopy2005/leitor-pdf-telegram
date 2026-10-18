from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.telegram_auth import make_download_token

from .conftest import BOT_TOKEN, make_init_data, make_settings

PDF_BYTES = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


class FakeSource:
    def __init__(self) -> None:
        self.calls = 0

    async def fetch(self, file_id: str, dest: Path) -> None:
        self.calls += 1
        dest.write_bytes(PDF_BYTES)


@pytest.fixture
def ctx(tmp_path: Path):
    def build(**overrides: object):
        source = FakeSource()
        app = create_app(make_settings(tmp_path, **overrides), file_source=source)
        client = TestClient(app)
        client.__enter__()
        record, _ = app.state.db.register(
            file_id="FILEID", file_unique_id="UNIQ", file_name="Apostila Ação.pdf",
            file_size=len(PDF_BYTES), chat_id=-100, message_id=1,
        )
        built.append(client)
        return client, record, source

    built: list[TestClient] = []
    yield build
    for client in built:
        client.__exit__(None, None, None)


def _auth(user_id: int = 42) -> dict[str, str]:
    return {"X-Telegram-Init-Data": make_init_data(user_id)}


def test_health(ctx) -> None:
    client, _, _ = ctx()
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok", "bot": "disabled", "pdfs": 1}


def test_frontend_is_served(ctx) -> None:
    client, _, _ = ctx()
    res = client.get("/")
    assert res.status_code == 200
    assert "telegram-web-app.js" in res.text
    assert client.get("/app.js").status_code == 200


def test_meta_requires_init_data(ctx) -> None:
    client, record, _ = ctx()
    assert client.get(f"/api/pdf/{record.id}/meta").status_code == 401
    bad = {"X-Telegram-Init-Data": make_init_data(42, token="1:outro")}
    assert client.get(f"/api/pdf/{record.id}/meta", headers=bad).status_code == 401


def test_meta_ok(ctx) -> None:
    client, record, _ = ctx()
    res = client.get(f"/api/pdf/{record.id}/meta", headers=_auth())
    assert res.status_code == 200
    body = res.json()
    assert body["file_name"] == "Apostila Ação.pdf"
    assert body["file_size"] == len(PDF_BYTES)
    assert body["created_at"]
    assert "file_id" not in body


def test_unknown_or_invalid_id_is_404(ctx) -> None:
    client, _, _ = ctx()
    assert client.get("/api/pdf/naoexiste/meta", headers=_auth()).status_code == 404
    assert client.get("/api/pdf/..%2Fetc/meta", headers=_auth()).status_code == 404


def test_allowed_user_ids_enforced(ctx) -> None:
    client, record, _ = ctx(allowed_user_ids=(1, 2))
    assert client.get(f"/api/pdf/{record.id}/meta", headers=_auth(42)).status_code == 403
    assert client.get(f"/api/pdf/{record.id}/meta", headers=_auth(2)).status_code == 200


def test_owner_is_always_allowed(ctx) -> None:
    client, record, _ = ctx(allowed_user_ids=(1,), owner_id=99)
    assert client.get(f"/api/pdf/{record.id}/meta", headers=_auth(99)).status_code == 200


def test_file_is_streamed_and_cached(ctx) -> None:
    client, record, source = ctx()
    for _ in range(2):
        res = client.get(f"/api/pdf/{record.id}/file", headers=_auth())
        assert res.status_code == 200
        assert res.headers["content-type"] == "application/pdf"
        assert res.headers["content-disposition"].startswith("inline")
        assert res.content == PDF_BYTES
        assert BOT_TOKEN not in res.text
    assert source.calls == 1


def test_file_requires_auth(ctx) -> None:
    client, record, source = ctx()
    assert client.get(f"/api/pdf/{record.id}/file").status_code == 401
    assert source.calls == 0


def test_download_link_flow(ctx) -> None:
    client, record, _ = ctx()
    assert client.post(f"/api/pdf/{record.id}/download-link").status_code == 401
    res = client.post(f"/api/pdf/{record.id}/download-link", headers=_auth())
    assert res.status_code == 200
    url = urlparse(res.json()["url"])
    assert url.netloc == "exemplo.test"
    assert BOT_TOKEN not in res.text
    dl = client.get(f"{url.path}?{url.query}")  # sem header: o token basta
    assert dl.status_code == 200
    assert dl.headers["content-disposition"].startswith("attachment")
    assert dl.content == PDF_BYTES


def test_download_token_bound_to_pdf(ctx) -> None:
    client, record, _ = ctx()
    other_token = make_download_token("outroid", BOT_TOKEN)
    assert client.get(f"/api/pdf/{record.id}/file?token={other_token}").status_code == 401
    assert client.get(f"/api/pdf/{record.id}/file?token=123.abc").status_code == 401
