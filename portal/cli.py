"""Alta de operadores y administradores. Los secretos se piden con getpass, nunca por argumento."""
import argparse
import getpass
import sys

from sqlalchemy import select
from sqlalchemy.orm import Session

from portal.cifrado import cifrar
from portal.config import get_settings
from portal.db import crear_engine
from portal.models import Admin, Operador
from portal.seguridad import hash_contrasena


def _pedir(etiqueta: str) -> str:
    valor = getpass.getpass(f"{etiqueta}: ")
    if not valor:
        sys.exit(f"{etiqueta} no puede ir vacío.")
    return valor


def crear_operador(args, settings) -> None:
    if bool(args.conduit_url) != bool(args.conduit_client_id):
        sys.exit("--conduit-url y --conduit-client-id van juntos.")
    llave = settings.fernet_key
    tpv = _pedir("Secreto del TPV (tutores)")
    tpv_admin = _pedir("Secreto del TPV (administración)")
    conduit = _pedir("Secreto de Conduit") if args.conduit_url else None
    op = Operador(
        nombre=args.nombre, host=args.host, tpv_url=args.tpv_url,
        tpv_secreto_cif=cifrar(tpv, llave),
        tpv_secreto_admin_cif=cifrar(tpv_admin, llave),
        conduit_url=args.conduit_url, conduit_client_id=args.conduit_client_id,
        conduit_secreto_cif=cifrar(conduit, llave) if conduit else None,
    )
    engine = crear_engine(settings.database_url)
    with Session(engine) as db:
        db.add(op)
        db.commit()
        print(f"Operador {op.host} creado (id {op.id}).")
    engine.dispose()


def crear_admin(args, settings) -> None:
    engine = crear_engine(settings.database_url)
    with Session(engine) as db:
        op = db.scalars(select(Operador).where(Operador.host == args.host)).first()
        if op is None:
            sys.exit(f"No existe un operador con host {args.host}.")
        contrasena = _pedir("Contraseña")
        adm = Admin(operador_id=op.id, usuario=args.usuario, nombre=args.nombre,
                    contrasena_hash=hash_contrasena(contrasena))
        db.add(adm)
        db.commit()
        print(f"Administrador {adm.usuario} creado en {op.host}.")
    engine.dispose()


def guardar_mp(args, settings) -> None:
    engine = crear_engine(settings.database_url)
    with Session(engine) as db:
        op = db.scalars(select(Operador).where(Operador.host == args.host)).first()
        if op is None:
            sys.exit(f"No existe un operador con host {args.host}.")
        op.mp_access_token_cif = cifrar(_pedir("Access Token de MercadoPago"), settings.fernet_key)
        db.commit()
        print(f"Token de MercadoPago guardado en {op.host}.")
    engine.dispose()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="python -m portal.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("crear-operador")
    o.add_argument("--nombre", required=True)
    o.add_argument("--host", required=True)
    o.add_argument("--tpv-url", required=True)
    o.add_argument("--conduit-url")
    o.add_argument("--conduit-client-id")
    a = sub.add_parser("crear-admin")
    a.add_argument("--host", required=True)
    a.add_argument("--usuario", required=True)
    a.add_argument("--nombre", required=True)
    m = sub.add_parser("guardar-mp")
    m.add_argument("--host", required=True)
    args = p.parse_args(argv)
    settings = get_settings()
    {"crear-operador": crear_operador, "crear-admin": crear_admin,
     "guardar-mp": guardar_mp}[args.cmd](args, settings)


if __name__ == "__main__":
    main()
