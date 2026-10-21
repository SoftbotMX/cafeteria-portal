import smtplib
import ssl
from email.message import EmailMessage

from portal.conduit import ErrorEnvio
from portal.config import Settings


def enviar_correo(settings: Settings, para: str, asunto: str, texto: str) -> None:
    if not settings.smtp_host:
        raise ErrorEnvio("El correo no está configurado.")
    msg = EmailMessage()
    msg["From"] = settings.smtp_remitente or settings.smtp_usuario or "portal@localhost"
    msg["To"] = para
    msg["Subject"] = asunto
    msg.set_content(texto)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as s:
            s.starttls(context=ssl.create_default_context())
            if settings.smtp_usuario and settings.smtp_contrasena:
                s.login(settings.smtp_usuario, settings.smtp_contrasena)
            s.send_message(msg)
    except (OSError, smtplib.SMTPException) as e:
        raise ErrorEnvio(f"No se pudo enviar el correo ({type(e).__name__}).") from None
