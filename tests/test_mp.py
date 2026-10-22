import pytest

from portal.mp import ClienteMP, ErrorMP
from tests.mp_falso import MPFalso


@pytest.fixture
def mp():
    return MPFalso()


def _cliente(mp, token="TEST-token"):
    return ClienteMP(token, transport=mp.transport)


def test_crear_preferencia_con_idempotencia(mp):
    c = _cliente(mp)
    p1 = c.crear_preferencia({"items": []}, idempotencia="L1")
    p2 = c.crear_preferencia({"items": []}, idempotencia="L1")
    assert p1["id"] == p2["id"] and len(mp.preferencias) == 1
    assert p1["init_point"].startswith("https://")


def test_consultar_pago(mp):
    pid = mp.pagar(7, 150)
    pago = _cliente(mp).pago(pid)
    assert pago["status"] == "approved" and pago["external_reference"] == "7"


def test_pago_inexistente_es_404(mp):
    with pytest.raises(ErrorMP) as e:
        _cliente(mp).pago("1")
    assert e.value.status == 404


def test_buscar_por_referencia(mp):
    mp.pagar(7, 150, status="rejected")
    mp.pagar(8, 100)
    assert [p["status"] for p in _cliente(mp).buscar_por_referencia("7")] == ["rejected"]


def test_token_equivocado(mp):
    with pytest.raises(ErrorMP) as e:
        _cliente(mp, "otro").pago("1")
    assert e.value.status == 401


def test_sin_conexion_es_status_0():
    def caer(_req):
        raise httpx.ConnectError("x")
    import httpx
    with pytest.raises(ErrorMP) as e:
        ClienteMP("t", transport=httpx.MockTransport(caer)).pago("1")
    assert e.value.status == 0


def test_id_de_pago_raro_no_sale_del_dominio(mp):
    with pytest.raises(ErrorMP):
        _cliente(mp).pago("../checkout/preferences")


def test_el_token_no_aparece_en_repr():
    assert "secreto" not in repr(ClienteMP("secreto"))
