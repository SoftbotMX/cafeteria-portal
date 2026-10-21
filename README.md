# Portal de tutores — cafetería escolar

Portal web de Softbot para los papás de una cafetería escolar que usa **Softbot TPV**: saldo de sus
hijos, pedidos con fecha, recargas con MercadoPago y alta por la administración de la cafetería.

- Contrato con el TPV: [`docs/API_TPV.md`](docs/API_TPV.md). El portal no guarda saldos.
- Instrucciones para agentes: [`AGENTS.md`](AGENTS.md).

Producción: `https://cafeteria.softbot.com.mx` (pendiente).

## Desarrollo local

```bash
docker compose up -d db                       # PostgreSQL 16 en el puerto 5433
python3.12 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cp .env.example .env                          # y llena PORTAL_FERNET_KEY (ver el comentario)
.venv/bin/pytest -q                           # crea la BD portal_test si falta
.venv/bin/uvicorn portal.app:create_app --factory --reload
```
Sin https en local, pon `PORTAL_COOKIE_SECURE=false` en `.env`.
