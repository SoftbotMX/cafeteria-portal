import re
import secrets
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from portal.alta import DatosAlta, ErrorAlta, dar_de_alta, escribir_tpv, mensaje_tpv, tutor_por_llave
from portal.credenciales import (
    ErrorEntrega, entregar_credenciales, entregas_de, siguiente_intento, tutores_con_entrega, usuario_de,
)
from portal.db import get_db
from portal.models import Admin, Bitacora, Operador, Sesion, Tutor
from portal.rutas.acceso import (
    COOKIE_CSRF_LOGIN, MSG_EXPIRO, MSG_LIMITE, MSG_MALO,
)
from portal.seguridad import (
    HASH_FALSO, ip_cliente, limite_excedido, normalizar_telefono, registrar_intento, verificar_contrasena,
)
from portal.sesiones import (
    COOKIE, DURACION, borrar_cookie, cerrar_sesion, crear_sesion, exigir_csrf, operador_actual,
    poner_cookie, sesion_admin, cerrar_sesiones,
)
from portal.tpv import ErrorTPV, cliente_admin
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
    ip = ip_cliente(request)
    usuario = usuario.strip()
    clave = _clave_intento(usuario)
    if limite_excedido(db, operador.id, clave, ip):
        return _pagina_entrar(request, cookie, 429, MSG_LIMITE, usuario)

    admin = db.scalar(select(Admin).where(
        Admin.operador_id == operador.id, Admin.usuario == usuario))
    ok = verificar_contrasena(admin.contrasena_hash if admin else HASH_FALSO, contrasena)
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
    # Incompleta: ninguna entrega de acceso terminada (alta o reenvío, cualquier intento).
    incompletas = {t.id for t in lista} - tutores_con_entrega(db, admin.operador_id)
    return templates.TemplateResponse(
        request, "admin/tutores.html",
        {"csrf": sesion.csrf, "admin": admin, "tutores": lista, "incompletas": incompletas})


def _filas(nombres: list[str], matriculas: list[str]) -> list[dict]:
    filas = [{"nombre": nombres[i] if i < len(nombres) else "",
              "matricula": matriculas[i] if i < len(matriculas) else ""} for i in range(FILAS_HIJOS)]
    return filas


MSG_BLOQUEADO = ("Reintenta sin cambiar nada. Si algo está mal, termina el alta y corrígelo "
                 "desde la ficha del tutor.")


def _pagina_alta(request: Request, sesion: Sesion, llave: str, datos: dict | None = None,
                 status: int = 200, error: str | None = None, bloqueado: bool = False):
    d = {"nombre": "", "telefono": "", "correo": "", "filas": _filas([], [])}
    d.update(datos or {})
    if bloqueado:
        # El TPV ya recibió estos datos: no se editan; sólo se muestran las filas con hijo.
        d["filas"] = [f for f in d["filas"] if f["nombre"].strip() or f["matricula"].strip()]
    return templates.TemplateResponse(
        request, "admin/alta.html",
        {"csrf": sesion.csrf, "llave": llave, "error": error, "bloqueado": bloqueado,
         "aviso_bloqueo": MSG_BLOQUEADO, **d}, status_code=status)


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
    if previo is not None:
        entregas = entregas_de(db, operador.id, previo.id)
        if entregas:
            # Alta terminada o ya con acceso (alta o reenvío): no se genera otra contraseña.
            marca = entregas[-1]
            if marca.http_status is None:
                envio = "interrumpido: no se sabe si se envió"
            else:
                envio = marca.codigo_error or "enviado"
            return templates.TemplateResponse(
                request, "admin/alta_completa.html",
                {"csrf": sesion.csrf, "tutor": previo, "admin": admin, "envio": envio})

    bloq = previo is not None  # el TPV ya conoce los datos de este alta
    datos, error = _validar(nombre, telefono, correo, hijo_nombre, hijo_matricula)
    if error:
        return _pagina_alta(request, sesion, llave, ctx, 400, error, bloq)
    datos.llave = llave

    actor = f"admin:{admin.usuario}"[:50]
    cliente = cliente_admin(request.app, operador, request.app.state.settings.fernet_key)
    try:
        tutor = dar_de_alta(db, operador, cliente, actor, datos)
    except ErrorAlta as e:
        db.commit()  # lo ya hecho en el TPV y la bitácora son verdad; el reintento lo retoma
        existe = tutor_por_llave(db, operador.id, llave) is not None
        return _pagina_alta(request, sesion, llave, ctx, 400, str(e), existe)

    try:
        res = entregar_credenciales(db, request.app, operador, tutor, 1, actor)
    except ErrorEntrega as e:
        db.commit()  # el tutor y los hijos ya existen en el TPV; no se pierde la fila local
        return _pagina_alta(request, sesion, llave, ctx, 500, str(e), True)
    db.commit()
    resp = templates.TemplateResponse(request, "admin/credenciales.html", {
        "csrf": sesion.csrf, "tutor": tutor, "usuario": usuario_de(tutor), "res": res,
        "hijos": tutor.hijos_creados, "admin": admin, "titulo": "Alta completada"})
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---- ficha del tutor ----

