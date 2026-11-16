# Pasar las recargas a la cuenta de Mercado Pago del cliente

Hoy el portal cobra con la cuenta **de prueba** de Mercado Pago (la aplicación «Portal Cafeteria
Softbot» de la cuenta de Softbot): sólo funcionan las tarjetas de prueba y no entra dinero real.
Esta guía cambia el portal a la cuenta **real de la cafetería**, para que las recargas de los padres
de familia (portal y WhatsApp) se depositen en ella.

En el portal sólo cambia **un dato**: el *Access Token* de producción de la cafetería. Lo demás
(liga de pago, aviso de pago, barrido, acreditación en el TPV) ya está hecho y no se toca. Lo
delicado es **el momento del cambio**: el portal guarda un solo token y con él revisa las recargas
en curso. Por eso el orden de esta guía importa:

1. Terminar las pruebas y dejar que se cierren todas las recargas de prueba (paso 4).
2. **Pausar** las recargas (paso 6).
3. Preparar el TPV real mientras están pausadas (paso 7).
4. Guardar el token de producción (paso 8).

---

## 0. Antes de empezar: dos decisiones

1. **¿De quién es la cuenta que cobra?** Lo normal es que sea **la cuenta de Mercado Pago de la
   cafetería** (el dinero de las recargas es suyo). Si fuera una cuenta de Softbot, Softbot
   recibiría el dinero y tendría que pasárselo a la cafetería.
2. **¿Qué TPV es el real?** Hoy el portal de producción apunta al TPV `cafeteria-demo`, que tiene
   alumnos, saldos y pedidos **de prueba**. **No cobres dinero real mientras apunte a la demo con
   datos de prueba**: un padre de familia real podría recargar a un alumno ficticio. Hay que
   escoger una de dos (lo hace Softbot en el paso 7, **no antes**):
   - limpiar `cafeteria-demo` (dejar sólo alumnos, padres de familia y productos reales) y usarla
     como la real, o
   - crear la instancia definitiva de la cafetería y cambiar a ella el portal.

---

## 1. Lo que necesita la cafetería

- Una **cuenta de Mercado Pago de vendedor** a nombre de la cafetería (persona física o moral),
  con la **identidad verificada** y una **cuenta bancaria (CLABE)** para retirar el dinero.
- Que el **dueño** de esa cuenta pueda entrar en ella desde una computadora (los pasos 2 y 3 los
  hace él o alguien con su sesión abierta).
- Revisar en su cuenta las **comisiones** y el **plazo en que se libera el dinero** de cada cobro,
  porque cambian según el plan que tenga contratado.

---

## 2. Crear la aplicación en la cuenta de la cafetería

Lo hace el dueño de la cuenta, en <https://www.mercadopago.com.mx/developers/panel/app>:

1. Entrar con la cuenta de Mercado Pago **de la cafetería** (no con la de Softbot).
2. **Crear aplicación**.
3. Nombre: `Portal cafetería <nombre de la cafetería>`.
4. Cuando pregunte qué se va a integrar, elegir **pagos en línea** con **Checkout Pro**. Es el
   producto que usa el portal: Mercado Pago muestra la página de pago y el padre de familia paga ahí
   con su tarjeta.
5. Si pregunta si se usa una plataforma de comercio electrónico, contestar que **no** (es un
   desarrollo propio).
6. Aceptar los términos y crear la aplicación.

> Los nombres de las pantallas de Mercado Pago cambian a veces. Si algo no coincide, lo que importa
> es: aplicación nueva, **Checkout Pro**, en la cuenta de la cafetería.

---

## 3. Activar las credenciales de producción

1. Dentro de la aplicación, ir a **Credenciales de producción**.
2. Llenar lo que pida para activarlas:
   - Industria o giro: **alimentos / cafetería escolar**.
   - Sitio web: `https://cafeteria.softbot.com.mx`.
3. Activar. Aparecen la **Public Key** y el **Access Token**, que empieza con `APP_USR-`.
4. El portal sólo necesita el **Access Token**. La Public Key no se usa.

