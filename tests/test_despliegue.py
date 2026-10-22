"""Lo del despliegue que, si se pierde, no lo nota ninguna otra prueba: los límites por IP
dependen de que uvicorn y nginx se pongan de acuerdo en la IP del papá."""
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def test_uvicorn_lee_la_ip_real_tras_nginx():
    docker = (RAIZ / "Dockerfile").read_text()
    assert "--proxy-headers" in docker and "--forwarded-allow-ips" in docker
    assert "alembic upgrade head" in docker


def test_nginx_sobrescribe_x_forwarded_for():
    nginx = (RAIZ / "deploy" / "nginx.conf").read_text()
    activas = [l.strip() for l in nginx.splitlines() if not l.strip().startswith("#")]
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in activas
    assert not any("$proxy_add_x_forwarded_for" in l for l in activas), (
        "uvicorn confía en el primer valor: agregar deja al papá elegir su IP")
    assert "proxy_set_header Host $host;" in activas


def test_el_puerto_solo_escucha_en_localhost():
    compose = (RAIZ / "deploy" / "docker-compose.yml").read_text()
    assert '"127.0.0.1:8110:8000"' in compose
