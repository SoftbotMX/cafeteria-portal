# API del TPV para el portal de tutores (cafetería escolar)

Contrato entre el TPV (`addons/softbot_tpv_cafeteria`) y el portal de tutores (repo aparte).
El portal **no guarda saldos**: todo lo lee y lo escribe aquí. Despliegue: **la API del TPV va
antes que el portal**.

## Petición

`POST https://<instancia>/sb_cafe/api/v1/<operación>` · cuerpo JSON · cabeceras:

```
Content-Type: application/json
X-SB-Cafe-Timestamp: <unix, segundos>
X-SB-Cafe-Signature: hmac-sha256=<hex>
```

`firma = HMAC-SHA256(secreto, timestamp + "." + ruta + "." + cuerpo)`, donde `ruta` es el path
completo (`/sb_cafe/api/v1/tutor/alumnos`) y `cuerpo` los bytes exactos enviados.

- Ventana de **300 s**. Cada firma se acepta **una vez**: un reintento se vuelve a firmar.
- **Dos secretos** por instancia (se generan al instalar): `sb_cafe.api_secreto` para las
  operaciones de tutor y `sb_cafe.api_secreto_admin` para las de administración (`admin/...`).
  No se cruzan: con el de tutores no se crean alumnos, con el de administración no se opera como
  tutor.
- Todas las operaciones son POST, también las de lectura (la firma cubre el cuerpo).

## Respuesta

`200 {"ok": true, ...}` o `{"ok": false, "error": {"codigo": "...", "mensaje": "..."}}` con:
`401` firma inválida · `404` tutor/alumno/pedido/credencial desconocido (uno ajeno responde igual
que uno inexistente) · `409` rechazado por el dominio (saldo insuficiente, petición repetida,
pago ya acreditado con otro reparto, llave reutilizada) · `400` datos mal formados · `503` sin
secreto configurado.

## Identidad y acceso

- **El acceso de los papás (usuario y contraseña) vive SÓLO en el portal.** La administración de
  la cafetería da de alta al tutor y a sus hijos en el portal; el portal los crea en el TPV con las
  operaciones `admin/...`, guarda el `tutor_ref` que le devuelve y manda usuario y contraseña al
  papá por correo o WhatsApp (Conduit). El cambio de contraseña al primer ingreso es del portal.
- `tutor_ref` (string) identifica al tutor. El TPV comprueba el **vínculo tutor-alumno en cada
  operación**: un vínculo desactivado corta el acceso de inmediato.
- **Las recargas sólo se hacen en el portal** (la caja no vende saldo). En la caja todo consumo
  va ligado a un alumno, que da su `codigo`; si el saldo no alcanza, el faltante se paga con otro
  método.

## Operaciones

| Operación | Cuerpo | Devuelve |
|---|---|---|
| `tutor/alumnos` | `tutor_ref` | `alumnos: [ficha]` |
| `alumno/movimientos` | `tutor_ref, alumno_id, pagina?, por_pagina? (≤100)` | `total, pagina, movimientos: [{fecha, tipo, descripcion, disponible, retenido, venta}]` |
| `alumno/pedidos` | `tutor_ref, alumno_id` | `pedidos: [pedido]` (los 50 más recientes) |
| `alumno/permiso` | `tutor_ref, alumno_id, compra_sin_pedido (bool), llave` | `alumno` — cualquiera de sus tutores; vale lo último |
| `menu` | — | `productos: [{id, nombre, categoria, precio}]` (precio con impuestos, el que cobra la caja) |
| `precompra` | `tutor_ref, alumno_id, items: [{producto_id, cantidad}], fecha_entrega? (AAAA-MM-DD, hoy por omisión), llave` | `pedido, alumno` |
| `precompra/cancelar` | `tutor_ref, pedido_id, llave` | `pedido, alumno` |
| `transferencia` | `tutor_ref, origen_id, destino_id, importe, llave` | `alumnos` |
| `credencial/bloquear` · `credencial/desbloquear` | `tutor_ref, credencial_id, llave` | `alumno` |
| `recarga` | `tutor_ref, pago_ref, total, reparto: [{alumno_id, importe}]` | `pago_ref, alumnos` |

`ficha` = `{id, nombre, nombres, apellido_paterno, apellido_materno, matricula, codigo, compra_sin_pedido, disponible, retenido, credenciales: [{id, tipo, bloqueada}]}`
(`codigo` es el que el alumno dice en la caja; `compra_sin_pedido`: si está en `false`, con su
saldo sólo se le cobra lo que sus tutores pidieron; nace en `true`).

### Administración (secreto `sb_cafe.api_secreto_admin`)

