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

## Bot de WhatsApp guiado

Conduit (Broker Mode) reenvía lo que escribe el papá a `/api/conduit/incoming/{validate,message}`
(firmado con el `broker_secret`); el portal identifica al tutor por su teléfono y el bot
(`portal/bot.py`) contesta por `/api/broker/reply`. Menú por números: saldo, recargar (total y
reparto por hijo, liga de MP), pedir (por categorías, carrito numerado, *eliminar N*), mis pedidos
(cancelar), transferir (pesos enteros), compras sin pedido, tarjetas, terminar. Las acciones salen
de `portal/acciones.py` (las mismas reglas que la web y el MCP).

- **Número:** +1 956 382 5162 (Meta: cuenta «Cafetería Escolar», app «cafeteria» publicada;
  Conduit: inquilino `cafeteria-escolar`).
- **Textos:** se acuerdan con el usuario antes de cambiarlos. Palabras que el papá escribe en
  `*negritas*`, opciones `*1*) …`, «Escribe el número de tu selección.», confirmaciones con
  «Digita *1* o *2*.», tips en `_itálica_`, alumno por su nombre corto (sin apellidos), montos
  exactos sin centavos (`_sin_centavos`, sólo en el bot).

- `/validate` acepta a todo número (cada rechazo cuenta para el silencio permanente de Conduit).
- Cada `message_id` se atiende una vez (`bot_mensaje`); un papá a la vez (candado de PostgreSQL);
  el paso y la respuesta se guardan en la misma transacción.
- `operador.bot_escrituras` (encendido por omisión) apaga las acciones por WhatsApp de inmediato.
  ⚠️ Conduit aún no verifica la firma de Meta (#39): decisión del usuario dejarlas encendidas.
- Se activa con `conduit_url`, `conduit_client_id` y el secreto de Conduit en el operador.
- **WhatsApp de la cafetería en los correos:** `/admin/cafeteria` («Datos de la cafetería») o
  `python -m portal.cli numero-whatsapp --host <host> --numero "+1 956 382 5162"`. Va con la liga
  `https://wa.me/<número>?text=hola` en el correo de acceso y en el del código de recuperación.
- **Acceso y código de recuperación:** sin plantilla aprobada (`operador.wa_plantillas` apagado) van
  por WhatsApp **y** por correo; el correo es la vía segura para un papá nuevo. Con plantilla, sólo
  WhatsApp (correo si falla). Ver `docs/PLANTILLAS_WHATSAPP.md`.

## Servidor MCP (lenguaje natural)

`https://<host>/mcp` (streamable HTTP, sin estado) para una IA que atienda a los papás. Mismo
núcleo que la web y el bot (`portal/acciones.py`).

- **Token** por operador, `Authorization: Bearer <token>` (en la BD sólo su sha256). Crearlo (se
  muestra una vez y reemplaza al anterior):
  `ssh -t gerri@softbot.com.mx 'cd ~/cafeteria-portal/deploy && docker compose -p cafeteria-portal exec portal python -m portal.cli crear-token-mcp --host cafeteria.softbot.com.mx'`.
- La IA afirma el `telefono` del papá (número mexicano). Consultas: `hijos`, `dias_para_pedir`,
  `menu`, `pedidos_por_recoger`.
- **Acciones en dos pasos impuestos por el servidor**: `preparar_pedido|recarga|transferencia|
  cancelacion|permiso|tarjeta` validan y devuelven resumen + `folio` (vence en 10 min);
  `confirmar(folio)` ejecuta lo preparado, una vez (un incierto se reintenta con su misma llave y
  no vence). El pedido se rechaza si el precio cambió entre preparar y confirmar.
- `operador.bot_escrituras` también apaga las acciones del MCP.

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

## Manual de pruebas

`docs/manual/Cafeteria-Manual-de-Pruebas.docx`: recorrido completo en tres secciones (TPV de la
cafetería, administración, padre en el portal y por WhatsApp), estilo Softbot (plantilla de
Expreso). Se genera con `python3 docs/manual/build_manual.py` a partir de `docs/manual/contenido.py`.
⚠️ Regenerar **sobrescribe** el .docx: si se editó a mano en Word, pasar antes los cambios a
`contenido.py`.
