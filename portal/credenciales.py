"""Entrega de accesos al tutor: contraseña temporal + envío por WhatsApp o correo."""
from dataclasses import dataclass, field

from cryptography.fernet import InvalidToken
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from portal.conduit import ErrorEnvio, enviar_whatsapp, plantilla as plantilla_wa
from portal.correo import enviar_correo
from portal.models import Bitacora, CodigoRecuperacion, Operador, Tutor
from portal.seguridad import generar_temporal, hash_contrasena, invalidar_codigos
from portal.sesiones import cerrar_sesiones

class ErrorEntrega(Exception):
    """No se pudo preparar la entrega; no se tocó la contraseña del tutor."""


def _secreto_conduit(operador: Operador, llave_fernet: str) -> str | None:
    try:
        return operador.secreto_conduit(llave_fernet)
    except (InvalidToken, ValueError):
        raise ErrorEntrega("No se pudo leer la configuración de WhatsApp (llave de cifrado). "
                           "Avisa a soporte.") from None


SIN_CANAL = "No hay un canal disponible: entrega la contraseña en persona."


@dataclass
class ResultadoEntrega:
    contrasena: str = field(repr=False)  # nunca en logs ni repr
    canal: str | None
    estado: str  # "enviado" | "en_curso" | "error" | "sin_canal"
    detalle: str


def usuario_de(tutor: Tutor) -> str:
    return tutor.telefono or tutor.correo or ""


def normalizar_wa(texto: str) -> str | None:
    """«+1 (956) 382-5162» → «19563825162». None si no es un número con lada de país (sin
    extensiones, sin dígitos no ASCII; el prefijo internacional «00» se quita)."""
    texto = texto.strip()
    if not texto or any(ch not in "0123456789 +-()." for ch in texto):
        return None
    digitos = "".join(ch for ch in texto if ch.isdigit())
    if digitos.startswith("00"):
        digitos = digitos[2:]
    if digitos.startswith("0") or not 11 <= len(digitos) <= 15:
        return None
    return digitos


def numero_visible(digitos: str) -> str:
    """19563825162 → +1 956 382 5162; 528341234567 → +52 834 123 4567."""
    for lada in ("1", "52"):
        if digitos.startswith(lada) and len(digitos) == len(lada) + 10:
            n = digitos[len(lada):]
            return f"+{lada} {n[:3]} {n[3:6]} {n[6:]}"
    return f"+{digitos}"


def enlace_whatsapp(operador: Operador) -> str | None:
    """Liga corta que abre el chat con la cafetería con «hola» ya escrito."""
    return f"https://wa.me/{operador.wa_numero}?text=hola" if operador.wa_numero else None


def texto_acceso(operador: Operador, tutor: Tutor, contrasena: str) -> str:
    texto = (f"Hola {tutor.nombre}.\n\n"
             f"Ya tienes acceso al portal de la cafetería:\nhttps://{operador.host}\n"
             f"Usuario: {usuario_de(tutor)}\nContraseña temporal: {contrasena}\n"
             "Te pediremos cambiarla al entrar.")
    if operador.wa_numero:
        texto += ("\n\nTambién puedes consultar el saldo de tus hijos, hacer pedidos y recargar por "
                  f"WhatsApp. Guarda el número {numero_visible(operador.wa_numero)} en tus contactos y "
                  "escríbele «hola» para empezar, o entra directo con esta liga:\n"
                  f"{enlace_whatsapp(operador)}")
    return texto


def texto_codigo(operador: Operador, codigo: str) -> str:
    texto = (f"Tu código para cambiar tu contraseña del portal de la cafetería es {codigo}. "
             "Vence en 15 minutos.")
    if operador.wa_numero:
        texto += (f"\n\nRecuerda que también puedes escribirnos por WhatsApp al "
                  f"{numero_visible(operador.wa_numero)}:\n{enlace_whatsapp(operador)}")
    return texto


