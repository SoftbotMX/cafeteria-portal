import smtplib

import httpx
import pytest

from portal.config import Settings
from portal.conduit import ErrorEnvio, enviar_whatsapp
from portal.correo import enviar_correo
from tests.conduit_falso import ConduitFalso

URL = "https://conduit.test"


def _enviar(f, **kw):
    return enviar_whatsapp(URL, "cli", "cond-s", "8341445807", "Hola ñ", "acceso:1:1", transport=f.transport, **kw)


def test_conduit_firma_y_cuerpo():
    f = ConduitFalso()
    assert _enviar(f) == "enviado"
    c = f.enviados[0]
    assert c["recipient"] == "528341445807" and c["channel"] == "whatsapp"
    assert c["client_id"] == "cli" and c["text"] == "Hola ñ" and c["idempotency_key"]


def test_conduit_duplicate_true_sigue_siendo_enviado():
    t = httpx.MockTransport(lambda r: httpx.Response(200, json={"duplicate": True}))
    assert enviar_whatsapp(URL, "c", "s", "8341445807", "x", "k", transport=t) == "enviado"


def test_conduit_409_es_en_curso():
    f = ConduitFalso(); f.responder = 409
    assert _enviar(f) == "en_curso"


def test_conduit_401_lanza():
    f = ConduitFalso(secreto="otro")
    with pytest.raises(ErrorEnvio):
        _enviar(f)


def test_conduit_red_lanza_sin_secreto():
    def boom(req):
        raise httpx.ConnectError("no", request=req)
    with pytest.raises(ErrorEnvio) as e:
        enviar_whatsapp(URL, "c", "sec-ultra", "8341445807", "x", "k", transport=httpx.MockTransport(boom))
    assert "sec-ultra" not in str(e.value)


def _settings(**kw):
    return Settings(database_url="x", fernet_key="y", **kw)


def test_correo_sin_host_lanza():
    with pytest.raises(ErrorEnvio):
        enviar_correo(_settings(), "a@b.mx", "as", "t")


def test_correo_envia_con_starttls(monkeypatch):
    log = []

    class S:
        def __init__(self, h, p, timeout=None): log.append(("c", h, p))
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self, **k): log.append("tls"); assert k["context"].verify_mode.name == "CERT_REQUIRED"
        def login(self, u, p): log.append(("login", u))
        def send_message(self, m): log.append(("msg", m["To"], m["Subject"]))
    monkeypatch.setattr(smtplib, "SMTP", S)
    enviar_correo(_settings(smtp_host="h", smtp_usuario="u", smtp_contrasena="p", smtp_remitente="r@x.mx"),
                  "a@b.mx", "Asunto ñ", "texto")
    assert log == [("c", "h", 587), "tls", ("login", "u"), ("msg", "a@b.mx", "Asunto ñ")]


def test_correo_falla_lanza(monkeypatch):
    def S(*a, **k): raise OSError("x")
    monkeypatch.setattr(smtplib, "SMTP", S)
    with pytest.raises(ErrorEnvio):
        enviar_correo(_settings(smtp_host="h"), "a@b.mx", "a", "t")


@pytest.mark.parametrize("tel", ["834144580", "83414458070", "83414458０7", "834-144-58", "", "+528341445"])
def test_conduit_telefono_invalido_no_envia(tel):
    f = ConduitFalso()
    with pytest.raises(ErrorEnvio):
        enviar_whatsapp(URL, "c", "cond-s", tel, "x", "k", transport=f.transport)
    assert f.enviados == []