class _SinTPV(Exception):
    """El detalle del TPV no se pudo leer; el texto es para la administración."""


def _alumnos_del_tpv(cliente, tutor_ref: str) -> list[dict]:
    try:
        alumnos = cliente.llamar("admin/tutor/detalle", {"tutor_ref": tutor_ref})["tutor"]["alumnos"]
        if not isinstance(alumnos, list) or not all(
                isinstance(a, dict) and isinstance(a.get("id"), int) for a in alumnos):
            raise TypeError
    except ErrorTPV as e:
        raise _SinTPV(mensaje_tpv(e, "consultar al tutor", True)) from None
    except (KeyError, TypeError):
        raise _SinTPV("El TPV respondió algo inesperado al consultar al tutor.") from None
    return alumnos


def _tutor_o_404(db: Session, admin: Admin, tutor_id: int, bloquear: bool = False) -> Tutor:
    q = select(Tutor).where(Tutor.id == tutor_id, Tutor.operador_id == admin.operador_id)
    tutor = db.scalar(q.with_for_update() if bloquear else q)
    if tutor is None:
        raise HTTPException(404)
    return tutor


def _otro_o_404(db: Session, admin: Admin, tutor: Tutor, otro_id: int) -> Tutor:
    if otro_id == tutor.id:
        raise HTTPException(404)
    otro = _tutor_o_404(db, admin, otro_id)
    if not otro.tutor_ref:
        raise HTTPException(404)
    return otro


def _ficha(request: Request, sesion: Sesion, admin: Admin, operador: Operador, db: Session,
           tutor: Tutor, *, otro: int | None = None, status: int = 200, error: str | None = None,
           mensajes: list[str] | None = None, hijo_form: dict | None = None):
    """Pinta la ficha. Si el TPV falla, la ficha sale igual con las acciones que no lo necesitan."""
    cliente = cliente_admin(request.app, operador, request.app.state.settings.fernet_key)
    hijos, error_tpv = None, None
    if not tutor.tutor_ref:
        error_tpv = MSG_SIN_REF
    else:
        try:
            hijos = _alumnos_del_tpv(cliente, tutor.tutor_ref)
        except _SinTPV:
            error_tpv = "No se pudo consultar el TPV. Vuelve a intentar en un momento."
    otros = db.scalars(select(Tutor).where(
        Tutor.operador_id == operador.id, Tutor.id != tutor.id, Tutor.tutor_ref != "")
        .order_by(Tutor.nombre, Tutor.id)).all()
    entregas = entregas_de(db, operador.id, tutor.id)
    if not entregas:
        ultima_entrega = None
    elif entregas[-1].http_status is None:
        ultima_entrega = "interrumpido: no se sabe si se envió"
    else:
        ultima_entrega = entregas[-1].codigo_error or "enviado"
    otro_tutor, hijos_otro = None, []
    if hijos is not None and otro is not None:
        otro_tutor = next((o for o in otros if o.id == otro), None)
        if otro_tutor is None:
            raise HTTPException(404)
        propios = {a["id"] for a in hijos}
        try:
            hijos_otro = [a for a in _alumnos_del_tpv(cliente, otro_tutor.tutor_ref)
                          if a["id"] not in propios]
        except _SinTPV:
            error = error or "No se pudo consultar el TPV. Vuelve a intentar en un momento."
    resp = templates.TemplateResponse(request, "admin/tutor.html", {
        "csrf": sesion.csrf, "admin": admin, "tutor": tutor, "usuario": usuario_de(tutor),
        "hijos": hijos, "error_tpv": error_tpv, "error": error, "mensajes": mensajes or [],
        "hijo_form": hijo_form or {"llave": "", "nombre": "", "matricula": ""},
        "otros": otros, "otro_tutor": otro_tutor, "hijos_otro": hijos_otro,
        "ultima_entrega": ultima_entrega,
        "incompleta": tutor.id not in tutores_con_entrega(db, operador.id),
        "intento": siguiente_intento(db, operador.id, tutor.id),
        "nueva_llave": lambda: str(uuid.uuid4())}, status_code=status)
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _ctx(request, par, operador, db, tutor_id, bloquear=False):
    sesion, admin = par
    return sesion, admin, _tutor_o_404(db, admin, tutor_id, bloquear)