def plantilla_acceso(operador: Operador, tutor: Tutor, contrasena: str) -> dict:
    return plantilla_wa("acceso_portal", [tutor.nombre, f"https://{operador.host}",
                                          usuario_de(tutor), contrasena])


def plantilla_codigo(codigo: str) -> dict:
    """Categoría Autenticación: el código va en el cuerpo y en el botón «Copiar código»."""
    return plantilla_wa("codigo_verificacion", [codigo], [
        {"type": "button", "sub_type": "url", "index": "0",
         "parameters": [{"type": "text", "text": codigo}]}])


def _por_correo(settings, tutor: Tutor, asunto: str, texto: str) -> tuple[str, str]:
    try:
        enviar_correo(settings, tutor.correo, asunto, texto)
    except ErrorEnvio as e:
        return "error", str(e)
    return "enviado", "Enviado por correo."


def enviar_al_tutor(db: Session, app, operador: Operador, tutor: Tutor, texto: str,
                    llave: str, asunto: str = "Tu acceso al portal de la cafetería",
                    plantilla: dict | None = None) -> tuple[str | None, str, str]:
    """(canal, estado, detalle). WhatsApp si hay teléfono y Conduit; correo si hay SMTP.

    `plantilla` se usa en WhatsApp sólo si el operador ya tiene sus plantillas aprobadas
    (`wa_plantillas`). SIN plantilla, el WhatsApp va como texto libre y Meta sólo lo entrega si el
    papá escribió en las últimas 24 h (Conduit lo acepta igual): entonces también va por correo,
    si hay. El correo siempre lleva `texto`."""
    settings = app.state.settings
    hay_correo = bool(tutor.correo and settings.smtp_host)
    if tutor.telefono and operador.conduit_url and operador.conduit_client_id \
            and operador.conduit_secreto_cif:
        con_plantilla = bool(plantilla and operador.wa_plantillas)
        # Sólo aquí se descifra: un secreto de Conduit roto no debe afectar a quien va por correo.
        try:
            secreto = _secreto_conduit(operador, settings.fernet_key)
            estado = enviar_whatsapp(
                operador.conduit_url, operador.conduit_client_id, secreto, tutor.telefono, texto,
                f"{settings.instancia}:{llave}", transport=app.state.conduit_transport,
                plantilla=plantilla if con_plantilla else None)
            detalle = ("Conduit lo está procesando." if estado == "en_curso"
                       else "Enviado por WhatsApp.")
        except (ErrorEntrega, ErrorEnvio) as e:
            estado, detalle = "error", str(e)
        # Con plantilla aprobada el WhatsApp llega solo; el correo sólo hace falta si falló.
        if not hay_correo or (con_plantilla and estado != "error"):
            return "whatsapp", estado, detalle
        c_estado, c_detalle = _por_correo(settings, tutor, asunto, texto)
        if c_estado != "enviado":
            return "whatsapp", estado, f"{detalle} El correo falló: {c_detalle}"
        if estado == "error":
            return "correo", "enviado", f"Enviado por correo (WhatsApp falló: {detalle})"
        if estado == "en_curso":
            return "whatsapp y correo", "enviado", "Enviado por correo; WhatsApp en proceso."
        return "whatsapp y correo", "enviado", "Enviado por WhatsApp y por correo."
    if hay_correo:
        c_estado, c_detalle = _por_correo(settings, tutor, asunto, texto)
        return "correo", c_estado, c_detalle
    return None, "sin_canal", SIN_CANAL


OPERACIONES_ENTREGA = ("entregar_acceso", "reenviar_acceso")


def entregas_de(db: Session, operador_id: int, tutor_id: int) -> list[Bitacora]:
    """Filas de entrega de acceso (alta o reenvío) de un tutor, de la más vieja a la más nueva."""
    return list(db.scalars(select(Bitacora).where(
        Bitacora.operador_id == operador_id, Bitacora.operacion.in_(OPERACIONES_ENTREGA),
        Bitacora.llave.like(f"acceso:{tutor_id}:%")).order_by(Bitacora.id)))


