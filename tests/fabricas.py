import re

from portal.cifrado import cifrar
from portal.models import Admin, Operador, Tutor
from portal.seguridad import hash_contrasena
from tests.conftest import FERNET_PRUEBAS

_n = {"tutor": 0}


def crear_operador(db, host="cafeteria.test", **kw) -> Operador:
    datos = dict(
        nombre="Cafetería de prueba", host=host, tpv_url="https://tpv.test",
        tpv_secreto_cif=cifrar("secreto-tutor", FERNET_PRUEBAS),
        tpv_secreto_admin_cif=cifrar("secreto-admin", FERNET_PRUEBAS),
        conduit_url="https://conduit.test", conduit_client_id="cafeteria",
        conduit_secreto_cif=cifrar("secreto-conduit", FERNET_PRUEBAS),
    )
    datos.update(kw)
    op = Operador(**datos)
    db.add(op)
    db.commit()
    return op


def crear_tutor(db, operador, telefono="8341445807", correo=None, contrasena="temporal123",
                debe_cambiar=True, **kw) -> Tutor:
    _n["tutor"] += 1
    datos = dict(
        operador_id=operador.id, tutor_ref=f"T{_n['tutor']}", nombre="Ana López",
        telefono=telefono, correo=correo, contrasena_hash=hash_contrasena(contrasena),
        debe_cambiar=debe_cambiar,
    )
    datos.update(kw)
    t = Tutor(**datos)
    db.add(t)
    db.commit()
    return t


def crear_admin(db, operador, usuario="caja", contrasena="admin1234") -> Admin:
    a = Admin(operador_id=operador.id, usuario=usuario, nombre="Caja",
              contrasena_hash=hash_contrasena(contrasena))
    db.add(a)
    db.commit()
    return a


def csrf_de(resp) -> str:
    for tag in re.findall(r"<input[^>]*>", resp.text):
        if 'name="csrf"' in tag:
            return re.search(r'value="([^"]*)"', tag).group(1)
    raise AssertionError("la página no trae el campo csrf")


def entrar_tutor(client, usuario, contrasena):
    """Pide el formulario (cookie doble) y lo envía. Devuelve la respuesta del POST."""
    pagina = client.get("/entrar")
    return client.post("/entrar", data={
        "usuario": usuario, "contrasena": contrasena, "csrf": csrf_de(pagina),
    })


def texto(html: str) -> str:
    """Lo que se lee en la página: sin etiquetas y con los espacios juntos (el diseño parte
    nombres y cifras en varios <span>)."""
    import re
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