🔴 **El Access Token es una llave para cobrar y devolver dinero de esa cuenta.**
- No lo mandes por correo, WhatsApp ni chat, y no lo pegues en ningún documento.
- Se escribe **una sola vez**, directamente en el servidor, en el paso 8, y ahí queda cifrado.
- Si alguien lo vio, se renueva en la misma pantalla de credenciales y se vuelve a guardar.

**No hay que configurar notificaciones (webhooks) en el panel.** El portal le dice a Mercado Pago a
dónde avisar en cada pago (`https://cafeteria.softbot.com.mx/mp/aviso`), y además nunca se cree el
aviso: siempre consulta el pago en Mercado Pago antes de acreditar.

---

## 4. Terminar las pruebas y dejar que se cierren las recargas de prueba

Mientras quede una recarga de prueba en curso, el portal necesita la cuenta de prueba para
revisarla:

- Una recarga con liga de pago se busca en Mercado Pago **hasta 24 horas**, aunque la liga venza a
  los 30 minutos.
- Toda recarga pagada tiene además una revisión final (**conciliación**) 15 minutos después de que
  vence su liga, para detectar pagos dobles.

1. Avisar a quienes estén probando que **dejen de recargar** desde ese momento, en el portal y por
   WhatsApp.
2. En la administración del portal → **Recargas**, resolver las que estén en **revisión**: no se
   arreglan solas. Revisar el pago en Mercado Pago y, según el caso, abonarlo en el TPV
   (**Cafetería → Ajustar saldo**) o devolverlo en Mercado Pago. Anotar cómo se resolvió cada una:
   el portal no tiene un botón para cerrarlas y seguirán diciendo «revisión».
3. Esperar a que las **pendientes** venzan o se acrediten. Puede tardar **hasta 24 horas** desde la
   última recarga de prueba.

No hace falta adivinar si ya terminó: el paso 6 lo comprueba y no deja pausar si queda algo en
curso.

---

## 5. Respaldo de la base de datos

En una terminal de tu Mac:

```bash
ssh gerri@softbot.com.mx '~/cafeteria-portal/deploy/respaldo.sh'
```

Debe contestar `respaldo: /home/gerri/respaldos/cafeteria-portal/portal_<fecha>.dump`.

---

## 6. Pausar las recargas

Este paso quita el token de prueba. **Sin token, el portal, WhatsApp y el asistente dejan de crear
recargas.** Todo se hace en una sola operación que bloquea la tabla de recargas mientras revisa, así
que no se puede colar una recarga nueva en medio:

- Si queda alguna recarga en curso, o alguna se creó en los últimos 5 minutos, **no pausa nada** y
  dice cuántas quedan. En ese caso, esperar (paso 4) y volver a correrlo.
- Si no queda ninguna, quita el token y retira las recargas que nunca tuvieron liga de pago (así una
  pantalla vieja no puede reusarlas con la cuenta nueva).
- Al final imprime el **corte**: el número de la última recarga de prueba y la hora.

En una terminal de tu Mac (todo el bloque de una vez):

```bash
ssh gerri@softbot.com.mx 'cd ~/cafeteria-portal/deploy && docker compose -p cafeteria-portal exec -T db psql -U portal -d portal -tA -v ON_ERROR_STOP=1' <<'SQL'
BEGIN;
LOCK TABLE recarga IN SHARE ROW EXCLUSIVE MODE;
DO $$
DECLARE
  op integer;
  n integer;
BEGIN
  SELECT id INTO op FROM operador WHERE host = 'cafeteria.softbot.com.mx' FOR UPDATE;
  IF op IS NULL THEN RAISE EXCEPTION 'NO SE PAUSÓ: no existe el operador'; END IF;
  SELECT count(*) INTO n FROM recarga
   WHERE operador_id = op
     AND (estado = 'aprobada'
          OR (estado = 'pendiente' AND init_point IS NOT NULL)
          OR (estado = 'pendiente' AND creada > now() - interval '5 minutes')
          OR (estado IN ('acreditada', 'revision') AND NOT conciliada));
  IF n > 0 THEN RAISE EXCEPTION 'NO SE PAUSÓ: quedan % recargas en curso. Espera y vuelve a intentar.', n; END IF;
  UPDATE operador SET mp_access_token_cif = NULL WHERE id = op;
  UPDATE recarga SET llave = left(llave, 100) || '-antes-del-corte'
   WHERE operador_id = op AND init_point IS NULL AND llave NOT LIKE '%-antes-del-corte';
END $$;
SELECT 'PAUSADAS. Corte: ultima recarga de prueba #' || coalesce(max(id), 0) || ' a las ' || now() FROM recarga;
COMMIT;
SQL
```

