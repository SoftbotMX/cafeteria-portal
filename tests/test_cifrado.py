import pytest
from cryptography.fernet import InvalidToken

from portal.cifrado import cifrar, descifrar
from tests.conftest import FERNET_PRUEBAS

OTRA_LLAVE = "Zm9vYmFyYmF6cXV4Zm9vYmFyYmF6cXV4Zm9vYmFyYmE="


def test_cifrado_ida_y_vuelta():
    token = cifrar("secreto-tpv", FERNET_PRUEBAS)
    assert token != "secreto-tpv"
    assert descifrar(token, FERNET_PRUEBAS) == "secreto-tpv"


def test_descifrar_con_otra_llave_falla():
    token = cifrar("secreto-tpv", FERNET_PRUEBAS)
    with pytest.raises(InvalidToken):
        descifrar(token, OTRA_LLAVE)