def tutores_con_entrega(db: Session, operador_id: int) -> set[int]:
    """Ids de tutores con al menos una entrega terminada (fila con http_status), de cualquier intento."""
    ids = set()
    for llave in db.scalars(select(Bitacora.llave).where(
            Bitacora.operador_id == operador_id, Bitacora.operacion.in_(OPERACIONES_ENTREGA),
            Bitacora.http_status.is_not(None))):
        partes = llave.split(":")
        if len(partes) == 3 and partes[0] == "acceso" and partes[1].isdigit():
            ids.add(int(partes[1]))
    return ids


def siguiente_intento(db: Session, operador_id: int, tutor_id: int) -> int:
    """Número de intento del próximo reenvío: filas previas + 1 (el alta usa el 1)."""
    return len(entregas_de(db, operador_id, tutor_id)) + 1


def entregar_credenciales(db: Session, app, operador: Operador, tutor: Tutor, intento: int,
                          actor: str = "admin", operacion: str = "entregar_acceso") -> ResultadoEntrega:
    # Antes de tocar la contraseña: si el secreto no se puede leer, no queda un acceso «entregado»
    # que nadie recibió. Con correo de respaldo no hace falta: el acceso llega por correo.
    if tutor.telefono and not (tutor.correo and app.state.settings.smtp_host):
        _secreto_conduit(operador, app.state.settings.fernet_key)
    contrasena = generar_temporal()
    tutor.contrasena_hash = hash_contrasena(contrasena)
    tutor.debe_cambiar = True
    cerrar_sesiones(db, "tutor", tutor.id)
    invalidar_codigos(db, tutor.id)
    llave = f"acceso:{tutor.id}:{intento}"
    marca = Bitacora(operador_id=operador.id, actor=actor[:50], operacion=operacion, llave=llave)
    db.add(marca)
    # Se confirma ANTES de enviar: si el commit fallara después del envío, el tutor tendría una
    # contraseña que no existe en la BD. Así lo enviado y lo mostrado siempre es lo guardado.
    db.commit()
    canal, estado, detalle = enviar_al_tutor(
        db, app, operador, tutor, texto_acceso(operador, tutor, contrasena), llave,
        plantilla=plantilla_acceso(operador, tutor, contrasena))
    marca.http_status = 200  # sin esto, el envío quedó interrumpido
    if estado != "enviado":
        marca.codigo_error = estado
    db.flush()  # la ruta confirma al final
    return ResultadoEntrega(contrasena, canal, estado, detalle)


MAX_FALLOS_TUTOR_24H = 10  # intentos fallidos de código por tutor, sumando todos sus códigos


def recuperacion_bloqueada(db: Session, operador_id: int, tutor_id: int) -> bool:
    """Tope por tutor: 10 intentos fallidos en 24 h. Sólo cuentan los códigos creados después del
    último acceso entregado por la administración (alta o reenvío terminado): reenviar el acceso lo
    desbloquea sin migración."""
    ultima = select(func.max(Bitacora.creado)).where(
        Bitacora.operador_id == operador_id, Bitacora.operacion.in_(OPERACIONES_ENTREGA),
        Bitacora.llave.like(f"acceso:{tutor_id}:%"), Bitacora.http_status.is_not(None)
    ).scalar_subquery()
    desde = func.greatest(func.now() - timedelta(hours=24),
                          func.coalesce(ultima, func.now() - timedelta(hours=24)))
    total = db.scalar(select(func.coalesce(func.sum(CodigoRecuperacion.intentos), 0)).where(
        CodigoRecuperacion.tutor_id == tutor_id, CodigoRecuperacion.creado >= desde))
    return total >= MAX_FALLOS_TUTOR_24H
