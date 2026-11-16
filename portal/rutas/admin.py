import hashlib
import io
import re
import secrets
import uuid
from datetime import date, datetime, time, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from PIL import Image
from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from portal.alta import (
    MSG_HIJO, DatosAlta, ErrorAlta, Hijo, dar_de_alta, escribir_tpv, mensaje_tpv, tutor_por_llave,
    validar_hijo)
from portal.credenciales import (
    ErrorEntrega, entregar_credenciales, entregas_de, recuperacion_bloqueada, siguiente_intento,
    tutores_con_entrega, usuario_de,
)
from portal.db import get_db
from portal.models import Admin, Bitacora, ConfigPedidos, DiaSinServicio, Operador, Sesion, Tutor, Recarga
from portal.nombres import juntar, partir
from portal.reglas import ZONAS, ahora_local, cargar_reglas
from portal.rutas.acceso import (
    MSG_EXPIRO, MSG_LIMITE, MSG_MALO,
)
from portal.seguridad import (
    HASH_FALSO, bloquear_limites, ip_cliente, limite_excedido, normalizar_telefono, registrar_intento, verificar_contrasena,
)
from portal.sesiones import (
    COOKIE, DURACION, borrar_cookie, cerrar_sesion, crear_sesion, exigir_csrf, operador_actual,
    poner_cookie, sesion_admin, cerrar_sesiones, borrar_csrf_login, cookie_csrf_login, poner_csrf_login,
)
from portal.tpv import ErrorTPV, cliente_admin
from portal.web import templates

router = APIRouter(prefix="/admin")

# Hijos por alta: se agregan uno por uno (la pantalla no tiene filas fijas); tope de cordura.
MAX_HIJOS = 10
MSG_MAX_HIJOS = f"Máximo {MAX_HIJOS} hijos por alta. Agrega los demás desde la ficha."
_LLAVE_OK = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def _clave_intento(usuario: str) -> str:
    # Prefijo propio: la clave de un admin nunca coincide con la de un tutor (teléfono/correo).
    return "admin:" + usuario[:100]


def _pagina_entrar(request: Request, csrf: str, status: int = 200, error: str | None = None,
                   usuario: str = ""):
    resp = templates.TemplateResponse(
        request, "admin/entrar.html", {"csrf": csrf, "error": error, "usuario": usuario},
        status_code=status)
    poner_csrf_login(resp, csrf, request.app.state.settings)
    return resp


@router.get("/entrar")
def entrar_form(request: Request, _: Operador = Depends(operador_actual)):
    return _pagina_entrar(request, request.cookies.get(cookie_csrf_login(request.app.state.settings)) or secrets.token_urlsafe(32))


@router.post("/entrar")
def entrar(request: Request, usuario: str = Form(""), contrasena: str = Form(""),
           csrf: str = Form(""), operador: Operador = Depends(operador_actual),
           db: Session = Depends(get_db)):
    cookie = request.cookies.get(cookie_csrf_login(request.app.state.settings), "")
    try:
        exigir_csrf(csrf, cookie)
    except HTTPException:
        return _pagina_entrar(request, secrets.token_urlsafe(32), 403, MSG_EXPIRO, usuario)
    ip = ip_cliente(request)
    usuario = usuario.strip()
    clave = _clave_intento(usuario)
    bloquear_limites(db, operador.id, clave)  # contar, verificar y registrar, atómico por usuario
    if limite_excedido(db, operador.id, clave, ip):
        db.rollback()
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
    borrar_csrf_login(resp, request.app.state.settings)
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
        {"csrf": sesion.csrf, "admin": admin, "tutores": lista, "incompletas": incompletas,
         "sin_horario": db.scalars(select(ConfigPedidos.id).where(
             ConfigPedidos.operador_id == admin.operador_id)).first() is None})


def _filas(nombres: list[str], paternos: list[str], maternos: list[str],
           matriculas: list[str]) -> list[dict]:
    def en(lista, i):
        return lista[i] if i < len(lista) else ""
    # Todas las filas que lleguen (el lector de formularios ya limita los campos): ninguna se
    # descarta en silencio; si hay más de MAX_HIJOS con datos, la validación rechaza el alta.
    n = max(len(nombres), len(paternos), len(maternos), len(matriculas))
    return [{"nombres": en(nombres, i), "paterno": en(paternos, i), "materno": en(maternos, i),
             "matricula": en(matriculas, i)} for i in range(n)]


