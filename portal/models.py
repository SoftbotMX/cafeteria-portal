from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Index, Integer, LargeBinary, Numeric, String, Time,
    UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, deferred, mapped_column

from portal.cifrado import descifrar
from portal.db import Base


def _creado() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Operador(Base):
    __tablename__ = "operador"

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(200))
    host: Mapped[str] = mapped_column(String(255), unique=True)
    tpv_url: Mapped[str] = mapped_column(String(500))
    tpv_secreto_cif: Mapped[str] = mapped_column(String)
    tpv_secreto_admin_cif: Mapped[str] = mapped_column(String)
    conduit_url: Mapped[str | None] = mapped_column(String(500))
    conduit_client_id: Mapped[str | None] = mapped_column(String(200))
    conduit_secreto_cif: Mapped[str | None] = mapped_column(String)
    mp_access_token_cif: Mapped[str | None] = mapped_column(String)
    # Acciones por WhatsApp (pedir, recargar, transferir…). Encendidas por decisión del usuario
    # (2026-10-22) aunque Conduit aún no verifica la firma de Meta (#39): sirve para apagarlas ya.
    bot_escrituras: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    # sha256 del token con que la IA llama al servidor MCP (el token sólo lo ve quien lo crea).
    mcp_token_hash: Mapped[str | None] = mapped_column(String(64))
    # Mensajes que inicia el portal por WhatsApp con plantilla aprobada en Meta (fuera de la ventana
    # de 24 h sólo así llegan). Apagado hasta que Meta las apruebe: mientras, texto libre.
    wa_plantillas: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # WhatsApp de la cafetería, sólo dígitos con lada de país (p. ej. 19563825162): va en los correos.
    wa_numero: Mapped[str | None] = mapped_column(String(20))
    # Logo de la cafetería (lo sube la administración en «Datos de la cafetería»). Los bytes sólo
    # se cargan al servir /logo; `logo_version` (huella corta) basta para pintar el <img>.
    logo: Mapped[bytes | None] = deferred(mapped_column(LargeBinary))
    logo_tipo: Mapped[str | None] = mapped_column(String(30))
    logo_version: Mapped[str | None] = mapped_column(String(16))
    creado: Mapped[datetime] = _creado()

    def secreto_tpv(self, llave_fernet: str) -> str:
        return descifrar(self.tpv_secreto_cif, llave_fernet)

    def secreto_tpv_admin(self, llave_fernet: str) -> str:
        return descifrar(self.tpv_secreto_admin_cif, llave_fernet)

    def token_mp(self, llave_fernet: str) -> str | None:
        """Access Token de MercadoPago del operador (sin él no se ofrece recargar)."""
        if not self.mp_access_token_cif:
            return None
        return descifrar(self.mp_access_token_cif, llave_fernet)

    def secreto_conduit(self, llave_fernet: str) -> str | None:
        if not self.conduit_secreto_cif:
            return None
        return descifrar(self.conduit_secreto_cif, llave_fernet)


