from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher()


def hash_contrasena(c: str) -> str:
    return _hasher.hash(c)


def verificar_contrasena(hash: str, c: str) -> bool:
    try:
        return _hasher.verify(hash, c)
    except (VerifyMismatchError, InvalidHashError):
        return False
