from portal.config import Settings


def test_smtp_vacio_es_none(monkeypatch):
    monkeypatch.setenv("PORTAL_SMTP_HOST", "")
    monkeypatch.setenv("PORTAL_SMTP_REMITENTE", "")
    s = Settings(database_url="x", fernet_key="y")
    assert s.smtp_host is None and s.smtp_remitente is None


def test_htmx_servido_localmente(client):
    assert client.get("/estatico/htmx.min.js").status_code == 200
    with open("portal/plantillas/base.html") as f:
        assert "unpkg" not in f.read()