class Admin(Base):
    __tablename__ = "admin"
    __table_args__ = (UniqueConstraint("operador_id", "usuario", name="uq_admin_operador_usuario"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    operador_id: Mapped[int] = mapped_column(ForeignKey("operador.id"))
    usuario: Mapped[str] = mapped_column(String(100))
    nombre: Mapped[str] = mapped_column(String(200))
    contrasena_hash: Mapped[str] = mapped_column(String)
    activo: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    creado: Mapped[datetime] = _creado()


class Tutor(Base):
    __tablename__ = "tutor"
    __table_args__ = (
        UniqueConstraint("operador_id", "tutor_ref", name="uq_tutor_operador_ref"),
        Index("uq_tutor_operador_telefono", "operador_id", "telefono", unique=True,
              postgresql_where=text("telefono IS NOT NULL")),
        Index("uq_tutor_operador_correo", "operador_id", "correo", unique=True,
              postgresql_where=text("correo IS NOT NULL")),
        Index("uq_tutor_operador_alta_llave", "operador_id", "alta_llave", unique=True,
              postgresql_where=text("alta_llave IS NOT NULL")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    operador_id: Mapped[int] = mapped_column(ForeignKey("operador.id"))
    tutor_ref: Mapped[str] = mapped_column(String(100))
    # `nombre` es el completo (el que recibe el TPV y sale en avisos); las partes son para el
    # saludo y los formularios. Se escriben juntos (portal/nombres.py).
    nombre: Mapped[str] = mapped_column(String(200))
    nombres: Mapped[str | None] = mapped_column(String(100))
    apellido_1: Mapped[str | None] = mapped_column(String(100))
    apellido_2: Mapped[str | None] = mapped_column(String(100))
    telefono: Mapped[str | None] = mapped_column(String(20))
    correo: Mapped[str | None] = mapped_column(String(255))
    contrasena_hash: Mapped[str] = mapped_column(String)
    debe_cambiar: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    activo: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    alta_llave: Mapped[str | None] = mapped_column(String(120))
    creado: Mapped[datetime] = _creado()

    @property
    def nombre_pila(self) -> str:
        """Nombre(s) para el saludo («hola, Laura»)."""
        from portal.nombres import partir
        return self.nombres or partir(self.nombre)[0]


class Sesion(Base):
    __tablename__ = "sesion"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    tipo: Mapped[str] = mapped_column(String(10))  # "admin" | "tutor"
    usuario_id: Mapped[int] = mapped_column(Integer)
    operador_id: Mapped[int] = mapped_column(ForeignKey("operador.id"))
    csrf: Mapped[str] = mapped_column(String(64))
    expira: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    creado: Mapped[datetime] = _creado()


class IntentoLogin(Base):
    __tablename__ = "intento_login"
    __table_args__ = (Index("ix_intento_login_creado", "creado"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    operador_id: Mapped[int] = mapped_column(ForeignKey("operador.id"))
    usuario: Mapped[str] = mapped_column(String(255))
    ip: Mapped[str] = mapped_column(String(64))
    exito: Mapped[bool] = mapped_column(Boolean)
    creado: Mapped[datetime] = _creado()


class CodigoRecuperacion(Base):
    __tablename__ = "codigo_recuperacion"

    id: Mapped[int] = mapped_column(primary_key=True)
    tutor_id: Mapped[int] = mapped_column(ForeignKey("tutor.id"))
    codigo_hash: Mapped[str] = mapped_column(String)
    expira: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    intentos: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    usado: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    creado: Mapped[datetime] = _creado()


class Bitacora(Base):
    __tablename__ = "bitacora"

    id: Mapped[int] = mapped_column(primary_key=True)
    operador_id: Mapped[int] = mapped_column(ForeignKey("operador.id"))
    actor: Mapped[str] = mapped_column(String(50))
    operacion: Mapped[str] = mapped_column(String(100))
    llave: Mapped[str] = mapped_column(String(120))
    http_status: Mapped[int | None] = mapped_column(Integer)
    codigo_error: Mapped[str | None] = mapped_column(String(100))
    creado: Mapped[datetime] = _creado()


class ConfigPedidos(Base):
    __tablename__ = "config_pedidos"

    id: Mapped[int] = mapped_column(primary_key=True)
    operador_id: Mapped[int] = mapped_column(ForeignKey("operador.id"), unique=True)
    hora_limite: Mapped[time | None] = mapped_column(Time)
    # Dígitos de weekday(): lunes=0 ... domingo=6.
    dias_servicio: Mapped[str] = mapped_column(String(7), default="01234", server_default="01234")
    zona_horaria: Mapped[str] = mapped_column(
        String(50), default="America/Mexico_City", server_default="America/Mexico_City")
    dias_anticipacion: Mapped[int] = mapped_column(Integer, default=14, server_default=text("14"))


class DiaSinServicio(Base):
    __tablename__ = "dia_sin_servicio"
    __table_args__ = (UniqueConstraint("operador_id", "fecha", name="uq_dia_sin_servicio"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    operador_id: Mapped[int] = mapped_column(ForeignKey("operador.id"))
    fecha: Mapped[date] = mapped_column(Date)
    motivo: Mapped[str] = mapped_column(String(200), default="", server_default="")


class Recarga(Base):
    """Una recarga pagada con MercadoPago. El reparto y el total salen de aquí al acreditar, nunca
    del aviso de MP. Estados: pendiente · aprobada (pagada, falta el TPV) · acreditada · rechazada ·
    vencida · revision (requiere a la administración)."""
    __tablename__ = "recarga"
    __table_args__ = (
        UniqueConstraint("operador_id", "tutor_id", "llave", name="uq_recarga_llave"),
        # Un mismo pago de MP no acredita dos recargas.
        Index("uq_recarga_operador_pago", "operador_id", "mp_pago_id", unique=True,
              postgresql_where=text("mp_pago_id IS NOT NULL")),
        Index("ix_recarga_estado", "estado"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    operador_id: Mapped[int] = mapped_column(ForeignKey("operador.id"))
    tutor_id: Mapped[int] = mapped_column(ForeignKey("tutor.id"))
    llave: Mapped[str] = mapped_column(String(120))
    total: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    reparto: Mapped[list] = mapped_column(JSONB)  # [{alumno_id, nombre, importe}]
    estado: Mapped[str] = mapped_column(String(12), default="pendiente", server_default="pendiente")
    mp_preferencia_id: Mapped[str | None] = mapped_column(String(100))
    init_point: Mapped[str | None] = mapped_column(String(500))
    liga_vence: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    mp_pago_id: Mapped[str | None] = mapped_column(String(50))
    # Otros pagos aprobados de la MISMA preferencia (el papá pagó dos veces): el dinero entró pero
    # no se acredita solo; la administración lo devuelve o lo acredita a mano.
    pagos_extra: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    error: Mapped[str | None] = mapped_column(String(500))
    # Revisión final de sus pagos en MP, ya vencida la liga: encuentra pagos de más sin aviso.
    conciliada: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    creada: Mapped[datetime] = _creado()
    actualizada: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    acreditada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BotConversacion(Base):
    """En qué paso va el papá en el bot de WhatsApp (`datos["paso"]` + lo elegido)."""
    __tablename__ = "bot_conversacion"
    __table_args__ = (UniqueConstraint("operador_id", "telefono", name="uq_bot_conversacion"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    operador_id: Mapped[int] = mapped_column(ForeignKey("operador.id"))
    telefono: Mapped[str] = mapped_column(String(10))
    datos: Mapped[dict] = mapped_column(JSONB, default=dict)
    actualizada: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class BotMensaje(Base):
    """Cada mensaje de Conduit una sola vez: un `message_id` repetido recibe la misma respuesta y
    no avanza la conversación (si no, el segundo «1» confirmaría lo que el papá no vio)."""
    __tablename__ = "bot_mensaje"
    __table_args__ = (UniqueConstraint("operador_id", "message_id", name="uq_bot_mensaje"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    operador_id: Mapped[int] = mapped_column(ForeignKey("operador.id"))
    message_id: Mapped[str] = mapped_column(String(100))
    respuesta: Mapped[str | None] = mapped_column(String)
    creado: Mapped[datetime] = _creado()


class McpConfirmacion(Base):
    """Una acción preparada por el MCP que espera el «sí» del papá. `confirmar(folio)` la ejecuta
    tal cual se preparó (una sola vez; con un incierto, se reintenta con la MISMA llave)."""
    __tablename__ = "mcp_confirmacion"
    __table_args__ = (UniqueConstraint("folio", name="uq_mcp_confirmacion_folio"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    folio: Mapped[str] = mapped_column(String(32))
    operador_id: Mapped[int] = mapped_column(ForeignKey("operador.id"))
    tutor_id: Mapped[int] = mapped_column(ForeignKey("tutor.id"))
    accion: Mapped[str] = mapped_column(String(20))
    datos: Mapped[dict] = mapped_column(JSONB)
    llave: Mapped[str] = mapped_column(String(64))
    expira: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    estado: Mapped[str] = mapped_column(String(10), default="preparada", server_default="preparada")
    resultado: Mapped[str | None] = mapped_column(String)
    creada: Mapped[datetime] = _creado()
