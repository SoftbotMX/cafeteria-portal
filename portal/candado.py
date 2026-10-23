"""Candado de PostgreSQL entre procesos que sobrevive a los commits de quien lo tiene.

Vive en una conexión APARTE: las acciones del núcleo hacen commit a medio camino y un
`FOR UPDATE` se soltaría ahí. Quien espera no retiene conexiones del pool (try + soltar entre
intentos), que el dueño necesita para terminar.
"""
import contextlib
import hashlib
import time

from sqlalchemy import Engine, text


class Ocupado(Exception):
    """El candado no se liberó a tiempo."""


def llave(*partes) -> int:
    h = hashlib.sha256(":".join(str(p) for p in partes).encode()).digest()
    return int.from_bytes(h[:8], "big", signed=True)


@contextlib.contextmanager
def tomar(engine: Engine, k: int, espera: float):
    limite = time.monotonic() + espera
    while True:
        con = engine.connect()
        if con.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": k}).scalar():
            break
        con.close()
        if time.monotonic() > limite:
            raise Ocupado
        time.sleep(0.2)
    try:
        yield
    finally:
        try:
            con.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": k})
            con.commit()
        finally:
            con.close()
