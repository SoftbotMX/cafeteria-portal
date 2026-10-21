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

## Pedidos (P2)

Los papás piden con fecha, cancelan, transfieren saldo, cambian el permiso de comprar sin pedido y
bloquean credenciales. Todo se escribe en el TPV con su `llave`; el portal no guarda saldos.

### Configuración de la administración (`/admin/pedidos`)

- **Hora límite**: se puede pedir y cancelar para HOY sólo antes de esa hora local (a la hora
  exacta ya no). Para fechas futuras no aplica. Sin hora límite no hay corte.
- **Días de servicio**: días de la semana en que hay cafetería (por omisión lunes a viernes).
- **Días sin servicio**: fechas sueltas (feriados, suspensiones) que no se pueden pedir.
- **Zona horaria**: la del plantel; decide qué es «hoy», la hora límite y el día que se muestra en
  los movimientos.
- **Anticipación**: cuántos días hacia adelante se puede pedir (por omisión 14).

Sin configuración guardada el portal no pone hora límite, ofrece todos los días y 14 de anticipación.

### Lista de arranque (go-live)

- [ ] **Guardar la configuración antes del 1-nov** (hora límite, días, zona, anticipación): sin
      ella no hay hora límite y se aceptarían pedidos de hoy a cualquier hora.
- [ ] El `tz` del partner de la compañía en el TPV debe ser **igual** a la zona horaria del portal:
      el TPV decide con su zona qué día es «hoy» al apartar y cancelar; si difieren, un pedido que
      el portal acepta puede rechazarse (o al revés) en las horas de cambio de día.
- [ ] La API del TPV se despliega antes que el portal.

### Pendiente en el TPV (no bloquea el portal; lo resuelve el usuario)

- `alumno/pedidos` devuelve sólo los 50 más recientes por fecha de creación y no pagina: un pedido
  futuro hecho hace mucho puede no aparecer y no se podría cancelar desde el portal.
- `precompra`: capturar el `IntegrityError` de la `llave` única (hoy un doble toque simultáneo
  devuelve una página de error de Odoo) y responder con el replay del pedido ya creado.
- Códigos de error propios: el TPV responde `409 rechazado` a todo `UserError` (saldo
  insuficiente sólo se distingue por el texto).