MSG_BLOQUEADO = ("Reintenta sin cambiar nada. Si algo está mal, termina el alta y corrígelo "
                 "desde la ficha del padre de familia.")


def _pagina_alta(request: Request, sesion: Sesion, llave: str, datos: dict | None = None,
                 status: int = 200, error: str | None = None, bloqueado: bool = False):
    d = {"nombre": "", "nombres": "", "apellido_1": "", "apellido_2": "", "telefono": "",
         "correo": "", "filas": []}
    d.update(datos or {})
    # Sólo las filas con hijo: la pantalla las muestra en la lista y deja vacío «Agregar un hijo».
    d["filas"] = [f for f in d["filas"] if any(v.strip() for v in f.values())]
    return templates.TemplateResponse(
        request, "admin/alta.html",
        {"csrf": sesion.csrf, "llave": llave, "error": error, "bloqueado": bloqueado,
         "aviso_bloqueo": MSG_BLOQUEADO, "max_hijos": MAX_HIJOS, **d}, status_code=status)


@router.get("/alta")
def alta_form(request: Request, par: tuple[Sesion, Admin] = Depends(sesion_admin)):
    return _pagina_alta(request, par[0], str(uuid.uuid4()))


MSG_NOMBRE = ("Escribe el nombre del padre de familia: nombre(s) y apellido 1 (máximo 100 "
              "caracteres cada uno); el apellido 2 es opcional.")


def validar_nombre(nombre: str, nombres: str, apellido_1: str, apellido_2: str):
    """(nombre completo, partes) o (None, mensaje). Con las partes vacías se acepta `nombre`
    completo (formularios de antes) y se separa con la regla del TPV."""
    n, a1, a2 = (x.strip() for x in (nombres, apellido_1, apellido_2))
    if not (n or a1 or a2):
        completo = nombre.strip()
        if not completo or len(completo) > 200:
            return None, MSG_NOMBRE
        return completo, partir(completo)
    if not n or not a1 or any(len(x) > 100 for x in (n, a1, a2)) or len(juntar(n, a1, a2)) > 200:
        return None, MSG_NOMBRE
    return juntar(n, a1, a2), (n, a1, a2 or None)


def _validar(nombre: str, telefono: str, correo: str, filas: list[dict], nombres: str = "",
             apellido_1: str = "", apellido_2: str = "") -> tuple[DatosAlta | None, str | None]:
    nombre, partes = validar_nombre(nombre, nombres, apellido_1, apellido_2)
    if nombre is None:
        return None, partes
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
    for f in filas:
        hijo = validar_hijo(f["nombres"], f["paterno"], f["materno"], f["matricula"])
        if hijo is None:
            continue
        if isinstance(hijo, str):
            return None, hijo
        hijos.append(hijo)
    if not hijos:
        return None, "Agrega al menos un hijo."
    if len(hijos) > MAX_HIJOS:
        return None, MSG_MAX_HIJOS
    return DatosAlta(nombre, tel, mail, hijos, "", partes), None


