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
Sin https en local, pon `PORTAL_COOKIE_SECURE=false` en `.env` (la cookie CSRF pierde el prefijo `__Host-`).
La app queda en `http://localhost:8000`; el operador se resuelve por el `Host` de la petición.

## Pruebas

```bash
docker compose up -d db
.venv/bin/pytest -q
```

## CLI (operador y administradores)

Los secretos (TPV, Conduit, contraseña del admin) se piden con `getpass`, nunca por argumento:

```bash
.venv/bin/python -m portal.cli crear-operador --nombre "Cafetería X" --host cafeteria.softbot.com.mx \
    --tpv-url https://tpv.ejemplo.mx [--conduit-url URL --conduit-client-id ID]
.venv/bin/python -m portal.cli crear-admin --host cafeteria.softbot.com.mx --usuario caja --nombre "Caja"
```

## Límites de intentos

Ventana de 15 min. Tutores: 5 fallos por usuario y 50 por IP (alto por CGNAT y wifi de la escuela).
Administración: contador propio de 20 por IP, para que los papás no puedan bloquear al personal.
Recuperación de contraseña: 3 solicitudes de código por usuario y 30 por IP (no cuentan como fallos
de login); los códigos incorrectos sí cuentan contra la IP.

## Despliegue (P4) — obligatorio

- **uvicorn** detrás de nginx con `--proxy-headers --forwarded-allow-ips=<IP de nginx/bridge>`. Sin esto
  todos los visitantes comparten la IP del proxy y los límites por IP bloquean a todos a la vez.
- **nginx** debe pasar `Host` (el operador se resuelve por host) y `X-Forwarded-For` / `X-Forwarded-Proto`:
  `proxy_set_header Host $host; proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  proxy_set_header X-Forwarded-Proto $scheme;`
- **SMTP**: puerto **587 con STARTTLS**. El 465 (TLS implícito) no está soportado.
- **`PORTAL_INSTANCIA`** única por instalación (p. ej. `cafeteria-colegio-x`): prefija las llaves de
  idempotencia enviadas a Conduit; dos instalaciones con el mismo valor se pisarían los mensajes.
- **`PORTAL_COOKIE_SECURE=true`** (por defecto): activa `Secure` en las cookies y el prefijo `__Host-`
  de la cookie CSRF previa al login.
- La API del TPV va **antes** que el portal.