# Un envío vivo dura como mucho el timeout de Conduit (10 s) o del SMTP (15 s); 2 min deja holgura.
ENVIO_EN_CURSO = timedelta(minutes=2)
MSG_SIN_REF = ("El alta de este tutor no está completa en el TPV (no tiene referencia): "
               "no se pueden consultar ni cambiar sus hijos.")
MSG_FORM_CADUCO = "El formulario caducó. Revisa los datos y envíalo de nuevo."


@router.get("/tutores/{tutor_id}")
def ficha(request: Request, tutor_id: int, otro: int | None = None,
          par: tuple[Sesion, Admin] = Depends(sesion_admin),
          operador: Operador = Depends(operador_actual), db: Session = Depends(get_db)):
    sesion, admin, tutor = _ctx(request, par, operador, db, tutor_id)
    return _ficha(request, sesion, admin, operador, db, tutor, otro=otro)


def _escribir(request, db, operador, admin, tutor, operacion, cuerpo, paso):
    """Escribe en el TPV y confirma una vez. Devuelve el mensaje de error o None."""
    cliente = cliente_admin(request.app, operador, request.app.state.settings.fernet_key)
    try:
        escribir_tpv(db, operador, cliente, f"admin:{admin.usuario}"[:50], operacion, cuerpo)
    except ErrorTPV as e:
        db.commit()  # la bitácora del intento fallido es verdad
        return mensaje_tpv(e, paso, True)
    db.commit()
    return None


@router.post("/tutores/{tutor_id}/hijos")
def agregar_hijo(request: Request, tutor_id: int, nombre: str = Form(""), matricula: str = Form(""),
                 llave: str = Form(""), csrf: str = Form(""),
                 par: tuple[Sesion, Admin] = Depends(sesion_admin),
                 operador: Operador = Depends(operador_actual), db: Session = Depends(get_db)):
    sesion, admin, tutor = _ctx(request, par, operador, db, tutor_id)
    exigir_csrf(csrf, sesion.csrf)
    if not tutor.tutor_ref:
        return _ficha(request, sesion, admin, operador, db, tutor, status=400, error=MSG_SIN_REF)
    nombre, matricula = nombre.strip(), matricula.strip()
    if not _LLAVE_OK.match(llave):
        return _ficha(request, sesion, admin, operador, db, tutor, status=400, error=MSG_FORM_CADUCO,
                      hijo_form={"llave": "", "nombre": nombre, "matricula": matricula})
    # Se conservan llave y datos al fallar: si el TPV sí creó al hijo pero la respuesta se perdió,
    # reintentar con la misma llave devuelve el original en vez de crear un duplicado.
    hijo_form = {"llave": llave, "nombre": nombre, "matricula": matricula}
    if not nombre or len(nombre) > 200 or len(matricula) > 50:
        return _ficha(request, sesion, admin, operador, db, tutor, status=400, hijo_form=hijo_form,
                      error="Escribe el nombre del hijo (máximo 200 caracteres) y una matrícula de hasta 50.")
    cuerpo = {"nombre": nombre, "tutores": [tutor.tutor_ref], "llave": llave}
    if matricula:
        cuerpo["matricula"] = matricula
    error = _escribir(request, db, operador, admin, tutor, "admin/alumno/crear", cuerpo,
                      f"crear a {nombre}")
    if error:
        return _ficha(request, sesion, admin, operador, db, tutor, status=400, error=error,
                      hijo_form=hijo_form)
    return _ficha(request, sesion, admin, operador, db, tutor, mensajes=[f"Se agregó a {nombre}."])


