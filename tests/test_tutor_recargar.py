"""Lo que hace el papá: recargar, pagar en MP, regresar y ver sus recargas."""
import re

import pytest
from fastapi.testclient import TestClient

from portal.cifrado import cifrar
from portal.models import Recarga
from tests.conftest import FERNET_PRUEBAS
from tests.fabricas import crear_operador, crear_tutor, csrf_de, entrar_tutor, texto
from tests.mp_falso import MPFalso
from tests.tpv_falso import TPVFalso


@pytest.fixture
def op(db):
    return crear_operador(db, mp_access_token_cif=cifrar("APP_USR-token", FERNET_PRUEBAS))


@pytest.fixture
def tpv(app):
    f = TPVFalso(secreto_tutor="secreto-tutor")
    app.state.tpv_transport = f.transport
    return f


@pytest.fixture
def mp(app):
    f = MPFalso(token="APP_USR-token")
    app.state.mp_transport = f.transport
    return f


@pytest.fixture
def c(app):
    return TestClient(app, base_url="https://cafeteria.test", follow_redirects=False)


@pytest.fixture
def tutor(db, op):
    return crear_tutor(db, op, debe_cambiar=False)


@pytest.fixture
def dos(tutor, tpv, c, mp):
    luis = tpv.agregar_hijo(tutor.tutor_ref, "Luis Pérez", disponible=10)
    ana = tpv.agregar_hijo(tutor.tutor_ref, "Ana Pérez")
    assert entrar_tutor(c, "8341445807", "temporal123").status_code == 303
    return luis, ana


def _ocultos(html):
    return dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', html))


def _revisar(c, luis, ana, il="100", ia="50"):
    csrf = csrf_de(c.get("/recargar"))
    return c.post("/recargar/revisar", data={"csrf": csrf, f"importe_{luis['id']}": il,
                                             f"importe_{ana['id']}": ia})


def _pagar(c, luis, ana, **kw):
    r = _revisar(c, luis, ana, **kw)
    assert r.status_code == 200 and "/recargar/confirmar" in r.text, r.text
    return c.post("/recargar/confirmar", data=_ocultos(r.text))


def test_inicio_ofrece_recargar(dos, c):
    assert 'href="/recargar"' in c.get("/").text


def test_sin_token_no_hay_recargas(db, op, dos, c):
    op.mp_access_token_cif = None
    db.commit()
    assert 'href="/recargar"' not in c.get("/").text
    assert c.get("/recargar").status_code == 404


def test_un_solo_hijo_tambien_ve_recargar(tutor, tpv, c, mp):
    h = tpv.agregar_hijo(tutor.tutor_ref, "Luis")
    entrar_tutor(c, "8341445807", "temporal123")
    assert 'href="/recargar"' in c.get(f"/hijos/{h['id']}").text


def test_formulario_con_un_importe_por_hijo(dos, c):
    luis, ana = dos
    r = c.get("/recargar")
    assert f'name="importe_{luis["id"]}"' in r.text and f'name="importe_{ana["id"]}"' in r.text
    assert "$50" in r.text and "$2,000" in r.text


def test_revisar_muestra_reparto_y_total(dos, c):
    r = _revisar(c, *dos)
    t = texto(r.text)
    assert "Luis Pérez $100.00" in t and "Ana Pérez $50.00" in t and "Total $150.00" in t
    assert "Pagar en línea" in t and "No necesitas tener cuenta de Mercado Pago" in t
    assert '<a class="boton secundario" href="/">Cancelar</a>' in r.text  # CANCELAR va al inicio


@pytest.mark.parametrize("il,ia", [("20", "20"), ("2000", "5"), ("10.50", "50"), ("", "")])
def test_revisar_rechaza_importes(dos, c, il, ia):
    r = _revisar(c, *dos, il=il, ia=ia)
    assert "/recargar/confirmar" not in r.text and "pesos enteros" in r.text