@router.post("/alta")
def alta(request: Request, nombre: str = Form(""), nombres: str = Form(""),
         apellido_1: str = Form(""), apellido_2: str = Form(""), telefono: str = Form(""),
         correo: str = Form(""), hijo_nombres: list[str] = Form([]),
         hijo_paterno: list[str] = Form([]), hijo_materno: list[str] = Form([]),
         hijo_matricula: list[str] = Form([]), llave: str = Form(""), csrf: str = Form(""),
         par: tuple[Sesion, Admin] = Depends(sesion_admin),
         operador: Operador = Depends(operador_actual), db: Session = Depends(get_db)):
    sesion, admin = par
    # Formulario de antes (sólo el nombre completo): se valida tal cual llegó, como siempre, para que
    # el reintento de un alta a medias coincida exacto con lo que ya recibió el TPV (Codex).
    legado = nombre if not (nombres.strip() or apellido_1.strip() or apellido_2.strip()) else ""
    ctx = {"nombre": nombre, "legado": legado.strip(), "nombres": nombres, "apellido_1": apellido_1, "apellido_2": apellido_2,
           "telefono": telefono, "correo": correo,
           "filas": _filas(hijo_nombres, hijo_paterno, hijo_materno, hijo_matricula)}
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
    datos, error = _validar(nombre, telefono, correo, ctx["filas"], nombres, apellido_1, apellido_2)
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
        alumnos = cliente.leer("admin/tutor/detalle", {"tutor_ref": tutor_ref})["tutor"]["alumnos"]
        if not isinstance(alumnos, list) or not all(
                isinstance(a, dict) and isinstance(a.get("id"), int) for a in alumnos):
            raise TypeError
    except ErrorTPV as e:
        raise _SinTPV(mensaje_tpv(e, "consultar al padre de familia", True)) from None
    except (KeyError, TypeError):
        raise _SinTPV("El TPV respondió algo inesperado al consultar al padre de familia.") from None
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
           mensajes: list[str] | None = None, hijo_form: dict | None = None,
           nombre_form: dict | None = None):
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
        "hijo_form": hijo_form or {"llave": "", "nombres": "", "apellido_paterno": "",
                                   "apellido_materno": "", "matricula": ""},
        "nombre_form": nombre_form or {"llave": "", "nombres": tutor.nombres or "",
                                       "apellido_1": tutor.apellido_1 or "",
                                       "apellido_2": tutor.apellido_2 or "",
                                       "antes": version_nombre(tutor)},
        "otros": otros, "otro_tutor": otro_tutor, "hijos_otro": hijos_otro,
        "ultima_entrega": ultima_entrega,
        "recuperacion_bloqueada": recuperacion_bloqueada(db, operador.id, tutor.id),
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
MSG_SIN_REF = ("El alta de este padre de familia no está completa en el TPV (no tiene referencia): "
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
def agregar_hijo(request: Request, tutor_id: int, nombres: str = Form(""),
                 apellido_paterno: str = Form(""), apellido_materno: str = Form(""),
                 matricula: str = Form(""), llave: str = Form(""), csrf: str = Form(""),
                 par: tuple[Sesion, Admin] = Depends(sesion_admin),
                 operador: Operador = Depends(operador_actual), db: Session = Depends(get_db)):
    sesion, admin, tutor = _ctx(request, par, operador, db, tutor_id)
    exigir_csrf(csrf, sesion.csrf)
    if not tutor.tutor_ref:
        return _ficha(request, sesion, admin, operador, db, tutor, status=400, error=MSG_SIN_REF)
    campos = {"nombres": nombres.strip(), "apellido_paterno": apellido_paterno.strip(),
              "apellido_materno": apellido_materno.strip(), "matricula": matricula.strip()}
    if not _LLAVE_OK.match(llave):
        return _ficha(request, sesion, admin, operador, db, tutor, status=400, error=MSG_FORM_CADUCO,
                      hijo_form={"llave": "", **campos})
    # Se conservan llave y datos al fallar: si el TPV sí creó al hijo pero la respuesta se perdió,
    # reintentar con la misma llave devuelve el original en vez de crear un duplicado.
    hijo_form = {"llave": llave, **campos}
    hijo = validar_hijo(nombres, apellido_paterno, apellido_materno, matricula)
    if not isinstance(hijo, Hijo):
        return _ficha(request, sesion, admin, operador, db, tutor, status=400, hijo_form=hijo_form,
                      error=hijo or MSG_HIJO)
    nombre = hijo.nombre
    cuerpo = {**hijo.cuerpo(), "tutores": [tutor.tutor_ref], "llave": llave}
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
                      error="El alta de este padre de familia no está completa en el TPV: no se generó ninguna contraseña.")
    if not tutor.activo:
        return _ficha(request, sesion, admin, operador, db, tutor, status=400,
                      error="El padre de familia está desactivado. Actívalo antes de reenviar su acceso.")
    previas = entregas_de(db, operador.id, tutor.id)
    # La comparación va en SQL: el reloj es el de la BD, el mismo que puso `creado`.
    if previas and previas[-1].http_status is None and db.scalar(select(exists().where(
            Bitacora.id == previas[-1].id, Bitacora.creado > func.now() - ENVIO_EN_CURSO))):
        # El candado se suelta al confirmar la contraseña, antes de enviar: otro reenvío ahora
        # invalidaría la que aún está saliendo.
        db.rollback()
        return _ficha(request, sesion, admin, operador, db, tutor, status=409,
                      error="Hay un envío de acceso en curso para este padre de familia. Espera un momento "
                            "y revisa la ficha antes de generar otra contraseña.")
    actual = len(previas) + 1
    if intento != actual:
        db.rollback()
        return _ficha(request, sesion, admin, operador, db, tutor, status=409,
                      error="El acceso de este padre de familia cambió desde que abriste la ficha (¿otro clic o "
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
                  mensajes=["Padre de familia activado." if tutor.activo else "Padre de familia desactivado: sus sesiones se cerraron."])


