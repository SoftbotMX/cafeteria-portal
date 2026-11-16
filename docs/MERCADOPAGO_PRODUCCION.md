# Pasar las recargas a la cuenta de Mercado Pago del cliente

Hoy el portal cobra con la cuenta **de prueba** de Mercado Pago (la aplicación «Portal Cafeteria
Softbot» de la cuenta de Softbot): sólo funcionan las tarjetas de prueba y no entra dinero real.
Esta guía cambia el portal a la cuenta **real de la cafetería**, para que las recargas de los padres
de familia (web y WhatsApp) se depositen en ella.

Todo se hace desde la administración del portal, en **Pagos con tarjeta** (`/admin/pagos`): ahí se
pausan y activan los pagos con tarjeta y se da de alta la cuenta con su *Access Token*. Lo mismo
sirve más adelante para cambiar a otra cuenta.

---

## 0. Antes de empezar: dos decisiones

1. **¿De quién es la cuenta que cobra?** Lo normal es que sea **la cuenta de Mercado Pago de la
   cafetería** (el dinero de las recargas es suyo). Si fuera una cuenta de Softbot, Softbot
   recibiría el dinero y tendría que pasárselo a la cafetería.
2. **¿Qué TPV es el real?** Hoy el portal de producción apunta al TPV `cafeteria-demo`, que tiene
   alumnos, saldos y pedidos **de prueba**. **No cobres dinero real mientras apunte a la demo con
   datos de prueba**: un padre de familia real podría recargar a un alumno ficticio. Hay que
   escoger una de dos (lo hace Softbot en el paso 5, con los pagos en pausa):
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
- Se pega **una sola vez**, directamente en la administración del portal (paso 6). Ahí queda cifrado
  y no se vuelve a mostrar.
- Si alguien lo vio, se renueva en la misma pantalla de credenciales y se vuelve a dar de alta.

**No hay que configurar notificaciones (webhooks) en el panel.** El portal le dice a Mercado Pago a
dónde avisar en cada pago (`https://cafeteria.softbot.com.mx/mp/aviso`), y además nunca se cree el
aviso: siempre consulta el pago en Mercado Pago antes de acreditar.

---

## 4. Pausar los pagos con tarjeta

1. Entrar a la administración del portal → **Pagos con tarjeta**.
2. Tocar el interruptor **Recargas en línea** → **Pausar**.
   Desde ese momento ya no se abren ligas de pago nuevas, ni en la web ni por WhatsApp. Las ligas
   que ya se habían abierto siguen vigentes hasta 30 minutos y, si alguien paga con una, su recarga
   se acredita. Las recargas en la caja de la cafetería siguen funcionando.
3. Mirar la columna **Dar de alta o cambiar la cuenta**:
   - Si dice **«Todavía hay recargas que la cuenta actual tiene que revisar…»**, el formulario está
     deshabilitado a propósito. Son recargas que se armaron con la cuenta anterior y que esa cuenta
     todavía tiene que revisar. Si se cambiara de cuenta ahora, sus pagos no se podrían acreditar.
     Hay que esperar: una liga sin pagar se cierra sola en **24 horas**. Las que estén en
     **revisión** se resuelven a mano en **Recargas** (abonándolas en el TPV con **Cafetería →
     Ajustar saldo** o devolviéndolas en Mercado Pago).
   - Cuando ya no queda ninguna, el formulario se habilita.

---

## 5. Preparar el TPV real (con los pagos en pausa)

**Sólo cuando el formulario del paso 4 ya esté habilitado** (sin recargas en curso, para que ningún
pago de la cuenta de prueba se acredite en el TPV real), Softbot hace lo que se decidió en el paso 0: limpiar `cafeteria-demo` o conectar el portal a la
instancia definitiva. Como los pagos están en pausa, nadie puede recargar en línea mientras tanto.

Antes de seguir, comprobar que en el portal sólo aparecen padres de familia e hijos reales.

---

## 6. Dar de alta la cuenta de la cafetería

En **Pagos con tarjeta → Dar de alta o cambiar la cuenta**:

1. Pegar el **Access Token** de producción (paso 3).
2. Escribir **tu contraseña de administración**.
3. Tocar **Guardar cuenta**.

El portal **comprueba el token con Mercado Pago antes de guardarlo**: no hay que verificar después.

- Si sale bien: «Credenciales guardadas: cuenta real …». A la izquierda, en **Cuenta de Mercado
  Pago**, aparecen el nombre y el número de la cuenta y **«Real · cobra dinero real»**. Revisa que
  sea la cuenta de la cafetería.
