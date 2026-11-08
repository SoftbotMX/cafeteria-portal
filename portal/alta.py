"""Alta de un tutor y sus hijos: primero en el TPV, luego el tutor local. Reintentable por llave."""
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from portal.models import Operador, Tutor
from portal.tpv import ClienteTPV, ErrorTPV, registrar_escritura


@dataclass
class Hijo:
    nombres: str
    paterno: str
    materno: str | None
    matricula: str | None

    @property
    def nombre(self) -> str:
        return " ".join(x for x in (self.nombres, self.paterno, self.materno) if x)

    def cuerpo(self) -> dict:
        """Para `admin/alumno/crear` del TPV (nombre por partes)."""
        c = {"nombres": self.nombres, "apellido_paterno": self.paterno}
        if self.materno:
            c["apellido_materno"] = self.materno
        if self.matricula:
            c["matricula"] = self.matricula
        return c


MSG_HIJO = ("Cada hijo necesita nombre(s) y apellido 1 (máximo 100 caracteres cada uno); el "
            "apellido 2 es opcional y la matrícula de hasta 50.")


def validar_hijo(nombres: str, paterno: str, materno: str, matricula: str) -> "Hijo | str | None":
    """El hijo, None si la fila viene vacía, o el mensaje de error."""
    nombres, paterno, materno, matricula = (x.strip() for x in (nombres, paterno, materno, matricula))
    if not (nombres or paterno or materno or matricula):
        return None
    if not nombres or not paterno or any(len(x) > 100 for x in (nombres, paterno, materno)) \
            or len(matricula) > 50:
        return MSG_HIJO
    return Hijo(nombres, paterno, materno or None, matricula or None)


class ErrorAlta(Exception):
    """Mensaje para la administración. Lo hecho hasta aquí es verdad y debe confirmarse."""


@dataclass
class DatosAlta:
    nombre: str
    telefono: str | None  # ya normalizado
    correo: str | None  # ya en minúsculas
    hijos: list["Hijo"]
    llave: str


def tutor_por_llave(db: Session, operador_id: int, llave: str, bloquear: bool = False) -> Tutor | None:
    """`bloquear`: SELECT ... FOR UPDATE; dos reintentos simultáneos se serializan hasta el commit."""
    q = select(Tutor).where(Tutor.operador_id == operador_id, Tutor.alta_llave == llave)
    return db.scalar(q.with_for_update() if bloquear else q)


def _choque(db: Session, operador: Operador, datos: DatosAlta, propio: Tutor | None) -> str | None:
    for campo, valor, msg in ((Tutor.telefono, datos.telefono, "Ya hay un tutor con ese teléfono"),
                              (Tutor.correo, datos.correo, "Ya hay un tutor con ese correo")):
        if valor is None:
            continue
        q = select(Tutor.id).where(Tutor.operador_id == operador.id, campo == valor)
        if propio is not None:
            q = q.where(Tutor.id != propio.id)
        if db.scalar(q) is not None:
            return msg
    return None


def escribir_tpv(db, operador, cliente, actor, operacion, cuerpo) -> dict:
    """Llamada de escritura al TPV + fila de bitácora (flush). La ruta confirma."""
    try:
        r = cliente.llamar(operacion, cuerpo)
    except ErrorTPV as e:
        registrar_escritura(db, operador.id, actor, operacion, cuerpo["llave"], e)
        raise
    registrar_escritura(db, operador.id, actor, operacion, cuerpo["llave"], None)
    return r


def mensaje_tpv(e: ErrorTPV, paso: str, en_ficha: bool = False) -> str:
    if e.codigo == "llave_reutilizada":
        if en_ficha:
            return ("Esta solicitud ya se había enviado con otros datos. Recarga la ficha y "
                    "vuelve a capturarla.")
        return ("Los datos cambiaron desde el primer intento. Reintenta con los datos originales "
                "o corrige desde la ficha del tutor.")
    if e.status <= 0 or e.status >= 500:
        return "No se pudo contactar al TPV. Vuelve a intentar en un momento."
    return f"El TPV rechazó la operación ({paso}): {e.mensaje} Revisa los datos y reintenta."


def dar_de_alta(db: Session, operador: Operador, cliente: ClienteTPV, actor: str,
                datos: DatosAlta) -> Tutor:
    """Deja `tutor.hijos_creados` (fichas del TPV, con su `codigo`) en el objeto devuelto.
    Con ErrorAlta la ruta debe confirmar (commit), no revertir: lo registrado es verdad."""
    tutor = tutor_por_llave(db, operador.id, datos.llave, bloquear=True)
    if tutor is not None and (tutor.nombre, tutor.telefono, tutor.correo) != (
            datos.nombre, datos.telefono, datos.correo):
        # Los accesos saldrían al contacto original: un reintento no puede cambiarlo en silencio.
        raise ErrorAlta("Este alta ya se había registrado con otro nombre, teléfono o correo del "
                        "tutor. Reenvía el formulario con los mismos datos del tutor.")
    choque = _choque(db, operador, datos, tutor)
    if choque:
        raise ErrorAlta(choque)

    if tutor is None:
        cuerpo = {"nombre": datos.nombre, "llave": f"{datos.llave}:tutor"}
        if datos.telefono:
            cuerpo["telefono"] = datos.telefono
        if datos.correo:
            cuerpo["correo"] = datos.correo
        try:
            r = escribir_tpv(db, operador, cliente, actor, "admin/tutor/crear", cuerpo)
            ref = r["tutor"]["tutor_ref"]
        except ErrorTPV as e:
            raise ErrorAlta(mensaje_tpv(e, "crear al tutor")) from None
        except (KeyError, TypeError):
            raise ErrorAlta("El TPV respondió algo inesperado al crear al tutor.") from None
        tutor = Tutor(
            operador_id=operador.id, tutor_ref=ref, nombre=datos.nombre, telefono=datos.telefono,
            correo=datos.correo, contrasena_hash="!", debe_cambiar=True, alta_llave=datos.llave)
        try:
            # Savepoint: si falla el insert sólo se deshace éste y la fila de bitácora del TPV queda.
            with db.begin_nested():
                db.add(tutor)
        except IntegrityError:
            if tutor_por_llave(db, operador.id, datos.llave) is not None:
                raise ErrorAlta("Otro alta con esta misma solicitud se está procesando. Espera "
                                "un momento y reintenta.") from None
            raise ErrorAlta(_choque(db, operador, datos, None)
                            or "Ya hay un tutor con ese teléfono o correo") from None

    creados = []
    for i, hijo in enumerate(datos.hijos):
        nombre = hijo.nombre
        cuerpo = {**hijo.cuerpo(), "tutores": [tutor.tutor_ref], "llave": f"{datos.llave}:alumno:{i}"}
        try:
            r = escribir_tpv(db, operador, cliente, actor, "admin/alumno/crear", cuerpo)
            creados.append(r["alumno"])
        except ErrorTPV as e:
            raise ErrorAlta(mensaje_tpv(e, f"crear a {nombre}")) from None
        except (KeyError, TypeError):
            raise ErrorAlta(f"El TPV respondió algo inesperado al crear a {nombre}.") from None
    tutor.hijos_creados = creados
    return tutor