# --- Configuración de pedidos -------------------------------------------------------------

MAX_ANTICIPACION = 30
DIAS_SEMANA = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
_HORA = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _pagina_pedidos(request: Request, sesion: Sesion, admin: Admin, db: Session, valores: dict | None = None,
                    error: str | None = None, mensajes: list[str] | None = None, status: int = 200):
    cfg = db.scalars(select(ConfigPedidos).where(ConfigPedidos.operador_id == admin.operador_id)).first()
    if valores is None:
        valores = {
            "hora_limite": cfg.hora_limite.strftime("%H:%M") if cfg and cfg.hora_limite else "",
            "dias": list(cfg.dias_servicio) if cfg else list("01234"),
            "zona_horaria": cfg.zona_horaria if cfg else ZONAS[0],
            "dias_anticipacion": str(cfg.dias_anticipacion if cfg else 14),
        }
    hoy = ahora_local(cargar_reglas(db, admin.operador_id), datetime.now(timezone.utc)).date()
    dias = db.scalars(select(DiaSinServicio).where(DiaSinServicio.operador_id == admin.operador_id)
                      .order_by(DiaSinServicio.fecha)).all()
    resp = templates.TemplateResponse(
        request, "admin/pedidos.html",
        {"csrf": sesion.csrf, "admin": admin, "v": valores, "zonas": ZONAS, "error": error,
         "max_anticipacion": MAX_ANTICIPACION,
         "dias_semana": list(enumerate(DIAS_SEMANA)), "mensajes": mensajes,
         "proximos": [d for d in dias if d.fecha >= hoy],
         "pasados": [d for d in reversed(dias) if d.fecha < hoy]},
        status_code=status)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.get("/pedidos")
def pedidos_form(request: Request, par: tuple[Sesion, Admin] = Depends(sesion_admin),
                 db: Session = Depends(get_db)):
    return _pagina_pedidos(request, par[0], par[1], db)


@router.post("/pedidos")
def pedidos_guardar(request: Request, hora_limite: str = Form(""), dias: list[str] = Form([]),
                    zona_horaria: str = Form(""), dias_anticipacion: str = Form(""),
                    csrf: str = Form(""), par: tuple[Sesion, Admin] = Depends(sesion_admin),
                    db: Session = Depends(get_db)):
    sesion, admin = par
    exigir_csrf(csrf, sesion.csrf)
    hora_limite = hora_limite.strip()
    dias = sorted(set(dias))
    valores = {"hora_limite": hora_limite, "dias": dias, "zona_horaria": zona_horaria,
               "dias_anticipacion": dias_anticipacion.strip()}
    error = None
    m = _HORA.match(hora_limite)
    if hora_limite and not m:
        error = "La hora límite debe ser una hora como 09:30, o déjala vacía si no hay límite."
    elif not dias or any(d not in "0123456" or len(d) != 1 for d in dias):
        error = "Elige al menos un día de servicio."
    elif zona_horaria not in ZONAS:
        error = "Elige una zona horaria de la lista."
    elif not (valores["dias_anticipacion"].isascii() and valores["dias_anticipacion"].isdigit()
              and 1 <= int(valores["dias_anticipacion"]) <= MAX_ANTICIPACION):
        error = f"Los días de anticipación deben ser un número del 1 al {MAX_ANTICIPACION}."
    if error:
        return _pagina_pedidos(request, sesion, admin, db, valores=valores, error=error, status=400)
    cfg = db.scalars(select(ConfigPedidos).where(ConfigPedidos.operador_id == admin.operador_id)
                     .with_for_update()).first()
    if cfg is None:
        cfg = ConfigPedidos(operador_id=admin.operador_id)
        db.add(cfg)
    cfg.hora_limite = time(int(m.group(1)), int(m.group(2))) if m else None
    cfg.dias_servicio = "".join(dias)
    cfg.zona_horaria = zona_horaria
    cfg.dias_anticipacion = int(valores["dias_anticipacion"])
    try:
        db.commit()
    except IntegrityError:  # dos primeros guardados a la vez: gana uno, el otro reintenta
        db.rollback()
        return _pagina_pedidos(request, sesion, admin, db, valores=valores,
                               error="Otra persona guardó al mismo tiempo. Vuelve a intentarlo.", status=409)
    return _pagina_pedidos(request, sesion, admin, db, mensajes=["Guardado."])