- Si dice `NO SE PAUSÓ: quedan N recargas en curso…`, no cambió nada: esperar y repetir.
- Si dice `PAUSADAS. Corte: ultima recarga de prueba #N a las …`, **anota ese número y la hora**:
  todas las recargas con número igual o menor son de prueba (paso 12). Este corte se anota **una
  sola vez**, en el cambio de cuenta; si más adelante se vuelve a pausar (paso 11), ese número no
  lo reemplaza.
- Comprobar en el portal, como padre de familia, que ya no aparece «Recargar saldo».

---

## 7. Preparar el TPV real (con las recargas pausadas)

Ahora, y no antes, Softbot hace lo que se decidió en el paso 0: limpiar `cafeteria-demo` o conectar
el portal a la instancia definitiva. Como no hay token, ningún pago de prueba puede acreditarse en el
TPV real durante este paso.

Antes de seguir:

1. Comprobar que en el portal sólo aparecen padres de familia e hijos reales.
2. Comprobar que ninguna recarga de prueba alcanzó a abrir su liga justo durante la pausa
   (cambia `N` por el número del corte):

   ```bash
   ssh gerri@softbot.com.mx 'cd ~/cafeteria-portal/deploy && docker compose -p cafeteria-portal exec -T db psql -U portal -d portal -tAc "select count(*) from recarga where id > N and init_point is not null"'
   ```

   Debe dar `0`. Si da otro número, son ligas de pago **de prueba**: nunca se acreditarán (sin
   token no se consultan, y la cuenta de producción no las ve). Anota sus números para no contarlas
   como reales en el paso 12.

---

## 8. Guardar el Access Token de producción

Con el Access Token de producción a la mano (en la pantalla de Mercado Pago), en una terminal de tu
Mac:

```bash
ssh -t gerri@softbot.com.mx 'cd ~/cafeteria-portal/deploy && docker compose -p cafeteria-portal exec portal python -m portal.cli guardar-mp --host cafeteria.softbot.com.mx'
```

- Pide `Access Token de MercadoPago:`. Pégalo y da Enter. **No se ve mientras lo escribes**; es
  normal.
- Debe contestar `Token de MercadoPago guardado en cafeteria.softbot.com.mx.`
- No hay que reiniciar nada: vuelve a aparecer «Recargar saldo» y la siguiente recarga ya usa la
  cuenta de la cafetería.

> Desde Claude Code se puede escribir con `!` al principio (`! ssh -t …`) para que corra en la
> sesión sin que el token pase por el chat.

---

## 9. Probar con dinero real

Con una tarjeta propia y a un alumno real (o uno de prueba creado para esto en el TPV real):

1. Entrar al portal como padre de familia → **Recargar saldo** → **$50** a un hijo → **Listo** →
   **Pagar en línea**.
2. La página de pago ya **no** es de prueba: acepta tarjetas reales y rechaza las de prueba.
3. Pagar. Al regresar al portal debe salir **«¡Listo! Se acreditaron $50»**. Si sale «Pago
   recibido» o «Aún no vemos tu pago», esperar un minuto y revisar de nuevo.
