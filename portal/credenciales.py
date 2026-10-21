"""Entrega de accesos al tutor: contraseña temporal + envío por WhatsApp o correo."""
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from portal.conduit import ErrorEnvio, enviar_whatsapp
from portal.correo import enviar_correo
from portal.models import Bitacora, Operador, Tutor
from portal.seguridad import generar_temporal, hash_contrasena
from portal.sesiones import cerrar_sesiones

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
                    llave: str) -> tuple[str | None, str, str]:
    """(canal, estado, detalle). WhatsApp si hay teléfono y Conduit; si no, correo si hay SMTP."""
    settings = app.state.settings
    secreto = operador.secreto_conduit(settings.fernet_key)
    if tutor.telefono and operador.conduit_url and operador.conduit_client_id and secreto:
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
            enviar_correo(settings, tutor.correo, "Tu acceso al portal de la cafetería", texto)
        except ErrorEnvio as e:
            return "correo", "error", str(e)
        return "correo", "enviado", "Enviado por correo."
    return None, "sin_canal", SIN_CANAL


def entregar_credenciales(db: Session, app, operador: Operador, tutor: Tutor, intento: int,
                          actor: str = "admin") -> ResultadoEntrega:
    contrasena = generar_temporal()
    tutor.contrasena_hash = hash_contrasena(contrasena)
    tutor.debe_cambiar = True
    cerrar_sesiones(db, "tutor", tutor.id)
    llave = f"acceso:{tutor.id}:{intento}"
    marca = Bitacora(operador_id=operador.id, actor=actor[:50], operacion="entregar_acceso", llave=llave)
    db.add(marca)
    # Se confirma ANTES de enviar: si el commit fallara después del envío, el tutor tendría una
    # contraseña que no existe en la BD. Así lo enviado y lo mostrado siempre es lo guardado.
    db.commit()
    canal, estado, detalle = enviar_al_tutor(
        db, app, operador, tutor, texto_acceso(operador, tutor, contrasena), llave)
    if estado not in ("enviado", "en_curso"):
        marca.codigo_error = estado
    db.flush()  # la ruta confirma al final
    return ResultadoEntrega(contrasena, canal, estado, detalle)