@router.post("/pedidos/sin-servicio")
def sin_servicio_agregar(request: Request, fecha: str = Form(""), motivo: str = Form(""),
                         csrf: str = Form(""), par: tuple[Sesion, Admin] = Depends(sesion_admin),
                         db: Session = Depends(get_db)):
    sesion, admin = par
    exigir_csrf(csrf, sesion.csrf)
    try:
        f = date.fromisoformat(fecha.strip())
    except ValueError:
        return _pagina_pedidos(request, sesion, admin, db, status=400,
                               error="Elige una fecha válida para el día sin servicio.")
    motivo = motivo.strip()
    if len(motivo) > 200:
        return _pagina_pedidos(request, sesion, admin, db, status=400,
                               error="El motivo es demasiado largo (máximo 200 letras).")
    ya = db.scalars(select(DiaSinServicio.id).where(
        DiaSinServicio.operador_id == admin.operador_id, DiaSinServicio.fecha == f)).first()
    if ya is not None:
        return _pagina_pedidos(request, sesion, admin, db, status=400,
                               error="Esa fecha ya está en la lista.")
    db.add(DiaSinServicio(operador_id=admin.operador_id, fecha=f, motivo=motivo))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return _pagina_pedidos(request, sesion, admin, db, status=400, error="Esa fecha ya está en la lista.")
    return _pagina_pedidos(request, sesion, admin, db, mensajes=["Día sin servicio agregado."])


@router.post("/pedidos/sin-servicio/{dia_id}/borrar")
def sin_servicio_borrar(request: Request, dia_id: int, csrf: str = Form(""),
                        par: tuple[Sesion, Admin] = Depends(sesion_admin),
                        db: Session = Depends(get_db)):
    sesion, admin = par
    exigir_csrf(csrf, sesion.csrf)
    dia = db.get(DiaSinServicio, dia_id)
    if dia is None or dia.operador_id != admin.operador_id:
        raise HTTPException(404)
    db.delete(dia)
    db.commit()
    return _pagina_pedidos(request, sesion, admin, db, mensajes=["Día sin servicio quitado."])


ESTADOS_RECARGA = ("revision", "aprobada", "pendiente", "acreditada", "vencida", "rechazada")


@router.get("/recargas")
def recargas_lista(request: Request, estado: str = "",
                   par: tuple[Sesion, Admin] = Depends(sesion_admin),
                   db: Session = Depends(get_db)):
    """Las recargas del operador; las que requieren a la administración (`revision`) primero."""
    sesion, admin = par
    zona = cargar_reglas(db, admin.operador_id).zona
    q = (select(Recarga, Tutor).join(Tutor, Tutor.id == Recarga.tutor_id)
         .where(Recarga.operador_id == admin.operador_id))
    if estado in ESTADOS_RECARGA:
        q = q.where(Recarga.estado == estado)
    # Primero lo que requiere a la administración: revisión o pagos de más.
    atender = or_(Recarga.estado == "revision", func.jsonb_array_length(Recarga.pagos_extra) > 0)
    q = q.order_by(~atender, Recarga.id.desc()).limit(200)
    resp = templates.TemplateResponse(request, "admin/recargas.html", {
        "filas": db.execute(q).all(), "estado": estado, "estados": ESTADOS_RECARGA,
        "csrf": sesion.csrf, "zona": zona})
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---- datos de la cafetería ----

