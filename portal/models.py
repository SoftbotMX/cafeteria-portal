from datetime import date, datetime, time

from sqlalchemy import (
    Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Time, UniqueConstraint, func, text,
)
from sqlalchemy.orm import Mapped, mapped_column

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
    creado: Mapped[datetime] = _creado()

    def secreto_tpv(self, llave_fernet: str) -> str:
        return descifrar(self.tpv_secreto_cif, llave_fernet)

    def secreto_tpv_admin(self, llave_fernet: str) -> str:
        return descifrar(self.tpv_secreto_admin_cif, llave_fernet)

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
    nombre: Mapped[str] = mapped_column(String(200))
    telefono: Mapped[str | None] = mapped_column(String(20))
    correo: Mapped[str | None] = mapped_column(String(255))
    contrasena_hash: Mapped[str] = mapped_column(String)
    debe_cambiar: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    activo: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    alta_llave: Mapped[str | None] = mapped_column(String(120))
    creado: Mapped[datetime] = _creado()


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
