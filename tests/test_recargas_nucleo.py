"""El núcleo de las recargas: crear la preferencia y acreditar SÓLO lo que MercadoPago confirma."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from portal import recargas
from portal.cifrado import cifrar
from portal.mp import ErrorMP
from portal.models import Recarga
from tests.conftest import FERNET_PRUEBAS
from tests.fabricas import crear_operador, crear_tutor
from tests.mp_falso import MPFalso
from tests.tpv_falso import TPVFalso

BASE = "https://cafeteria.test"


@pytest.fixture
def op(db):
    return crear_operador(db, mp_access_token_cif=cifrar("TEST-token", FERNET_PRUEBAS))


@pytest.fixture
def tpv(app):
    f = TPVFalso(secreto_tutor="secreto-tutor")
    app.state.tpv_transport = f.transport
    return f


@pytest.fixture
def mp(app):
    f = MPFalso()
    app.state.mp_transport = f.transport
    return f


@pytest.fixture
def tutor(db, op):
    return crear_tutor(db, op, debe_cambiar=False)


@pytest.fixture
def hijos(tutor, tpv):
    return (tpv.agregar_hijo(tutor.tutor_ref, "Luis", disponible=10),
            tpv.agregar_hijo(tutor.tutor_ref, "Ana", disponible=0))


def _crear(db, app, op, tutor, hijos, luis=100, ana=50, llave="llave-0001"):
    reparto = [{"alumno_id": h["id"], "nombre": h["nombre"], "importe": i}
               for h, i in zip(hijos, (luis, ana)) if i]
    return recargas.crear(db, app, op, tutor, reparto, Decimal(luis + ana), llave, BASE)


# -- armar el reparto ---------------------------------------------------------
def _armar(hijos, **importes):
    return recargas.armar_reparto(hijos, {f"importe_{h['id']}": importes.get(h["nombre"], "")
                                          for h in hijos})


def test_armar_reparto(hijos):
    reparto, total = _armar(hijos, Luis="100", Ana="0")
    assert total == Decimal(100)
    assert reparto == [{"alumno_id": hijos[0]["id"], "nombre": "Luis", "importe": 100}]


@pytest.mark.parametrize("luis,ana", [("49", ""), ("2000", "1"), ("10.5", "50"), ("-5", "100"),
                                      ("abc", "60"), ("", ""), ("0", "0"), ("1e3", "")])
def test_armar_reparto_rechaza(hijos, luis, ana):
    assert isinstance(_armar(hijos, Luis=luis, Ana=ana), str)


def test_armar_reparto_limites_exactos(hijos):
    assert _armar(hijos, Luis="50")[1] == Decimal(50)
    assert _armar(hijos, Luis="1500", Ana="500")[1] == Decimal(2000)


# -- crear --------------------------------------------------------------------
def test_crear_preferencia_sin_oxxo_y_binaria(db, app, op, tutor, hijos, mp):
    r = _crear(db, app, op, tutor, hijos)
    assert r.estado == "pendiente" and r.total == Decimal(150)
    assert r.init_point.startswith("https://mp.test/"), "nunca sandbox_init_point (ya no existe)"
    (pref,) = mp.preferencias.values()
    assert pref["binary_mode"] is True
    assert {t["id"] for t in pref["payment_methods"]["excluded_payment_types"]} == {
        "ticket", "atm", "bank_transfer"}
    assert pref["external_reference"] == str(r.id)
    assert pref["notification_url"] == f"{BASE}/mp/aviso"
    assert pref["back_urls"]["success"] == f"{BASE}/recargas/{r.id}"
    (item,) = pref["items"]
    assert item["unit_price"] == 150 and item["quantity"] == 1 and item["currency_id"] == "MXN"
    vence = datetime.fromisoformat(pref["expiration_date_to"])
    assert timedelta(minutes=25) < vence - datetime.now(timezone.utc) <= timedelta(minutes=30)


def test_crear_dos_veces_con_la_misma_llave(db, app, op, tutor, hijos, mp):
    r1 = _crear(db, app, op, tutor, hijos)
    r2 = _crear(db, app, op, tutor, hijos)
    assert r1.id == r2.id and len(mp.preferencias) == 1
    assert db.query(Recarga).count() == 1


def test_crear_con_mp_caido_se_puede_reintentar(db, app, op, tutor, hijos, mp):
    mp.fallar.add("preferencia")
    with pytest.raises(ErrorMP):
        _crear(db, app, op, tutor, hijos)
    mp.fallar.clear()
    r = _crear(db, app, op, tutor, hijos)
    assert r.init_point and db.query(Recarga).count() == 1


# -- procesar el pago ---------------------------------------------------------
def test_pago_aprobado_se_acredita(db, app, op, tutor, hijos, mp, tpv):
    r = _crear(db, app, op, tutor, hijos)
    pid = mp.pagar(r.id, 150)
    assert recargas.procesar_pago(db, app, op, pid).estado == "acreditada"
    db.commit()
    (cuerpo,) = tpv.cuerpos_recarga
    assert cuerpo["tutor_ref"] == tutor.tutor_ref and cuerpo["pago_ref"] == pid
    assert cuerpo["total"] == 150
    assert sorted((p["alumno_id"], p["importe"]) for p in cuerpo["reparto"]) == sorted(
        [(hijos[0]["id"], 100), (hijos[1]["id"], 50)])
    assert hijos[0]["disponible"] == 110 and hijos[1]["disponible"] == 50
    assert r.mp_pago_id == pid and r.acreditada_en is not None


def test_el_mismo_pago_dos_veces_no_vuelve_al_tpv(db, app, op, tutor, hijos, mp, tpv):
    r = _crear(db, app, op, tutor, hijos)
    pid = mp.pagar(r.id, 150)
    recargas.procesar_pago(db, app, op, pid)
    db.commit()
    recargas.procesar_pago(db, app, op, pid)
    db.commit()
    assert len(tpv.cuerpos_recarga) == 1 and hijos[0]["disponible"] == 110


@pytest.mark.parametrize("monto,moneda", [(149, "MXN"), (151, "MXN"), (150, "USD")])
def test_monto_o_moneda_distintos_van_a_revision(db, app, op, tutor, hijos, mp, tpv, monto, moneda):
    r = _crear(db, app, op, tutor, hijos)
    pid = mp.pagar(r.id, monto, moneda=moneda)
    assert recargas.procesar_pago(db, app, op, pid).estado == "revision"
    assert tpv.cuerpos_recarga == [] and r.error


def test_pago_rechazado_no_acredita_y_se_puede_reintentar(db, app, op, tutor, hijos, mp, tpv):
    r = _crear(db, app, op, tutor, hijos)
    malo = mp.pagar(r.id, 150, status="rejected")
    assert recargas.procesar_pago(db, app, op, malo).estado == "pendiente"
    assert tpv.cuerpos_recarga == [] and r.error
    bueno = mp.pagar(r.id, 150)
    assert recargas.procesar_pago(db, app, op, bueno).estado == "acreditada"


def test_pago_de_otro_operador_no_se_toca(db, app, op, tutor, hijos, mp, tpv):
    otro = crear_operador(db, host="otra.test", mp_access_token_cif=cifrar("TEST-token", FERNET_PRUEBAS))
    r = _crear(db, app, op, tutor, hijos)
    pid = mp.pagar(r.id, 150)
    assert recargas.procesar_pago(db, app, otro, pid) is None
    assert r.estado == "pendiente" and tpv.cuerpos_recarga == []


@pytest.mark.parametrize("ref", ["", "abc", "1 OR 1=1", None])
def test_referencia_rara_se_ignora(db, app, op, mp, ref):
    pid = mp.pagar(ref, 150)
    mp.pagos[pid]["external_reference"] = ref
    assert recargas.procesar_pago(db, app, op, pid) is None


def test_pago_inexistente_se_ignora(db, app, op, mp):
    assert recargas.procesar_pago(db, app, op, "12345") is None


def test_tpv_caido_queda_aprobada_y_se_reintenta(db, app, op, tutor, hijos, mp, tpv):
    r = _crear(db, app, op, tutor, hijos)
    pid = mp.pagar(r.id, 150)
    tpv.fallar_en.add("recarga")
    assert recargas.procesar_pago(db, app, op, pid).estado == "aprobada"
    db.commit()
    tpv.fallar_en.clear()
    recargas.acreditar(db, app, op, r)
    assert r.estado == "acreditada" and r.error is None


def test_pago_ya_acreditado_con_otro_reparto_no_es_exito(db, app, op, tutor, hijos, mp, tpv):
    r = _crear(db, app, op, tutor, hijos)
    pid = mp.pagar(r.id, 150)
    tpv.recargas[pid] = "otra huella"
    assert recargas.procesar_pago(db, app, op, pid).estado == "revision"
    assert "otro reparto" in r.error


def test_segundo_pago_aprobado_se_anota_para_la_administracion(db, app, op, tutor, hijos, mp, tpv):
    r = _crear(db, app, op, tutor, hijos)
    recargas.procesar_pago(db, app, op, mp.pagar(r.id, 150))
    db.commit()
    otro = mp.pagar(r.id, 150)
    assert recargas.procesar_pago(db, app, op, otro).estado == "acreditada"
    assert r.pagos_extra == [otro] and len(tpv.cuerpos_recarga) == 1
    recargas.procesar_pago(db, app, op, otro)
    assert r.pagos_extra == [otro]


# -- pendientes ---------------------------------------------------------------
def test_revisar_pendiente_encuentra_el_pago(db, app, op, tutor, hijos, mp, tpv):
    r = _crear(db, app, op, tutor, hijos)
    mp.pagar(r.id, 150, status="rejected")
    mp.pagar(r.id, 150)
    assert recargas.revisar_pendiente(db, app, op, r).estado == "acreditada"


def test_pendiente_vieja_sin_pago_vence(db, app, op, tutor, hijos, mp):
    r = _crear(db, app, op, tutor, hijos)
    r.creada = datetime.now(timezone.utc) - timedelta(hours=25)
    db.commit()
    assert recargas.revisar_pendiente(db, app, op, r).estado == "vencida"


def test_pendiente_reciente_sin_pago_sigue_pendiente(db, app, op, tutor, hijos, mp):
    r = _crear(db, app, op, tutor, hijos)
    assert recargas.revisar_pendiente(db, app, op, r).estado == "pendiente"


def test_dos_caminos_a_la_vez_acreditan_una_vez(engine, db, app, op, tutor, hijos, mp, tpv):
    """Aviso y regreso del papá al mismo tiempo, con un TPV lento: el segundo espera el candado
    de la fila y ya la encuentra acreditada (ni siquiera vuelve a llamar al TPV)."""
    import threading
    import time

    import httpx
    from sqlalchemy.orm import Session

    r = _crear(db, app, op, tutor, hijos)
    pid = mp.pagar(r.id, 150)
    lento = tpv.transport

    def con_espera(req):
        if req.url.path.endswith("/recarga"):
            time.sleep(0.4)
        return lento.handle_request(req)

    app.state.tpv_transport = httpx.MockTransport(con_espera)
    estados = []

    def camino():
        with Session(engine) as s:
            o = s.get(type(op), op.id)
            estados.append(recargas.procesar_pago(s, app, o, pid).estado)
            s.commit()

    hilos = [threading.Thread(target=camino) for _ in range(3)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert estados == ["acreditada"] * 3
    assert len(tpv.cuerpos_recarga) == 1 and hijos[0]["disponible"] == 110


# -- revisión de Codex (challenge P3) --------------------------------------------------------
def test_monto_con_mas_decimales_no_se_redondea(db, app, op, tutor, hijos, mp, tpv):
    r = _crear(db, app, op, tutor, hijos)
    pid = mp.pagar(r.id, 149.999)
    assert recargas.procesar_pago(db, app, op, pid).estado == "revision"
    assert tpv.cuerpos_recarga == []


def test_segundo_pago_no_detiene_el_reintento_del_primero(db, app, op, tutor, hijos, mp, tpv):
    r = _crear(db, app, op, tutor, hijos)
    tpv.fallar_en.add("recarga")
    a = mp.pagar(r.id, 150)
    recargas.procesar_pago(db, app, op, a)
    db.commit()
    b = mp.pagar(r.id, 150)
    assert recargas.procesar_pago(db, app, op, b).estado == "aprobada"
    assert r.pagos_extra == [b]
    db.commit()
    tpv.fallar_en.clear()
    recargas.reintentar(db, app, op, r.id)
    assert r.estado == "acreditada" and r.pagos_extra == [b]
    assert [c["pago_ref"] for c in tpv.cuerpos_recarga] == [a]  # el 503 no llega a registrarse


def test_sin_aviso_se_registran_todos_los_pagos_aprobados(db, app, op, tutor, hijos, mp, tpv):
    r = _crear(db, app, op, tutor, hijos)
    a = mp.pagar(r.id, 150)
    b = mp.pagar(r.id, 150)
    recargas.revisar_pendiente(db, app, op, r)
    assert r.estado == "acreditada" and len(tpv.recargas) == 1
    assert {r.mp_pago_id, *r.pagos_extra} == {a, b}


def test_vencer_no_pisa_una_acreditacion(db, app, op, tutor, hijos, mp, tpv):
    """El barrido decide vencer con la fila tomada: si entretanto se acreditó, no la toca."""
    r = _crear(db, app, op, tutor, hijos)
    r.creada = datetime.now(timezone.utc) - timedelta(hours=25)
    db.commit()
    real = mp.transport.handle_request
    pid = None

    def busca_y_paga(req):
        nonlocal pid
        resp = real(req)
        if req.url.path == "/v1/payments/search" and pid is None:
            # mientras el barrido busca, llega el aviso por otro camino y acredita
            from sqlalchemy.orm import Session
            pid = mp.pagar(r.id, 150)
            with Session(app.state.engine) as s:
                recargas.procesar_pago(s, app, s.get(type(op), op.id), pid)
                s.commit()
        return resp

    import httpx
    app.state.mp_transport = httpx.MockTransport(busca_y_paga)
    recargas.revisar_pendiente(db, app, op, r)
    db.commit()
    db.refresh(r)
    assert r.estado == "acreditada"


def test_doble_envio_simultaneo_no_da_500(engine, db, app, op, tutor, hijos, mp):
    import threading

    from sqlalchemy.orm import Session
    reparto = [{"alumno_id": hijos[0]["id"], "nombre": "Luis", "importe": 100}]
    salidas = []
    barrera = threading.Barrier(3)

    def envio():
        with Session(engine) as s:
            o, t = s.get(type(op), op.id), s.get(type(tutor), tutor.id)
            barrera.wait()
            salidas.append(recargas.crear(s, app, o, t, reparto, Decimal(100), "llave-junta-1",
                                          BASE).id)

    hilos = [threading.Thread(target=envio) for _ in range(3)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert len(salidas) == 3 and len(set(salidas)) == 1
    assert db.query(Recarga).count() == 1


def test_busqueda_recorre_todas_las_paginas(db, app, op, tutor, hijos, mp, tpv):
    r = _crear(db, app, op, tutor, hijos)
    mp.pagar(r.id, 150)  # el aprobado queda al fondo: los intentos rechazados son más recientes
    for _ in range(130):
        mp.pagar(r.id, 150, status="rejected")
    assert recargas.revisar_pendiente(db, app, op, r).estado == "acreditada"