def test_hijo_ajeno_no_entra_al_reparto(dos, c, tpv):
    otro = tpv.agregar_hijo("OTRO", "Ajeno")
    csrf = csrf_de(c.get("/recargar"))
    r = c.post("/recargar/revisar", data={"csrf": csrf, f"importe_{otro['id']}": "100"})
    assert "/recargar/confirmar" not in r.text


def test_confirmar_lleva_a_mercado_pago(db, dos, c, mp):
    r = _pagar(c, *dos)
    assert r.status_code == 303 and r.headers["location"].startswith("https://mp.test/checkout")
    rec = db.query(Recarga).one()
    assert rec.total == 150 and rec.estado == "pendiente"
    assert mp.preferencias[rec.mp_preferencia_id]["external_reference"] == str(rec.id)


def test_doble_toque_en_pagar_no_duplica(db, dos, c, mp):
    r = _revisar(c, *dos)
    datos = _ocultos(r.text)
    a = c.post("/recargar/confirmar", data=datos)
    b = c.post("/recargar/confirmar", data=datos)
    assert a.headers["location"] == b.headers["location"]
    assert db.query(Recarga).count() == 1 and len(mp.preferencias) == 1


def test_confirmar_sin_csrf(dos, c):
    datos = _ocultos(_revisar(c, *dos).text)
    datos["csrf"] = "malo"
    assert c.post("/recargar/confirmar", data=datos).status_code == 403


def test_mp_caido_permite_reintentar(db, dos, c, mp):
    mp.fallar.add("preferencia")
    datos = _ocultos(_revisar(c, *dos).text)
    r = c.post("/recargar/confirmar", data=datos)
    assert r.status_code == 200 and "Mercado Pago" in r.text and "/recargar/confirmar" in r.text
    mp.fallar.clear()
    assert c.post("/recargar/confirmar", data=_ocultos(r.text)).status_code == 303
    assert db.query(Recarga).count() == 1


def test_regreso_con_pago_aprobado_acredita(db, dos, c, mp, tpv):
    _pagar(c, *dos)
    rec = db.query(Recarga).one()
    mp.pagar(rec.id, 150)
    r = c.get(f"/recargas/{rec.id}")
    assert "Se acreditaron $150.00" in r.text
    assert dos[0]["disponible"] == 110 and len(tpv.cuerpos_recarga) == 1
    c.get(f"/recargas/{rec.id}")
    assert len(tpv.cuerpos_recarga) == 1


def test_regreso_sin_pago_ofrece_pagar(db, dos, c, mp):
    _pagar(c, *dos)
    rec = db.query(Recarga).one()
    r = c.get(f"/recargas/{rec.id}")
    assert "Aún no vemos tu pago" in texto(r.text) and rec.init_point in r.text


def test_regreso_con_pago_rechazado(db, dos, c, mp, tpv):
    _pagar(c, *dos)
    rec = db.query(Recarga).one()
    mp.pagar(rec.id, 150, status="rejected")
    r = c.get(f"/recargas/{rec.id}")
    assert "no se aprobó" in r.text and tpv.cuerpos_recarga == []


def test_recarga_ajena_es_404(db, op, dos, c, mp):
    _pagar(c, *dos)
    rec = db.query(Recarga).one()
    otro = crear_tutor(db, op, telefono="8340000000", debe_cambiar=False)
    rec.tutor_id = otro.id
    db.commit()
    assert c.get(f"/recargas/{rec.id}").status_code == 404
    assert c.get("/recargas/abc").status_code in (404, 422)


def test_mis_recargas(db, dos, c, mp):
    _pagar(c, *dos)
    rec = db.query(Recarga).one()
    r = c.get("/recargas")
    assert f'href="/recargas/{rec.id}"' in r.text and "$150.00" in r.text