def _pagina_cafeteria(request, sesion, admin, operador, numero=None, error=None, mensajes=None,
                      status=200):
    from portal.credenciales import enlace_whatsapp, numero_visible
    guardado = operador.wa_numero or ""
    resp = templates.TemplateResponse(request, "admin/cafeteria.html", {
        "csrf": sesion.csrf, "admin": admin, "error": error, "mensajes": mensajes,
        "numero": numero if numero is not None else (numero_visible(guardado) if guardado else ""),
        "visible": numero_visible(guardado) if guardado else "", "enlace": enlace_whatsapp(operador),
        "logo_version": operador.logo_version, "nombre_cafeteria": operador.nombre,
    }, status_code=status)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.get("/cafeteria")
def cafeteria_form(request: Request, par: tuple[Sesion, Admin] = Depends(sesion_admin),
                   operador: Operador = Depends(operador_actual)):
    return _pagina_cafeteria(request, par[0], par[1], operador)


@router.post("/cafeteria")
def cafeteria_guardar(request: Request, wa_numero: str = Form(""), csrf: str = Form(""),
                      par: tuple[Sesion, Admin] = Depends(sesion_admin),
                      operador: Operador = Depends(operador_actual), db: Session = Depends(get_db)):
    sesion, admin = par
    exigir_csrf(csrf, sesion.csrf)
    from portal.credenciales import normalizar_wa
    texto = wa_numero.strip()
    digitos = normalizar_wa(texto) if texto else None
    if texto and digitos is None:
        return _pagina_cafeteria(request, sesion, admin, operador, numero=texto, status=400,
                                 error="Escribe el número con lada de país, por ejemplo +1 956 382 5162 "
                                       "o +52 834 123 4567.")
    op = db.get(Operador, operador.id)
    op.wa_numero = digitos or None
    db.commit()
    return _pagina_cafeteria(request, sesion, admin, op, mensajes=["Guardado."])


# ---- logo de la cafetería (cada instalación muestra el suyo) ----

