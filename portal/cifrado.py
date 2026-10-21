from cryptography.fernet import Fernet


def cifrar(texto: str, llave: str) -> str:
    return Fernet(llave.encode()).encrypt(texto.encode()).decode()


def descifrar(token: str, llave: str) -> str:
    """Lanza cryptography.fernet.InvalidToken si la llave no corresponde."""
    return Fernet(llave.encode()).decrypt(token.encode()).decode()
