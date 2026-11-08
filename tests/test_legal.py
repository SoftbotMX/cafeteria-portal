import pytest


@pytest.mark.parametrize("ruta, titulo", [
    ("/privacidad", "Aviso de privacidad"),
    ("/terminos", "Condiciones del servicio"),
    ("/eliminar-datos", "Eliminar mis datos"),
])
def test_paginas_legales_publicas(client, ruta, titulo):
    r = client.get(ruta, follow_redirects=False)  # sin sesión: Meta las revisa sin iniciar sesión
    assert r.status_code == 200
    assert f"<h1>{titulo}</h1>" in r.text
    assert "contacto@softbot.com.mx" in r.text
    assert "Zaragoza 102" in r.text


def test_privacidad_dice_que_no_guarda_tarjetas(client):
    assert "no guardamos el número de la tarjeta ni\n    su código de seguridad" in client.get("/privacidad").text