| Operación | Cuerpo | Devuelve |
|---|---|---|
| `admin/tutor/crear` | `nombre, telefono?, correo?, llave` | `tutor` |
| `admin/tutor/editar` | `tutor_ref, nombre?, telefono?, correo?, llave` | `tutor` |
| `admin/tutor/detalle` | `tutor_ref` | `tutor` |
| `admin/alumno/crear` | `nombres, apellido_paterno, apellido_materno?, matricula?, tutores: [tutor_ref], llave` (`apellido_paterno` = Apellido 1, `apellido_materno` = Apellido 2; o, como antes, `nombre` completo: se separa con las dos últimas palabras como apellidos) | `alumno` (con su `codigo` generado) |
| `admin/alumno/editar` | `alumno_id, nombres?, apellido_paterno?, apellido_materno?, nombre?, matricula?, llave` (sólo cambian las partes que vengan) | `alumno` |
| `admin/vincular` | `tutor_ref, alumno_id, activo (bool, true por omisión), llave` | `tutor` |

`tutor` = `{tutor_ref, nombre, telefono, correo, activo, alumnos: [ficha]}`. Toda escritura de
administración guarda su respuesta: un reintento con la misma llave la devuelve sin repetir el alta.
`pedido` = `{id, nombre, alumno_id, fecha_entrega, total, estado, lineas}`;
`estado` ∈ `retenida` (por recoger) · `cobrada` · `liberada` (no se recogió) · `cancelada`.

## Idempotencia

Toda escritura lleva `llave` (≤120 caracteres; única por tutor en las operaciones de tutor y única por instancia en las de administración):

- `precompra`, `transferencia`: repetir con la misma llave y el mismo contenido **no vuelve a
  aplicar** la operación; la respuesta trae los saldos **actuales**, no una copia de la primera.
  Con otro contenido, `409`.
- `credencial/bloquear`, `credencial/desbloquear`, `precompra/cancelar`, `alumno/permiso`: repetir devuelve la
  **respuesta guardada** sin volver a ejecutar (así un «desbloquear» viejo reintentado no deshace
  un bloqueo posterior). La autorización se revisa igual en cada reintento. Misma llave con otra
  operación o contenido: `409 llave_reutilizada`.
- `recarga`: idempotente por **`pago_ref`**. El mismo pago con el mismo reparto devuelve `200` sin
  acreditar otra vez. Con **otro** reparto: `409 pago_ya_acreditado` — el pago ya entró, pero
  **con un reparto distinto** al de esta petición: es un error del portal que hay que revisar, no
  una confirmación del reparto pedido.

## Recarga con MercadoPago (lo que le toca al portal)

1. El tutor elige el total y el reparto entre sus alumnos.
2. El portal crea el cobro en la cuenta de MercadoPago **del operador** con
   `external_reference` = id de la recarga en el portal.
3. Llega el webhook. **No se le cree**: el portal consulta el pago en la API de MercadoPago y
   exige `approved` y monto = total del reparto.
4. Llama `recarga` con `pago_ref` = id del pago de MercadoPago y reintenta hasta `200`. Un
   `409 pago_ya_acreditado` NO es éxito: el pago entró con otro reparto; se alerta.
5. Barrido periódico: pagos aprobados sin acreditar ⇒ reintento.

El TPV asienta cada recarga: cargo al diario **MercadoPago** (`MPAGO`), abono al pasivo
`206.01.90 Saldos de monedero de alumnos`.

## Avisos a los tutores por WhatsApp (Conduit)

El TPV avisa a cada tutor con vínculo activo y teléfono, **sólo de sus alumnos**:

| Evento | Cuándo | Texto |
|---|---|---|
| `recarga` | el portal acredita un pago (`recarga`, sólo la primera vez) | importe por alumno y su disponible |
| `compra` | una venta cobra del monedero | lo pagado con saldo (y su pedido, si lo había) y cuánto le queda |
| `pedido_liberado` | un pedido no se recogió en su día | el pedido, lo devuelto y el disponible |

Se **encolan** dentro de la operación (si ésta se revierte, no sale nada) y los manda el cron
«Cafetería: enviar avisos a tutores» a `POST <url>/api/notify` de Conduit, con
`idempotency_key` = llave del aviso. Encolar nunca tumba una venta. Estados en
`sb.cafe.aviso`: `enviado`, `fallido` (400/401/404, o fallas pasajeras agotadas),
`incierto` (Conduit respondió `409` hasta el final de su ventana de 10 min: reintentar lo
duplicaría) y `vencido` (más de 2 h sin salir).

Configuración (Ajustes → Parámetros del sistema). **Sin los tres no se encola nada:**

| Parámetro | Valor |
|---|---|
| `sb_cafe.conduit_url` | base de Conduit, p. ej. `https://conduit.ejemplo.mx` |
| `sb_cafe.conduit_client_id` | `client_id` del inquilino del operador |
| `sb_cafe.conduit_secreto` | su `broker_secret` |

El teléfono del tutor sale de su contacto: 10 dígitos se completan con `52`. Pendiente del lado
de Conduit: plantillas de WhatsApp aprobadas (fuera de la ventana de 24 h Meta no entrega texto
libre) — ver `conduit/docs/INTEGRACION_CAFETERIA_ESCOLAR.md`.
