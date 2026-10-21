"""Entrega de accesos al tutor: contraseña temporal + envío por WhatsApp o correo."""
from dataclasses import dataclass, field

from cryptography.fernet import InvalidToken
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from portal.conduit import ErrorEnvio, enviar_whatsapp
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


def texto_acceso(operador: Operador, tutor: Tutor, contrasena: str) -> str:
    return (f"Hola {tutor.nombre}. Tu acceso al portal de la cafetería: https://{operador.host} · "
            f"usuario: {usuario_de(tutor)} · contraseña temporal: {contrasena}. "
            "Te pediremos cambiarla al entrar.")


def enviar_al_tutor(db: Session, app, operador: Operador, tutor: Tutor, texto: str,
                    llave: str, asunto: str = "Tu acceso al portal de la cafetería") -> tuple[str | None, str, str]:
    """(canal, estado, detalle). WhatsApp si hay teléfono y Conduit; si no, correo si hay SMTP."""
    settings = app.state.settings
    if tutor.telefono and operador.conduit_url and operador.conduit_client_id \
            and operador.conduit_secreto_cif:
        # Sólo aquí se descifra: un secreto de Conduit roto no debe afectar a quien va por correo.
        try:
            secreto = _secreto_conduit(operador, settings.fernet_key)
        except ErrorEntrega as e:
            return "whatsapp", "error", str(e)
        try:
            estado = enviar_whatsapp(
                operador.conduit_url, operador.conduit_client_id, secreto, tutor.telefono, texto,
                llave, transport=app.state.conduit_transport)
        except ErrorEnvio as e:
            return "whatsapp", "error", str(e)
        return "whatsapp", estado, ("Conduit lo está procesando." if estado == "en_curso"
                                    else "Enviado por WhatsApp.")
    if tutor.correo and settings.smtp_host:
        try:
            enviar_correo(settings, tutor.correo, asunto, texto)
        except ErrorEnvio as e:
            return "correo", "error", str(e)
        return "correo", "enviado", "Enviado por correo."
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
    # que nadie recibió.
    if tutor.telefono:
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
        db, app, operador, tutor, texto_acceso(operador, tutor, contrasena), llave)
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
