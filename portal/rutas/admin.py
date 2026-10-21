import re
import secrets
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from portal.alta import DatosAlta, ErrorAlta, dar_de_alta, tutor_por_llave
from portal.credenciales import entregar_credenciales, usuario_de
from portal.db import get_db
from portal.models import Admin, Bitacora, Operador, Sesion, Tutor
from portal.rutas.acceso import (
    _HASH_FALSO, _ip, COOKIE_CSRF_LOGIN, MSG_EXPIRO, MSG_LIMITE, MSG_MALO,
)
from portal.seguridad import (
    limite_excedido, normalizar_telefono, registrar_intento, verificar_contrasena,
)
from portal.sesiones import (
    COOKIE, DURACION, borrar_cookie, cerrar_sesion, crear_sesion, exigir_csrf, operador_actual,
    poner_cookie, sesion_admin,
)
from portal.tpv import cliente_admin
from portal.web import templates

router = APIRouter(prefix="/admin")

FILAS_HIJOS = 5
_LLAVE_OK = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def _clave_intento(usuario: str) -> str:
    # Prefijo propio: la clave de un admin nunca coincide con la de un tutor (teléfono/correo).
    return "admin:" + usuario[:100]


def _pagina_entrar(request: Request, csrf: str, status: int = 200, error: str | None = None,
                   usuario: str = ""):
    resp = templates.TemplateResponse(
        request, "admin/entrar.html", {"csrf": csrf, "error": error, "usuario": usuario},
        status_code=status)
    resp.set_cookie(COOKIE_CSRF_LOGIN, csrf, max_age=3600, httponly=True,
                    secure=request.app.state.settings.cookie_secure, samesite="lax", path="/")
    return resp


@router.get("/entrar")
def entrar_form(request: Request, _: Operador = Depends(operador_actual)):
    return _pagina_entrar(request, request.cookies.get(COOKIE_CSRF_LOGIN) or secrets.token_urlsafe(32))


@router.post("/entrar")
def entrar(request: Request, usuario: str = Form(""), contrasena: str = Form(""),
           csrf: str = Form(""), operador: Operador = Depends(operador_actual),
           db: Session = Depends(get_db)):
    cookie = request.cookies.get(COOKIE_CSRF_LOGIN, "")
    try:
        exigir_csrf(csrf, cookie)
    except HTTPException:
        return _pagina_entrar(request, secrets.token_urlsafe(32), 403, MSG_EXPIRO, usuario)
    ip = _ip(request)
    usuario = usuario.strip()
    clave = _clave_intento(usuario)
    if limite_excedido(db, operador.id, clave, ip):
        return _pagina_entrar(request, cookie, 429, MSG_LIMITE, usuario)

    admin = db.scalar(select(Admin).where(
        Admin.operador_id == operador.id, Admin.usuario == usuario))
    ok = verificar_contrasena(admin.contrasena_hash if admin else _HASH_FALSO, contrasena)
    if admin is None or not ok or not admin.activo:
        registrar_intento(db, operador.id, clave, ip, exito=False)
        db.commit()
        return _pagina_entrar(request, cookie, 200, MSG_MALO, usuario)

    registrar_intento(db, operador.id, clave, ip, exito=True)
    viejo = request.cookies.get(COOKIE)
    if viejo:
        cerrar_sesion(db, viejo)
    token = crear_sesion(db, "admin", admin.id, operador.id)
    db.commit()
    resp = RedirectResponse("/admin", status_code=303)
    poner_cookie(resp, token, request.app.state.settings, int(DURACION["admin"].total_seconds()))
    resp.delete_cookie(COOKIE_CSRF_LOGIN, path="/")
    return resp


@router.post("/salir")
def salir(request: Request, csrf: str = Form(""),
          par: tuple[Sesion, Admin] = Depends(sesion_admin), db: Session = Depends(get_db)):
    exigir_csrf(csrf, par[0].csrf)
    cerrar_sesion(db, request.cookies[COOKIE])
    db.commit()
    resp = RedirectResponse("/admin/entrar", status_code=303)
    borrar_cookie(resp)
    return resp


@router.get("")
def tutores(request: Request, par: tuple[Sesion, Admin] = Depends(sesion_admin),
            db: Session = Depends(get_db)):
    sesion, admin = par
    lista = db.scalars(select(Tutor).where(Tutor.operador_id == admin.operador_id)
                       .order_by(Tutor.nombre, Tutor.id)).all()
    return templates.TemplateResponse(
        request, "admin/tutores.html", {"csrf": sesion.csrf, "admin": admin, "tutores": lista})


def _filas(nombres: list[str], matriculas: list[str]) -> list[dict]:
    filas = [{"nombre": nombres[i] if i < len(nombres) else "",
              "matricula": matriculas[i] if i < len(matriculas) else ""} for i in range(FILAS_HIJOS)]
    return filas