LOGO_MAX = 512 * 1024
LOGO_MIN = 64  # menos que esto no es una imagen que se pueda ver
# Sólo mapas de bits, por su contenido (no se cree la extensión ni el tipo que manda el
# navegador). SVG no: puede llevar scripts.
MSG_LOGO = "Sube una imagen PNG, JPG o WebP de hasta 512 KB."
LOGO_LADO_MAX = 2048  # se muestra a 148 px; acota la memoria al decodificar
_IEND = b"\0\0\0\0IEND\xaeB`\x82"
_FORMATOS_LOGO = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def _webp_medidas_ok(datos: bytes) -> bool:
    """Medidas del lienzo WebP leídas del encabezado, sin decodificar (formato RIFF de libwebp)."""
    if len(datos) < 30 or datos[8:12] != b"WEBP":
        return False
    bloque = datos[12:16]
    if bloque == b"VP8X":
        ancho = int.from_bytes(datos[24:27], "little") + 1
        alto = int.from_bytes(datos[27:30], "little") + 1
    elif bloque == b"VP8 ":
        ancho = int.from_bytes(datos[26:28], "little") & 0x3FFF
        alto = int.from_bytes(datos[28:30], "little") & 0x3FFF
    elif bloque == b"VP8L":
        bits = int.from_bytes(datos[21:25], "little")
        ancho, alto = (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    else:
        return False
    return 1 <= ancho <= LOGO_LADO_MAX and 1 <= alto <= LOGO_LADO_MAX


def _tipo_logo(datos: bytes) -> str | None:
    """El tipo si el archivo es una imagen PNG, JPEG o WebP que se decodifica completa. Se decide por
    el contenido (Pillow), no por la extensión ni el tipo que manda el navegador: un archivo truncado,
    corrupto o de otro formato no llega a guardarse y romper el logo en todo el portal."""
    if len(datos) < LOGO_MIN:
        return None
    # PNG: verify() deja de leer al encontrar IEND sin revisar su suma; se exige el cierre completo.
    if datos.startswith(b"\x89PNG") and not datos.endswith(_IEND):
        return None
    # WebP: Pillow reserva el lienzo al abrirlo, antes de que se puedan revisar las medidas.
    if datos[:4] == b"RIFF" and not _webp_medidas_ok(datos):
        return None
    try:
        with Image.open(io.BytesIO(datos), formats=list(_FORMATOS_LOGO)) as img:
            tipo = _FORMATOS_LOGO.get(img.format)
            ancho, alto = img.size
            # Las medidas se leen del encabezado: se revisan antes de decodificar los pixeles.
            if tipo is None or not (1 <= ancho <= LOGO_LADO_MAX and 1 <= alto <= LOGO_LADO_MAX):
                return None
            if getattr(img, "is_animated", False):  # load() sólo revisaría el primer cuadro
                return None
            # verify() revisa el contenedor (en PNG: sumas de cada bloque y que cierre con IEND);
            # load() no lo hace, pero sí decodifica los pixeles. verify() deja la imagen inservible:
            # se vuelve a abrir para load().
            img.verify()
        with Image.open(io.BytesIO(datos), formats=list(_FORMATOS_LOGO)) as img:
            img.load()  # decodifica todo; truncado o corrupto lanza aquí
    except Exception:  # cualquier falla del decodificador = no es una imagen que se pueda mostrar
        return None
    return tipo


@router.post("/cafeteria/logo")
def cafeteria_logo(request: Request, logo: UploadFile = File(...), csrf: str = Form(""),
                         par: tuple[Sesion, Admin] = Depends(sesion_admin),
                         operador: Operador = Depends(operador_actual),
                         db: Session = Depends(get_db)):
    sesion, admin = par
    exigir_csrf(csrf, sesion.csrf)
    # Ruta síncrona (FastAPI la corre en el pool de hilos): leer y guardar en BD no congela el
    # servidor si la fila del operador está ocupada (Codex).
    datos = logo.file.read(LOGO_MAX + 1)
    tipo = _tipo_logo(datos)
    if not datos or len(datos) > LOGO_MAX or tipo is None:
        return _pagina_cafeteria(request, sesion, admin, operador, status=400, error=MSG_LOGO)
    op = db.get(Operador, operador.id)
    op.logo, op.logo_tipo = datos, tipo
    op.logo_version = hashlib.sha256(datos).hexdigest()[:12]
    db.commit()
    request.state.cafeteria = {"nombre": op.nombre, "logo_version": op.logo_version}
    return _pagina_cafeteria(request, sesion, admin, op, mensajes=["Logo guardado."])


@router.post("/cafeteria/logo/quitar")
def cafeteria_logo_quitar(request: Request, csrf: str = Form(""),
                          par: tuple[Sesion, Admin] = Depends(sesion_admin),
                          operador: Operador = Depends(operador_actual),
                          db: Session = Depends(get_db)):
    sesion, admin = par
    exigir_csrf(csrf, sesion.csrf)
    op = db.get(Operador, operador.id)
    op.logo = op.logo_tipo = op.logo_version = None
    db.commit()
    request.state.cafeteria = {"nombre": op.nombre, "logo_version": None}
    return _pagina_cafeteria(request, sesion, admin, op, mensajes=["Logo quitado."])


# ---- corregir el nombre del padre de familia (por partes) ----

def version_nombre(tutor: Tutor) -> str:
    """Huella del nombre tal como lo tiene la ficha. Viaja en el formulario: si al guardar ya no es
    la misma, alguien lo cambió después (otra pestaña, un reenvío viejo) y no se pisa."""
    datos = "\x1f".join(x or "" for x in (tutor.nombre, tutor.nombres, tutor.apellido_1, tutor.apellido_2))
    return hashlib.sha256(datos.encode()).hexdigest()[:16]


MSG_NOMBRE_CAMBIO = "El nombre cambió desde que abriste la ficha. Revísalo y vuelve a corregirlo."


@router.post("/tutores/{tutor_id}/nombre")
def corregir_nombre(request: Request, tutor_id: int, nombres: str = Form(""),
                    apellido_1: str = Form(""), apellido_2: str = Form(""), llave: str = Form(""),
                    antes: str = Form(""), csrf: str = Form(""),
                    par: tuple[Sesion, Admin] = Depends(sesion_admin),
                    operador: Operador = Depends(operador_actual), db: Session = Depends(get_db)):
    # FOR UPDATE hasta el commit: dos correcciones a la vez no se pisan.
    sesion, admin, tutor = _ctx(request, par, operador, db, tutor_id, bloquear=True)
    exigir_csrf(csrf, sesion.csrf)
    # Al fallar se conservan llave, datos y huella: reintentar no escribe dos veces.
    nombre_form = {"llave": llave, "nombres": nombres, "apellido_1": apellido_1,
                   "apellido_2": apellido_2, "antes": antes}
    if not tutor.tutor_ref:
        return _ficha(request, sesion, admin, operador, db, tutor, status=400, error=MSG_SIN_REF)
    if tutor.id not in tutores_con_entrega(db, operador.id):
        # El reintento del alta exige los datos originales: no se cambia el nombre a medias.
        return _ficha(request, sesion, admin, operador, db, tutor, status=400,
                      error="Termina el alta antes de corregir el nombre.")
    if antes != version_nombre(tutor):
        # Página vieja o reenvío tardío: no deshace una corrección posterior (Codex).
        return _ficha(request, sesion, admin, operador, db, tutor, status=409, error=MSG_NOMBRE_CAMBIO)
    if not _LLAVE_OK.match(llave):
        return _ficha(request, sesion, admin, operador, db, tutor, status=400, error=MSG_FORM_CADUCO,
                      nombre_form={**nombre_form, "llave": ""})
    if not nombres.strip() and not apellido_1.strip():
        completo, partes = None, MSG_NOMBRE
    else:
        completo, partes = validar_nombre("", nombres, apellido_1, apellido_2)
    if completo is None:
        return _ficha(request, sesion, admin, operador, db, tutor, status=400, error=partes,
                      nombre_form=nombre_form)
    # Siempre se escribe el nombre completo (si no cambió, el TPV no cambia nada) y se confirma
    # con una lectura: lo que queda en la ficha es lo que el TPV tiene AHORA.
    cliente = cliente_admin(request.app, operador, request.app.state.settings.fernet_key)
    try:
        escribir_tpv(db, operador, cliente, f"admin:{admin.usuario}"[:50], "admin/tutor/editar",
                     {"tutor_ref": tutor.tutor_ref, "nombre": completo, "llave": llave})
    except ErrorTPV as e:
        db.commit()  # la bitácora del intento fallido es verdad
        return _ficha(request, sesion, admin, operador, db, tutor, status=400,
                      error=mensaje_tpv(e, "corregir el nombre", True), nombre_form=nombre_form)
    try:
        vigente = cliente.leer("admin/tutor/detalle", {"tutor_ref": tutor.tutor_ref})["tutor"]["nombre"]
        if not isinstance(vigente, str) or not vigente.strip():
            raise TypeError
    except (ErrorTPV, KeyError, TypeError):
        db.commit()  # la bitácora de la escritura es verdad; la ficha no se toca
        return _ficha(request, sesion, admin, operador, db, tutor, status=400, nombre_form=nombre_form,
                      error="El TPV recibió el cambio, pero no pudimos confirmar el nombre que quedó. "
                            "Vuelve a tocar «Guardar nombre» en un momento (no se repite).")
    vigente = vigente.strip()[:200]
    if vigente == completo:
        aviso = "Nombre corregido."
    else:
        partes = partir(vigente)
        aviso = f"El TPV tiene otro nombre; la ficha quedó como «{vigente}»."
    tutor.nombre = vigente
    tutor.nombres, tutor.apellido_1, tutor.apellido_2 = (
        (partes[0] or "")[:100] or None, partes[1] and partes[1][:100], partes[2] and partes[2][:100])
    db.commit()
    return _ficha(request, sesion, admin, operador, db, tutor, mensajes=[aviso])