def test_cambiar_regresa_con_los_importes(db, dos, c, mp):
    luis, ana = dos
    r = _revisar(c, *dos)
    volver = re.search(r'class="volver" href="([^"]+)"', r.text).group(1).replace("&amp;", "&")
    t = c.get(volver).text
    assert re.search(f'name="importe_{luis["id"]}"[^>]*value="100"', t)
    assert re.search(f'name="importe_{ana["id"]}"[^>]*value="50"', t)
    assert "$150.00" in t and "total · 2 hijos" in t  # la barra ya trae el total


def test_pago_recibido_avisa_que_llegara_notificacion(db, dos, c, mp, monkeypatch):
    _pagar(c, *dos)
    rec = db.query(Recarga).one()
    rec.estado = "revision"  # no se reintenta: se queda como está
    db.commit()
    assert "notificación en tu celular" not in c.get(f"/recargas/{rec.id}").text
    rec.estado = "aprobada"
    db.commit()
    # Sigue «aprobada»: el TPV todavía no acredita.
    monkeypatch.setattr("portal.recargas.reintentar", lambda *a, **k: None)
    t = texto(c.get(f"/recargas/{rec.id}").text)
    assert "Pago recibido" in t and "No vuelvas a pagar" in t
    assert ("En cuanto el pago se confirme recibirás una notificación en tu celular y el saldo "
            "quedará actualizado.") in t


def test_sin_respuesta_de_mp_no_ofrece_pagar_otra_vez(db, dos, c, mp):
    """Si no se puede preguntar a MP, «pendiente» puede ser un pago cuyo aviso no ha llegado: no se
    ofrece pagar de nuevo (doble cobro). Hallazgo de Codex challenge."""
    _pagar(c, *dos)
    rec = db.query(Recarga).one()
    mp.fallar.update({"pago", "buscar"})
    r = c.get(f"/recargas/{rec.id}")
    assert "No pudimos confirmar con Mercado Pago si ya pagaste" in r.text
    assert rec.init_point not in r.text and "Revisar de nuevo" in r.text
    mp.fallar.clear()
    assert rec.init_point in c.get(f"/recargas/{rec.id}").text


def test_hijos_con_el_mismo_nombre_corto_se_distinguen(tutor, tpv, c, mp):
    tpv.agregar_hijo(tutor.tutor_ref, "Juan Pérez López", disponible=10)
    tpv.agregar_hijo(tutor.tutor_ref, "JUAN PÉREZ GARCÍA", disponible=10)  # se ven en mayúsculas
    tpv.agregar_hijo(tutor.tutor_ref, "Ana Pérez López", disponible=10)
    assert entrar_tutor(c, "8341445807", "temporal123").status_code == 303
    for ruta in ("/recargar", "/"):
        t = c.get(ruta).text
        assert "Juan Pérez López" in t and "JUAN PÉREZ GARCÍA" in t
        assert "Ana Pérez López" in t  # con una coincidencia, todos van completos


def test_total_de_la_barra_solo_suma_pesos_enteros(dos, c, mp):
    luis, ana = dos
    for raro in ("1e3", "100.99", "１００"):
        t = c.get(f"/recargar?importe_{luis['id']}={raro}").text
        assert "$0.00" in t and "total · 0 hijos" in t, raro


def test_sin_token_de_mp_no_ofrece_pagar(db, dos, c, mp, op):
    _pagar(c, *dos)
    rec = db.query(Recarga).one()
    op.mp_access_token_cif = None
    db.commit()
    r = c.get(f"/recargas/{rec.id}")
    assert rec.init_point not in r.text and "No pudimos confirmar con Mercado Pago" in r.text


def test_nombres_completos_no_crean_otra_coincidencia():
    from portal.formato import nombres_visibles
    hijos = [{"id": 1, "nombre": "Juan Pérez López"}, {"id": 2, "nombre": "Juan Pérez García"},
             {"id": 3, "nombre": "Juan Pérez López Martínez"}]
    vistos = nombres_visibles(hijos)
    assert len({v.casefold() for v in vistos.values()}) == 3
    assert nombres_visibles([{"id": 1, "nombre": "Sofía Méndez Ruiz"}]) == {1: "Sofía Méndez"}