def _pagina_alta(request: Request, sesion: Sesion, llave: str, datos: dict | None = None,
                 status: int = 200, error: str | None = None):
    d = {"nombre": "", "telefono": "", "correo": "", "filas": _filas([], [])}
    d.update(datos or {})
    return templates.TemplateResponse(
        request, "admin/alta.html",
        {"csrf": sesion.csrf, "llave": llave, "error": error, **d}, status_code=status)


@router.get("/alta")
def alta_form(request: Request, par: tuple[Sesion, Admin] = Depends(sesion_admin)):
    return _pagina_alta(request, par[0], str(uuid.uuid4()))


def _validar(nombre: str, telefono: str, correo: str, hijo_nombre: list[str],
             hijo_matricula: list[str]) -> tuple[DatosAlta | None, str | None]:
    nombre = nombre.strip()
    if not nombre or len(nombre) > 200:
        return None, "Escribe el nombre del tutor (máximo 200 caracteres)."
    tel = None
    if telefono.strip():
        tel = normalizar_telefono(telefono)
        if tel is None:
            return None, "El teléfono debe tener 10 dígitos."
    mail = correo.strip().lower() or None
    if mail and ("@" not in mail or len(mail) > 255 or any(ch.isspace() for ch in mail)):
        return None, "El correo no es válido."
    if tel is None and mail is None:
        return None, "Escribe un teléfono o un correo: es el usuario con el que entrará."
    hijos = []
    for n in _filas(hijo_nombre, hijo_matricula):
        nom, mat = n["nombre"].strip(), n["matricula"].strip()
        if not nom and not mat:
            continue
        if not nom or len(nom) > 200 or len(mat) > 50:
            return None, "Cada hijo necesita nombre (máximo 200 caracteres) y matrícula de hasta 50."
        hijos.append((nom, mat or None))
    if not hijos:
        return None, "Agrega al menos un hijo."
    return DatosAlta(nombre, tel, mail, hijos, ""), None


def _completo(db: Session, operador_id: int, tutor: Tutor) -> bool:
    return db.scalar(select(Bitacora.id).where(
        Bitacora.operador_id == operador_id, Bitacora.operacion == "entregar_acceso",
        Bitacora.llave == f"acceso:{tutor.id}:1")) is not None


@router.post("/alta")
def alta(request: Request, nombre: str = Form(""), telefono: str = Form(""),
         correo: str = Form(""), hijo_nombre: list[str] = Form([]),
         hijo_matricula: list[str] = Form([]), llave: str = Form(""), csrf: str = Form(""),
         par: tuple[Sesion, Admin] = Depends(sesion_admin),
         operador: Operador = Depends(operador_actual), db: Session = Depends(get_db)):
    sesion, admin = par
    ctx = {"nombre": nombre, "telefono": telefono, "correo": correo,
           "filas": _filas(hijo_nombre, hijo_matricula)}
    try:
        exigir_csrf(csrf, sesion.csrf)
    except HTTPException:
        return _pagina_alta(request, sesion, llave, ctx, 403, MSG_EXPIRO)
    if not _LLAVE_OK.match(llave):
        return _pagina_alta(request, sesion, str(uuid.uuid4()), ctx, 400,
                            "El formulario caducó. Revisa los datos y envíalo de nuevo.")

    previo = tutor_por_llave(db, operador.id, llave, bloquear=True)
    if previo is not None and _completo(db, operador.id, previo):
        # Doble clic o reenvío de un alta terminada: no se genera otra contraseña.
        return templates.TemplateResponse(request, "admin/alta_completa.html",
                                          {"csrf": sesion.csrf, "tutor": previo, "admin": admin})

    datos, error = _validar(nombre, telefono, correo, hijo_nombre, hijo_matricula)
    if error:
        return _pagina_alta(request, sesion, llave, ctx, 400, error)
    datos.llave = llave

    actor = f"admin:{admin.usuario}"[:50]
    cliente = cliente_admin(request.app, operador, request.app.state.settings.fernet_key)
    try:
        tutor = dar_de_alta(db, operador, cliente, actor, datos)
    except ErrorAlta as e:
        db.commit()  # lo ya hecho en el TPV y la bitácora son verdad; el reintento lo retoma
        return _pagina_alta(request, sesion, llave, ctx, 400, str(e))

    res = entregar_credenciales(db, request.app, operador, tutor, 1, actor)
    db.commit()
    resp = templates.TemplateResponse(request, "admin/credenciales.html", {
        "csrf": sesion.csrf, "tutor": tutor, "usuario": usuario_de(tutor), "res": res,
        "hijos": tutor.hijos_creados, "admin": admin})
    resp.headers["Cache-Control"] = "no-store"
    return resp
