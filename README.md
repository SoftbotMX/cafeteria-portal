# Portal de tutores — cafetería escolar

Portal web de Softbot para los papás de una cafetería escolar que usa **Softbot TPV**: saldo de sus
hijos, pedidos con fecha, recargas con MercadoPago y alta por la administración de la cafetería.

- Contrato con el TPV: [`docs/API_TPV.md`](docs/API_TPV.md). El portal no guarda saldos.
- Instrucciones para agentes: [`AGENTS.md`](AGENTS.md).

Producción: `https://cafeteria.softbot.com.mx`.

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

## Recargas con MercadoPago (P3)

Checkout Pro. El papá reparte entre sus hijos (pesos enteros, total de $50 a $2,000), paga en
MercadoPago (sólo tarjeta o saldo de MP: sin OXXO ni SPEI) y el TPV acredita con `recarga`.

- **Token del operador** (cifrado en BD; sin él no aparece «Recargar»):
  `ssh -t gerri@softbot.com.mx 'cd ~/cafeteria-portal/deploy && docker compose -p cafeteria-portal exec portal python -m portal.cli guardar-mp --host cafeteria.softbot.com.mx'`.
  Pruebas: el Access Token **de prueba** de la app; producción: el de producción. Siempre se usa
  `init_point` (MP ya no tiene `sandbox_init_point`).
- **Aviso de MP**: cada preferencia lleva `notification_url = https://<host>/mp/aviso`; no hay que
  configurar nada en el panel. El aviso **no se cree**: se consulta el pago en la API de MP.
- **Barrido** (servicio `barrido` del compose, cada minuto): reintenta las pagadas que el TPV no
  tomó, busca pagos sin aviso tras 10 min, vence las pendientes de 24 h y concilia cada recarga
  una vez, 15 min después de que vence su liga de pago.
- **Administración** (`/admin/recargas`): arriba las que la requieren. `revision` = el pago entró
  pero no cuadró o el TPV lo rechazó (p. ej. `pago_ya_acreditado`, que **no** es éxito);
  «Pago extra» = el papá pagó dos veces la misma recarga. Se resuelven en MercadoPago (devolución)
  o con un ajuste en el TPV.

## Producción (P4)

En el VPS (`ssh gerri@softbot.com.mx`), repo clonado en `~/cafeteria-portal` (deploy key de sólo lectura):

```bash
cd ~/cafeteria-portal && git pull
cd deploy && docker compose -p cafeteria-portal up -d --build   # migra al arrancar
```
- Variables en `deploy/.env` (chmod 600), plantilla en `deploy/env.example`. **Respalda aparte
  `PORTAL_FERNET_KEY`**: sin ella los secretos cifrados del operador no se pueden leer.
- nginx: copiar `deploy/nginx.conf` a `/etc/nginx/sites-available/cafeteria.softbot.com.mx.conf`,
  enlazarlo en `sites-enabled`, `sudo nginx -t && sudo systemctl reload nginx`, y después
  `sudo certbot --nginx -d cafeteria.softbot.com.mx --redirect`.
- Respaldo diario de la BD: `deploy/respaldo.sh` en el cron del usuario (14 días en
  `~/respaldos/cafeteria-portal`). Restaurar: `pg_restore -U portal -d portal --clean` dentro del
  contenedor `db`.
- Operador y administradores: `docker compose -p cafeteria-portal exec portal python -m portal.cli …`
  (con `ssh -t`, getpass necesita terminal).

Reglas que el despliegue no puede romper (hay pruebas en `tests/test_despliegue.py`):
- **uvicorn** con `--proxy-headers --forwarded-allow-ips='*'`, publicado sólo en `127.0.0.1`.
- **nginx** pasa `Host` (el operador se resuelve por host) y **sobrescribe** `X-Forwarded-For` con
  `$remote_addr`. Nunca `$proxy_add_x_forwarded_for`: uvicorn toma el primer valor y el papá podría
  mandar uno distinto en cada intento y saltarse los límites por IP.
- **SMTP**: puerto **587 con STARTTLS**. El 465 (TLS implícito) no está soportado.
- **`PORTAL_INSTANCIA`** única por instalación: prefija las llaves de idempotencia enviadas a Conduit.
- **`PORTAL_COOKIE_SECURE=true`** (por defecto).
- La API del TPV va **antes** que el portal.