@router.post("/tutores/{tutor_id}/vincular")
def vincular(request: Request, tutor_id: int, otro_tutor_id: int = Form(...), alumno_id: int = Form(...),
             llave: str = Form(""), csrf: str = Form(""),
             par: tuple[Sesion, Admin] = Depends(sesion_admin),
             operador: Operador = Depends(operador_actual), db: Session = Depends(get_db)):
    sesion, admin, tutor = _ctx(request, par, operador, db, tutor_id)
    exigir_csrf(csrf, sesion.csrf)
    if not tutor.tutor_ref:
        return _ficha(request, sesion, admin, operador, db, tutor, status=400, error=MSG_SIN_REF)
    otro = _otro_o_404(db, admin, tutor, otro_tutor_id)
    if not _LLAVE_OK.match(llave):
        return _ficha(request, sesion, admin, operador, db, tutor, status=400, error=MSG_FORM_CADUCO)
    cliente = cliente_admin(request.app, operador, request.app.state.settings.fernet_key)
    try:
        # El alumno_id del formulario no se cree: debe ser hijo del otro tutor según el TPV.
        alumnos = _alumnos_del_tpv(cliente, otro.tutor_ref)
    except _SinTPV as e:
        return _ficha(request, sesion, admin, operador, db, tutor, status=400, error=str(e))
    elegido = next((a for a in alumnos if a["id"] == alumno_id), None)
    if elegido is None:
        raise HTTPException(404)
    error = _escribir(request, db, operador, admin, tutor, "admin/vincular",
                      {"tutor_ref": tutor.tutor_ref, "alumno_id": alumno_id, "activo": True,
                       "llave": llave}, "vincular al hijo")
    if error:
        return _ficha(request, sesion, admin, operador, db, tutor, status=400, error=error)
    return _ficha(request, sesion, admin, operador, db, tutor,
                  mensajes=[f"{elegido.get('nombre', 'El hijo')} quedó vinculado a {tutor.nombre}."])


@router.post("/tutores/{tutor_id}/desvincular")
def desvincular(request: Request, tutor_id: int, alumno_id: int = Form(...), llave: str = Form(""),
                csrf: str = Form(""), par: tuple[Sesion, Admin] = Depends(sesion_admin),
                operador: Operador = Depends(operador_actual), db: Session = Depends(get_db)):
    sesion, admin, tutor = _ctx(request, par, operador, db, tutor_id)
    exigir_csrf(csrf, sesion.csrf)
    if not tutor.tutor_ref:
        return _ficha(request, sesion, admin, operador, db, tutor, status=400, error=MSG_SIN_REF)
    if not _LLAVE_OK.match(llave):
        return _ficha(request, sesion, admin, operador, db, tutor, status=400, error=MSG_FORM_CADUCO)
    cliente = cliente_admin(request.app, operador, request.app.state.settings.fernet_key)
    try:
        alumnos = _alumnos_del_tpv(cliente, tutor.tutor_ref)
    except _SinTPV as e:
        return _ficha(request, sesion, admin, operador, db, tutor, status=400, error=str(e))
    elegido = next((a for a in alumnos if a["id"] == alumno_id), None)
    if elegido is None:
        raise HTTPException(404)
    error = _escribir(request, db, operador, admin, tutor, "admin/vincular",
                      {"tutor_ref": tutor.tutor_ref, "alumno_id": alumno_id, "activo": False,
                       "llave": llave}, "desvincular al hijo")
    if error:
        return _ficha(request, sesion, admin, operador, db, tutor, status=400, error=error)
    return _ficha(request, sesion, admin, operador, db, tutor,
                  mensajes=[f"{elegido.get('nombre', 'El hijo')} ya no está vinculado a {tutor.nombre}."])


