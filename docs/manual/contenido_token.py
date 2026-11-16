"""Guía para el cliente: cómo obtener el Access Token de producción de Mercado Pago.

Se arma con build_manual.py:
  python3 docs/manual/build_manual.py contenido_token Guia-Access-Token-Mercado-Pago.docx \\
      "Cafetería escolar · Access Token de Mercado Pago · Softbot"

Basada en la documentación pública de Mercado Pago Developers (México), consultada el 16 de noviembre
de 2026: Credenciales, Salir a producción (Checkout Pro) y Crear aplicación."""

SITIO = 'https://cafeteria.softbot.com.mx'


def build(k):
    h1, h2, para, bullet, table, note, cover = (k[n] for n in (
        'h1', 'h2', 'para', 'bullet', 'table', 'note', 'cover'))

    def pasos(filas):
        return table([700, 3500, 5880], ['#', 'Qué hacer', 'Detalle'],
                     [[str(i), *f] for i, f in enumerate(filas, 1)], sz=19)

    o = [cover('MERCADO PAGO · CÓMO OBTENER TU ACCESS TOKEN',
               'Para cobrar en tu cuenta las recargas que los padres de familia pagan con tarjeta',
               'Softbot · 16 de noviembre de 2026')]

    # ------------------------------------------------------------------ 1
    o.append(h1('1. Para qué sirve'))
    o.append(para('Los padres de familia recargan el saldo de sus hijos en el portal de la cafetería y por '
                  'WhatsApp, y pagan con tarjeta en **Mercado Pago**. Para que ese dinero llegue a **tu cuenta '
                  'de Mercado Pago**, el portal necesita una llave de tu cuenta llamada **Access Token**.'))
    for t in [
        'El **Access Token** lo genera Mercado Pago dentro de una «aplicación» de tu cuenta. Es gratis.',
        'Es una **llave privada**: con ella se pueden cobrar y devolver pagos de tu cuenta. Trátala como una '
        'contraseña.',
        'El portal sólo necesita el **Access Token de producción**. No necesitas la Public Key, el Client ID '
        'ni el Client Secret.',
        'Hacer los pasos de esta guía toma unos **15 minutos**.',
    ]:
        o.append(bullet(t))

    # ------------------------------------------------------------------ 2
    o.append(h1('2. Antes de empezar'))
    o.append(table([3400, 6680], ['Necesitas', 'Por qué'], [
        ['Una **cuenta de Mercado Pago de vendedor** a nombre de la cafetería', 'Ahí se depositan las recargas. '
         'Puede ser de persona física o moral.'],
        ['Tu **identidad verificada** en Mercado Pago', 'Mercado Pago la pide para activar los cobros reales.'],
        ['Una **cuenta bancaria (CLABE)** dada de alta en Mercado Pago', 'Para retirar el dinero de las '
         'recargas a tu banco.'],
        ['Una **computadora** con la sesión de esa cuenta', 'Los pasos se hacen en el sitio de Mercado Pago '
         'Developers; en el celular algunas pantallas no se ven completas.'],
        ['El **sitio web del portal**', f'Mercado Pago lo pide al activar: `{SITIO}`'],
    ], sz=19))
    o.append(para('Revisa también en tu cuenta de Mercado Pago las **comisiones** por cobro y el **plazo en que '
                  'se libera el dinero**, porque dependen del plan de tu cuenta.'))

    # ------------------------------------------------------------------ 3
    o.append(h1('3. Paso a paso'))
    o.append(note('Mercado Pago cambia de vez en cuando los nombres de sus pantallas. Si algo no coincide '
                  'exactamente, busca lo más parecido: lo importante es crear una aplicación de Checkout Pro, '
                  'activar las credenciales de producción y copiar su Access Token.'))

    o.append(h2('3.1 Entrar a Mercado Pago Developers'))
    o.append(pasos([
        ['Abrir el sitio de desarrolladores', 'En la computadora, entra a '
         '`https://www.mercadopago.com.mx/developers`.'],
        ['Ingresar con la cuenta de la cafetería', 'Toca **Ingresar** y entra con el correo y la contraseña de '
         'la **cuenta de Mercado Pago que va a cobrar** (la de la cafetería).'],
        ['Abrir tus integraciones', 'Arriba a la derecha, entra a **Tus integraciones** (también puedes ir '
         'directo a `https://www.mercadopago.com.mx/developers/panel/app`).'],
    ]))

    o.append(h2('3.2 Crear la aplicación'))
    o.append(para('Si ya tienes una aplicación de **Checkout Pro** para la cafetería, salta al punto 3.3.'))
    o.append(pasos([
        ['Crear aplicación', 'En **Tus integraciones**, toca **Crear aplicación**.'],
        ['Nombre', 'Escribe un nombre que reconozcas, por ejemplo `Portal cafetería <nombre de la cafetería>`.'],
        ['Tipo de solución', 'Si pregunta qué tipo de pagos vas a integrar, elige **pagos en línea** (pagos '
         'online).'],
        ['Plataforma de comercio electrónico', 'Si pregunta si usas una plataforma de tienda en línea, contesta '
         '**No** (el portal es un desarrollo propio).'],
        ['Producto', 'Elige **Checkout Pro**: es la página de pago de Mercado Pago que usa el portal.'],
        ['API integrada', 'Si te pide elegir entre **Orders** y **Preferences** para Checkout Pro, elige '
         '**Preferences**.'],
        ['Confirmar', 'Acepta la **Declaración de Privacidad** y los **Términos y condiciones**, completa la '
         'verificación («No soy un robot») y toca **Crear aplicación**.'],
    ]))
    o.append(para('Al crearla, Mercado Pago genera solas unas **credenciales de prueba**. **Ésas no sirven para '
                  'cobrar**: sigue con el punto 3.3.'))

    o.append(h2('3.3 Activar las credenciales de producción'))
    o.append(pasos([
        ['Abrir la aplicación', 'En **Tus integraciones**, toca la tarjeta de la aplicación que creaste.'],
        ['Ir a Credenciales', 'En el menú de la izquierda, entra a **Credenciales** (en algunas pantallas se llama '
         '**Credenciales de producción**).'],
        ['Elegir Producción', 'En el selector de ambiente, elige **Producción** (no «Prueba»).'],
        ['Activar', 'Toca **Activar ambiente de producción** (o **Activar credenciales**).'],
        ['Industria', 'Elige el giro más parecido a tu negocio (alimentos, restaurantes o educación).'],
        ['Sitio web', f'Escribe `{SITIO}`.'],
        ['Confirmar', 'Acepta la **Declaración de Privacidad** y los **Términos y condiciones**, completa la '
         'verificación y toca **Activar** (o **Activar credenciales de producción**).'],
    ]))

    o.append(h2('3.4 Copiar el Access Token'))
    o.append(pasos([
        ['Revisar el ambiente', 'Con **Producción** elegido en el selector, verás la Public Key, el **Access '
         'Token**, el Client ID y el Client Secret.'],
        ['Copiar sólo el Access Token', 'Toca el botón de copiar junto a **Access Token**. Empieza con `APP_USR-` '
         'y es largo: cópialo completo, sin espacios.'],
        ['Cuidado con el de prueba', 'El Access Token **de prueba también empieza con `APP_USR-`**. Antes de '
         'copiar, revisa que el selector diga **Producción**.'],
    ]))

    # ------------------------------------------------------------------ 4
    o.append(h1('4. Darlo de alta en el portal de la cafetería'))
    o.append(para('Lo hace la **administración de la cafetería** en el portal, con su usuario de administración. '
                  'El Access Token se pega una sola vez y queda guardado cifrado: el portal no lo vuelve a '
                  'mostrar.'))
    o.append(pasos([
        ['Entrar a la administración', f'Entra a `{SITIO}/admin/entrar` con tu usuario de administración y '
         'abre **Pagos con tarjeta**.'],
        ['Pausar los pagos', 'Toca el interruptor **Recargas en línea** → **Pausar**. Mientras están en pausa '
         'no se abren ligas de pago nuevas.'],
        ['Esperar si hace falta', 'Si la sección **Dar de alta o cambiar la cuenta** dice «Todavía hay recargas '
         'que la cuenta actual tiene que revisar», espera y vuelve a entrar: una liga sin pagar se cierra sola '
         'en 24 horas. Si sigue igual después de un día, avisa a Softbot: puede haber un pago por revisar.'],
        ['Pegar el Access Token', 'En **Dar de alta o cambiar la cuenta**, pega el Access Token, escribe tu '
         'contraseña de administración y toca **Guardar cuenta**. El portal lo comprueba con Mercado Pago.'],
        ['Revisar la cuenta', 'A la izquierda, en **Cuenta de Mercado Pago**, debe aparecer el nombre de **tu** '
         'cuenta y **«Real · cobra dinero real»**. Si dice «De prueba», copiaste el de prueba: repite el punto '
         '3.4 con **Producción**.'],
        ['Activar los pagos', 'Toca **Recargas en línea** → **Activar**. Desde ese momento las recargas con '
         'tarjeta se cobran en tu cuenta.'],
    ]))
    o.append(para('**Si prefieres que Softbot lo dé de alta por ti**, no mandes el Access Token por correo, '
                  'WhatsApp ni mensaje. Usa la opción **Compartir credenciales** de Mercado Pago (en '
                  '**Credenciales**, sección «Comparte las credenciales con un desarrollador») o acuerda con '
                  'Softbot hacerlo juntos en una llamada.'))

    # ------------------------------------------------------------------ 5
    o.append(h1('5. Cuidados del Access Token'))
    o.append(table([5040, 5040], ['Sí', 'No'], [
        ['Pégalo directamente en **Pagos con tarjeta** del portal.', 'No lo mandes por correo, WhatsApp, SMS ni '
         'chat.'],
        ['Guárdalo sólo en tu cuenta de Mercado Pago (siempre lo puedes volver a copiar ahí).', 'No lo escribas '
         'en documentos, notas, capturas de pantalla ni papeles.'],
        ['Si crees que alguien lo vio, **renuévalo** en Mercado Pago y vuelve a darlo de alta en el portal.',
         'No compartas tu usuario y contraseña de Mercado Pago para que otra persona lo copie.'],
        ['Usa el de **Producción** para cobrar.', 'No uses el de **Prueba**: con él no entra dinero real.'],
    ], first_bold=False, sz=19))

    # ------------------------------------------------------------------ 6
    o.append(h1('6. Problemas frecuentes'))
    o.append(table([3600, 6480], ['Qué pasa', 'Qué hacer'], [
        ['No aparece **Activar** en Producción', 'Revisa que tu identidad y tus datos de cuenta estén completos '
         'en Mercado Pago; si sigue igual, consulta la ayuda de Mercado Pago.'],
        ['Me pide un sitio web', f'Escribe `{SITIO}`.'],
        ['El portal dice «De prueba · sólo tarjetas de prueba»', 'Copiaste el Access Token de **Prueba**. Vuelve '
         'a copiarlo con el selector en **Producción** y guárdalo otra vez.'],
        ['El portal dice «Mercado Pago no reconoció esa credencial»', 'Se copió incompleto, es de otra '
         'aplicación, o las credenciales de producción no están activas o se renovaron. Revisa en Mercado Pago '
         'que estén activas y cópialo otra vez con su botón de copiar.'],
        ['El portal dice «Pega el Access Token completo…»', 'Lo pegado no parece un Access Token: debe empezar '
         'con `APP_USR-`.'],
        ['El formulario del portal está deshabilitado', 'Primero pausa los pagos. Si aun así dice que hay '
         'recargas que revisar, espera a que terminen; si pasa de un día, avisa a Softbot.'],
        ['Aparece una cuenta que no es la de la cafetería', 'Entraste a Mercado Pago con otra cuenta. Cierra '
         'sesión, entra con la de la cafetería y repite desde el punto 3.1.'],
    ], sz=19))

    # ------------------------------------------------------------------ 7
    o.append(h1('7. Fuentes'))
    o.append(para('Pasos tomados de la documentación pública de Mercado Pago Developers para México:'))
    for t in [
        'Credenciales: `https://www.mercadopago.com.mx/developers/es/docs/your-integrations/credentials`',
        'Salir a producción (Checkout Pro): '
        '`https://www.mercadopago.com.mx/developers/es/docs/checkout-pro-preferences/go-to-production`',
        'Crear aplicación: `https://www.mercadopago.com.mx/developers/es/docs/checkout-pro/create-application`',
    ]:
        o.append(bullet(t))
    o.append(note('¿Dudas? Escribe a Softbot antes de compartir cualquier credencial.'))
    return ''.join(o)
