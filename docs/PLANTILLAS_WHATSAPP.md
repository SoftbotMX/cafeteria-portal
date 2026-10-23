# Plantillas de WhatsApp de la cafetería (para registrar en Meta)

Fuera de la ventana de 24 h (el papá no ha escrito al número de la cafetería en el último día),
WhatsApp sólo deja **iniciar** un mensaje con una plantilla aprobada. Esto cubre los avisos del TPV
y los mensajes que inicia el portal. Las **respuestas del bot** no necesitan plantilla: el papá acaba
de escribir y estamos dentro de la ventana.

Se registran en la **WABA de la cafetería** (Meta Business → WhatsApp Manager → Plantillas), idioma
**Español (MEX) `es_MX`**. Reglas de Meta que condicionan el texto:

- Las variables `{{1}}`, `{{2}}`… **no pueden ir vacías** ni llevar saltos de línea, tabuladores o
  más de cuatro espacios seguidos.
- El cuerpo no puede **empezar ni terminar** con una variable, y debe llevar texto suficiente
  alrededor (si no, Meta lo rechaza por «demasiadas variables para su longitud»).
- Cada plantilla necesita un **ejemplo** por variable al enviarla a revisión.

Envío: `POST /api/notify` de Conduit con `template {name, language: "es_MX", components}` e
`idempotency_key` obligatorio (Conduit #42).

## Avisos del TPV (categoría **Utilidad**)

| Nombre | Cuerpo | Ejemplo de variables | Evento del TPV hoy |
|---|---|---|---|
| `recarga_acreditada` | Hola, se acreditó tu recarga en la cafetería: {{1}}. Gracias por usar el portal. | {{1}} = `Luis +$100.00 (disponible $210.00); Ana +$50.00 (disponible $50.00)` | `recarga` (un aviso por tutor con sus hijos) |
| `compra_realizada` | Aviso de la cafetería: {{1}} pagó {{2}} con su saldo. Le quedan {{3}} disponibles. | `Luis` · `$45.00` · `$165.00` | `compra` (hoy agrega «(su pedido PC-00002)»: se omite en la plantilla) |
| `pedido_liberado` | Aviso de la cafetería: {{1}} no recogió su pedido {{2}} del {{3}}. Le devolvimos {{4}} a su saldo y tiene {{5}} disponibles. | `Luis` · `PC-00002` · `22/10/2026` · `$75.00` · `$240.00` | `pedido_liberado` |

> El diseño de Conduit (#42) menciona `saldo_bajo`: el TPV **no** tiene ese aviso hoy. Si se quiere,
> es trabajo nuevo en el TPV (umbral por alumno o por tutor).

## Mensajes del portal

| Nombre | Categoría | Cuerpo | Ejemplo | Cuándo |
|---|---|---|---|---|
| `acceso_portal` | Utilidad | Hola {{1}}, ya tienes acceso al portal de la cafetería en {{2}} con el usuario {{3}} y la contraseña temporal {{4}}. Te pediremos cambiarla al entrar. | `Ana` · `https://cafeteria.softbot.com.mx` · `8341445807` · `fresa-limon-42` | Alta del tutor y «Reenviar acceso» |
| `codigo_verificacion` | **Autenticación** | (texto fijo de Meta) «*{{1}}* es tu código de verificación.» + botón **Copiar código**; vence en 10 min | `148789` | Recuperar contraseña |

Notas:
- La categoría **Autenticación** tiene el texto fijo de Meta (no se redacta) y es la que corresponde
  a códigos de un solo uso; Meta la aprueba casi siempre al momento.
- `acceso_portal` lleva una contraseña temporal. Meta permite enviarla en Utilidad, pero puede
  reclasificarla o rechazarla. Si la rechaza, la alternativa es mandar sólo la liga y el usuario, y
  que el papá use «¿Olvidaste tu contraseña?», que llega con `codigo_verificacion`.

## Orden

1. Crear la WABA y el número de la cafetería (Conduit, `docs/GUIA_META_WHATSAPP.md` §8).
2. **Registrar estas cinco plantillas** (en paralelo al alta del inquilino: la revisión tarda de
   minutos a 48 h).
3. Con las plantillas **aprobadas**: el TPV y el portal empiezan a mandar `template`. Antes de eso
   recibirían `400 132001` (plantilla inexistente).