@router.post("/tutores/{tutor_id}/reenviar")
def reenviar(request: Request, tutor_id: int, intento: int = Form(0), llave: str = Form(""),
             csrf: str = Form(""), par: tuple[Sesion, Admin] = Depends(sesion_admin),
             operador: Operador = Depends(operador_actual), db: Session = Depends(get_db)):
    sesion, admin = par
    # Con candado: dos clics simultáneos se serializan y el segundo ya ve la fila del primero.
    tutor = _tutor_o_404(db, admin, tutor_id, bloquear=True)
    exigir_csrf(csrf, sesion.csrf)
    if not _LLAVE_OK.match(llave):
        return _ficha(request, sesion, admin, operador, db, tutor, status=400, error=MSG_FORM_CADUCO)
    if not tutor.tutor_ref:
        return _ficha(request, sesion, admin, operador, db, tutor, status=400,
                      error="El alta de este tutor no está completa en el TPV: no se generó ninguna contraseña.")
    if not tutor.activo:
        return _ficha(request, sesion, admin, operador, db, tutor, status=400,
                      error="El tutor está desactivado. Actívalo antes de reenviar su acceso.")
    previas = entregas_de(db, operador.id, tutor.id)
    # La comparación va en SQL: el reloj es el de la BD, el mismo que puso `creado`.
    if previas and previas[-1].http_status is None and db.scalar(select(exists().where(
            Bitacora.id == previas[-1].id, Bitacora.creado > func.now() - ENVIO_EN_CURSO))):
        # El candado se suelta al confirmar la contraseña, antes de enviar: otro reenvío ahora
        # invalidaría la que aún está saliendo.
        db.rollback()
        return _ficha(request, sesion, admin, operador, db, tutor, status=409,
                      error="Hay un envío de acceso en curso para este tutor. Espera un momento "
                            "y revisa la ficha antes de generar otra contraseña.")
    actual = len(previas) + 1
    if intento != actual:
        db.rollback()
        return _ficha(request, sesion, admin, operador, db, tutor, status=409,
                      error="El acceso de este tutor cambió desde que abriste la ficha (¿otro clic o "
                            "otra persona?). Revisa el estado y vuelve a intentar si hace falta.")
    try:
        res = entregar_credenciales(db, request.app, operador, tutor, actual,
                                    f"admin:{admin.usuario}"[:50], operacion="reenviar_acceso")
    except ErrorEntrega as e:
        db.rollback()
        return _ficha(request, sesion, admin, operador, db, tutor, status=500, error=str(e))
    db.commit()
    resp = templates.TemplateResponse(request, "admin/credenciales.html", {
        "csrf": sesion.csrf, "tutor": tutor, "usuario": usuario_de(tutor), "res": res,
        "hijos": None, "admin": admin, "titulo": "Acceso reenviado"})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.post("/tutores/{tutor_id}/activo")
def activo(request: Request, tutor_id: int, activo: str = Form(""), csrf: str = Form(""),
           par: tuple[Sesion, Admin] = Depends(sesion_admin),
           operador: Operador = Depends(operador_actual), db: Session = Depends(get_db)):
    sesion, admin = par
    tutor = _tutor_o_404(db, admin, tutor_id, bloquear=True)
    exigir_csrf(csrf, sesion.csrf)
    if activo not in ("0", "1"):
        raise HTTPException(400)
    tutor.activo = activo == "1"
    if not tutor.activo:
        cerrar_sesiones(db, "tutor", tutor.id)  # misma transacción: no queda una sesión viva
    db.commit()
    return _ficha(request, sesion, admin, operador, db, tutor,
                  mensajes=["Tutor activado." if tutor.activo else "Tutor desactivado: sus sesiones se cerraron."])