- Si dice **«De prueba · sólo tarjetas de prueba»**, se pegó el token de prueba: repetir con el de
  **producción**.
- Si no lo acepta, explica por qué (token incompleto, Mercado Pago no lo reconoce, cuenta que no es
  de México, contraseña equivocada) y no cambia nada.

Los pagos **siguen en pausa** después de guardar.

---

## 7. Activar los pagos

Tocar **Recargas en línea** → **Activar**. El recuadro de confirmación avisa que se cobrará con la
cuenta real. Desde ese momento vuelve «Recargar saldo» en el portal y en WhatsApp.

---

## 8. Probar con dinero real

Con una tarjeta propia y a un alumno real (o uno de prueba creado para esto en el TPV real):

1. Entrar al portal como padre de familia → **Recargar saldo** → **$50** a un hijo → **Listo** →
   **Pagar en línea**.
2. La página de pago ya **no** es de prueba: acepta tarjetas reales y rechaza las de prueba.
3. Pagar. Al regresar al portal debe salir **«¡Listo! Se acreditaron $50»**. Si sale «Pago
   recibido» o «Aún no vemos tu pago», esperar un minuto y revisar de nuevo.
4. Comprobar que:
   - el saldo del hijo subió $50 en el portal;
   - llegó el aviso de recarga por WhatsApp;
   - en la administración → **Recargas** aparece **acreditada**, con su número de pago de Mercado Pago;
   - en el TPV → **Cafetería → Movimientos** aparece la recarga;
   - en la cuenta de Mercado Pago de la cafetería aparece el cobro, y en el estado de cuenta de la
     tarjeta, como **CAFETERIA**.
5. Repetir una vez por **WhatsApp** (escribir **hola** → **2**) para comprobar la liga de pago del bot.

---

## 9. Devolver la prueba

La devolución en Mercado Pago **no** baja el saldo del alumno en el TPV: se hacen las dos cosas.

1. En la cuenta de Mercado Pago de la cafetería: **Actividad** → el cobro de $50 → **Devolver**.
2. En el TPV: **Cafetería → Ajustar saldo** → el alumno → **Descontar** $50 con el motivo
   «Devolución de la recarga de prueba de Mercado Pago».

Lo mismo aplica más adelante a cualquier devolución real a un padre de familia, y a los
**«Pago extra»** que marque la administración (un padre de familia que pagó dos veces la misma
recarga): se devuelve en Mercado Pago y, si se acreditó, se descuenta en el TPV.

---

## 10. Si algo sale mal

- **La página de pago no abre** («No pudimos abrir Mercado Pago»): primero volver a tocar «Pagar en
  línea» unos minutos después; puede ser una falla pasajera de Mercado Pago. Si sigue igual, revisar
  en Mercado Pago que las credenciales de producción estén activas (paso 3) y, si hace falta, volver
  a dar de alta el Access Token (pasos 4 y 6).
- **Se cobró pero no se acreditó** después de unos minutos: no volver a cobrar.
  - Si en la administración → Recargas sale como **aprobada**, el portal la reintenta cada minuto.
  - Si sale como **revisión**, no se arregla sola: se resuelve a mano como en el paso 4.
  - Si sigue sin acreditarse, avisar a Softbot con la hora y el número de pago.
- **Para suspender las recargas en línea** mientras se arregla algo: pausar con el interruptor. Las
  recargas en curso se siguen revisando y acreditando aunque estén en pausa.
- 🔴 **No vuelvas a dar de alta la cuenta de prueba** en el portal de producción. Con ella, un pago
  con tarjeta de prueba crearía saldo sin dinero real.

---

## 11. Cambiar a otra cuenta más adelante

Es el mismo camino: pausar (paso 4), esperar a que el formulario se habilite, dar de alta la cuenta
nueva con su Access Token (paso 6), revisar que diga «Real» con el nombre correcto y activar
(paso 7).

---

## 12. Después del cambio

- La aplicación de prueba «Portal Cafeteria Softbot» de la cuenta de Softbot **no se borra**: sirve
  para probar en otros ambientes, nunca en el portal de producción.
- Las recargas de prueba anteriores siguen en el historial (administración → Recargas y «Mis
  recargas»). Son de prueba y **no son ingresos**.
- En el TPV, las recargas en línea se registran en el diario **MercadoPago (MPAGO)** contra la cuenta
  del monedero. A partir del cambio, ese diario representa el saldo de la cuenta de Mercado Pago de
  la cafetería y se concilia contra sus retiros.
- Las recargas **en caja** (efectivo o tarjeta en la terminal de la cafetería) no pasan por Mercado
  Pago y no cambian con esta guía.