4. Comprobar que:
   - el saldo del hijo subió $50 en el portal;
   - llegó el aviso de recarga por WhatsApp;
   - en la administración → **Recargas** aparece **acreditada**, con su número de pago de Mercado
     Pago y con número **mayor que el corte**;
   - en el TPV → **Cafetería → Movimientos** aparece la recarga;
   - en la cuenta de Mercado Pago de la cafetería aparece el cobro, y en el estado de cuenta de la
     tarjeta, como **CAFETERIA**.
5. Repetir una vez por **WhatsApp** (escribir **hola** → **2**) para comprobar la liga de pago del bot.

---

## 10. Devolver la prueba

La devolución en Mercado Pago **no** baja el saldo del alumno en el TPV: se hacen las dos cosas.

1. En la cuenta de Mercado Pago de la cafetería: **Actividad** → el cobro de $50 → **Devolver**.
2. En el TPV: **Cafetería → Ajustar saldo** → el alumno → **Descontar** $50 con el motivo
   «Devolución de la recarga de prueba de Mercado Pago».

Lo mismo aplica más adelante a cualquier devolución real a un padre de familia, y a los
**«Pago extra»** que marque la administración (un padre de familia que pagó dos veces la misma
recarga): se devuelve en Mercado Pago y, si se acreditó, se descuenta en el TPV.

---

## 11. Si algo sale mal

- **La página de pago no abre** («No pudimos abrir Mercado Pago»): el token está mal copiado o las
  credenciales de producción no están activas. Revisar el paso 3 y volver a guardar el token
  (paso 8).
- **Se cobró pero no se acreditó** después de unos minutos: no volver a cobrar.
  - Si en la administración → Recargas sale como **aprobada**, el barrido la reintenta cada minuto.
  - Si sale como **revisión**, no se arregla sola: se resuelve a mano como en el paso 4.
  - Si sigue sin acreditarse, avisar a Softbot con la hora y el número de pago.
- 🔴 **Nunca regreses al token de prueba** en el portal de producción. Con la cuenta de prueba, un
  pago con tarjeta de prueba crearía saldo sin dinero real. Además, las recargas reales en curso
  dejarían de poder consultarse. Las pruebas con la cuenta de prueba se hacen en un ambiente aparte.
- **Para suspender las recargas** mientras se arregla algo: repetir el paso 6. Sólo pausa cuando ya
  no quedan recargas reales en curso; si las hay, dice cuántas y no toca nada. Para reanudar,
  repetir el paso 8 con el mismo Access Token de producción.
  🔴 **El número que imprime esa pausa NO es un corte nuevo**: ignóralo. El corte que separa
  pruebas de producción es **sólo el de la primera vez** (el que anotaste en el paso 6, antes de
  guardar el token de producción); las recargas posteriores a él son reales aunque haya pausas.

---

## 12. Después del cambio

- La aplicación de prueba «Portal Cafeteria Softbot» de la cuenta de Softbot **no se borra**: sirve
  para probar en otros ambientes, nunca en el portal de producción.
- **El historial mezcla pruebas y producción.** La lista de recargas de la administración y «Mis
  recargas» de los padres de familia siguen mostrando las recargas de prueba anteriores, incluso
  las acreditadas. Para separarlas usa el **corte del paso 6**: las recargas con número igual o
  menor al anotado, y las que hayas anotado en el paso 7, son de prueba y **no son ingresos**. Las
  demás son de la cuenta de la cafetería.
- En el TPV, las recargas en línea se registran en el diario **MercadoPago (MPAGO)** contra la cuenta
  del monedero. A partir del corte, ese diario representa el saldo de la cuenta de Mercado Pago de
  la cafetería y se concilia contra sus retiros. Si el TPV de prueba se volvió el real (paso 7), los
  asientos de prueba de antes del corte se limpian junto con los datos de prueba.
- Las recargas **en caja** (efectivo o tarjeta en la terminal de la cafetería) no pasan por Mercado
  Pago y no cambian con esta guía.
