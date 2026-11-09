"""Contenido del manual de pruebas de la cafetería escolar (lo arma build_manual.py)."""

CASOS = [600, 2300, 3880, 3300]
CAB = ['#', 'Qué probar', 'Cómo', 'Resultado esperado']


def build(k):
    h1, h2, para, bullet, table, note, cover = (k[n] for n in (
        'h1', 'h2', 'para', 'bullet', 'table', 'note', 'cover'))

    def casos(pref, filas):
        return table(CASOS, CAB, [[f'{pref}.{i}', *f] for i, f in enumerate(filas, 1)], sz=18)

    o = [cover('CAFETERÍA ESCOLAR · MANUAL DE PRUEBAS',
               'TPV de la cafetería, administración, portal del padre y WhatsApp',
               'Softbot · versión del 9 de noviembre de 2026')]

    # ------------------------------------------------------------------ 0
    o.append(h1('0. Antes de empezar'))
    o.append(para('Este manual recorre el sistema completo de la cafetería escolar para comprobar que todo '
                  'funciona antes de usarlo con papás reales. Está en **tres secciones**: la configuración del '
                  '**TPV** para la cafetería, la **administración** de la cafetería en el portal, y el **padre de '
                  'familia**, tanto en el portal web como por **WhatsApp**. Cada prueba dice qué hacer y qué debe '
                  'pasar; si el resultado no coincide, anótalo con la hora y una captura.'))
    o.append(h2('Ambiente de prueba'))
    o.append(table([2600, 7480], ['Pieza', 'Dónde'], [
        ['TPV (Odoo)', 'https://cafeteria-demo.tpv.softbot.com.mx · usuario de administrador o de gerente'],
        ['Caja', 'En el TPV: Punto de venta → abrir la caja de la cafetería'],
        ['Portal del padre', 'https://cafeteria.softbot.com.mx/entrar'],
        ['Administración', 'https://cafeteria.softbot.com.mx/admin/entrar'],
        ['WhatsApp de la cafetería', '+1 956 382 5162 (escribir «hola»)'],
        ['Mercado Pago', 'Cuenta de **prueba**: sólo funcionan las tarjetas de prueba (abajo)'],
    ], sz=18))
    o.append(h2('Tarjetas de prueba de Mercado Pago (México)'))
    o.append(para('Vencimiento **11/30** y código de seguridad **123** en todas. El **nombre del titular** decide '
                  'el resultado: `APRO` aprueba; `FUND` (fondos insuficientes) u `OTHE` (error general) rechazan.'))
    o.append(table([3360, 3360, 3360], ['Tipo', 'Marca', 'Número'], [
        ['Crédito', 'Mastercard', '5474 9254 3267 0366'],
        ['Crédito', 'Visa', '4075 5957 1648 3764'],
        ['Débito', 'Mastercard', '5579 0534 6148 2647'],
    ], sz=18))
    o.append(h2('Datos que conviene tener a la mano'))
    for t in [
        '**Dos teléfonos con WhatsApp**: el del papá y el de la mamá (o un segundo tutor), para comprobar que '
        'los avisos llegan a los dos.',
        '**Un correo** para cada tutor: las contraseñas y los códigos también se mandan por correo.',
        '**Una ventana de incógnito** para entrar como papá mientras la administración está abierta en otra: si '
        'se usan en la misma ventana, una sesión cierra la otra.',
        'Las contraseñas **no** van en este documento: se piden a Softbot.',
    ]:
        o.append(bullet(t))

    # ------------------------------------------------------------------ 1
    o.append(h1('1. Configuración del TPV para la cafetería'))
    o.append(para('Todo lo de la cafetería está en el TPV en **Punto de venta → Cafetería**. El TPV guarda los '
                  'saldos (el «monedero» de cada alumno), cobra en la caja y manda los avisos de WhatsApp; el '
                  'portal sólo consulta y pide.'))
    o.append(h2('1.1 Qué revisar en la configuración'))
    o.append(table([2900, 7180], ['Dónde', 'Qué debe estar'], [
        ['Ajustes → Empresas → (la empresa) → pestaña **Cafetería**',
         '**Hora de fin de servicio** (por ejemplo 15:00). A esa hora se liberan los pedidos del día que no se '
         'recogieron. Vacío: a la medianoche. Cerrar la caja **no** libera pedidos.'],
        ['Inventario/Punto de venta → Productos',
         'Los productos que se pueden pedir por adelantado tienen **«Se puede precomprar»**. El producto de '
         'recarga en caja tiene **«Es recarga de saldo»**.'],
        ['Punto de venta → Configuración → Métodos de pago',
         'Existe un método con **«Monedero del alumno»** y está en la caja de la cafetería.'],
        ['Ajustes → Técnico → Parámetros del sistema',
         '`sb_cafe.api_secreto` y `sb_cafe.api_secreto_admin` (conexión con el portal); `sb_cafe.conduit_url`, '
         '`sb_cafe.conduit_client_id` y `sb_cafe.conduit_secreto` (avisos de WhatsApp); '
         '`sb_cafe.conduit_plantillas` = 1 (avisos con plantilla aprobada por Meta).'],
        ['Ajustes → Técnico → Acciones planificadas',
         '«Cafetería: enviar avisos a tutores» (cada minuto) y «Cafetería: liberar saldo apartado al cierre del '
         'día» (cada 15 minutos), las dos activas.'],
    ], sz=18))
    o.append(h2('1.2 Menús de la cafetería'))
    o.append(table([2600, 7480], ['Menú', 'Para qué'], [
        ['Alumnos', 'Ficha de cada alumno: Nombre(s), Apellido 1, Apellido 2, matrícula, **código** para la caja, '
                    'saldo disponible y apartado, credenciales y el botón **Ajustar saldo**.'],
        ['Tutores', 'Papás, mamás y tutores con su teléfono y correo.'],
        ['Tutores y alumnos', 'Quién está vinculado con quién. Un alumno puede tener varios tutores.'],
        ['Credenciales', 'Tarjetas o códigos QR de cada alumno, activas o bloqueadas.'],
        ['Movimientos', 'Libro del monedero: recargas, compras, pedidos, ajustes y transferencias.'],
        ['Precompras', 'Pedidos hechos por los papás: por recoger, cobrados, no recogidos o cancelados.'],
        ['Monedero por revisar', 'Ventas de caja que no pudieron descontar el monedero y hay que revisar a mano.'],
        ['Ajustar saldo', 'Corrección de saldo con motivo; queda en el libro y en contabilidad.'],
        ['Importar alumnos (CSV)', 'Alta masiva: matrícula, nombre, código, teléfono, correo, credencial, tipo, '
                                   'tutor, tutor_telefono, tutor_correo. Reimportar actualiza, no duplica.'],
    ], sz=18))
    o.append(h2('1.3 Pruebas'))
    o.append(casos('1', [
        ['Ficha de alumno por partes', 'Alumnos → abrir un alumno → pestaña Cafetería. Cambiar el Apellido 2 y '
         'guardar.', 'El nombre completo se rehace solo con Nombre(s), Apellido 1 y Apellido 2.'],
        ['Código del alumno en caja', 'Abrir la caja → Cliente → buscar por nombre o escribir el **código** del '
         'alumno.', 'Aparece el alumno y una notificación «… · disponible $X» con su saldo.'],
        ['Compra con monedero', 'Con un alumno que **puede comprar sin pedido**: agregar productos y pagar con '
         '«Monedero del alumno».', 'Se cobra; baja su disponible; en Movimientos aparece la compra; a los tutores '
         'les llega el aviso de compra por WhatsApp.'],
        ['Alumno sin permiso de compra', 'Elegir un alumno al que el papá le quitó «compras sin pedido».',
         'Al elegirlo sale el aviso «Sin monedero para compras»; el monedero no se ofrece como pago.'],
        ['Pedido del papá en caja', 'Elegir a un alumno con pedido para hoy.', 'Sale «Pedido por cobrar» con el '
         'folio y el total. «Cobrar ahora» carga los productos del pedido en su propio ticket.'],
        ['El pedido sólo con monedero', 'Con el pedido cargado, intentar pagar una parte en efectivo.',
         'Lo impide: «El pedido se cobra sólo con monedero…». Lo extra va en otra venta.'],
        ['Precio del pedido', 'Cambiar el precio de un producto después de que el papá pidió; cobrar el pedido.',
         'Se cobra al precio con que se hizo el pedido.'],
        ['Recarga en caja', 'Vender el producto de recarga a un alumno, pagando en efectivo.',
         'Sube su disponible y aparece en Movimientos. (La recarga en caja no manda aviso; la del portal sí.)'],
        ['Ajuste de saldo', 'Alumnos → Ajustar saldo, con motivo.', 'Cambia el saldo; queda en Movimientos como '
         'ajuste y en contabilidad.'],
        ['Bloqueo de credencial', 'Bloquear una credencial (desde el portal o en Credenciales) y pasarla en caja.',
         'La caja la rechaza; desbloqueada vuelve a funcionar.'],
        ['Cerrar caja no libera pedidos', 'Con un pedido de hoy sin recoger, cerrar la caja y abrir otra (cambio '
         'de turno).', 'El pedido sigue «por recoger» y su saldo sigue apartado; no sale aviso.'],
        ['Hora de fin de servicio', 'Poner la hora de fin de servicio unos minutos adelante; esperar a que pase '
         '(el proceso corre cada 15 minutos).', 'El pedido de hoy no recogido pasa a «Liberada»: el saldo vuelve '
         'y a los tutores les llega «pedido no recogido».'],
        ['Pedido para hoy tras la hora', 'Pasada la hora de fin de servicio, pedir para hoy desde el portal.',
         'Se rechaza: «La cafetería ya terminó el servicio de hoy. Pide para otro día.» Para mañana sí se puede.'],
        ['Importar alumnos', 'Importar un CSV con dos alumnos y su tutor; importarlo otra vez.',
         'La primera vez los crea con su código; la segunda no duplica nada. Las filas con error se informan.'],
    ]))
    o.append(note('Clientes archivados: la caja guarda la lista de clientes en el navegador. Si un alumno o tutor '
                  'archivado sigue apareciendo, borrar los datos del sitio en el navegador de la caja (sin ventas '
                  'pendientes de sincronizar).'))

    # ------------------------------------------------------------------ 2
    o.append(h1('2. Administración de la cafetería (portal)'))
    o.append(para('La administración da de alta a los papás y a sus hijos, define cuándo se puede pedir y revisa '
                  'las recargas. Se entra en **/admin/entrar** con el usuario de la cafetería.'))
    o.append(h2('Cómo recibe el papá su acceso'))
    o.append(para('**Por ahora, el papá recibe su usuario y contraseña del portal por correo.** Por eso, al darlo '
                  'de alta, **captura siempre el correo del tutor**. El correo también le dice a qué número de '
                  'WhatsApp de la cafetería escribir y trae una liga para abrir el chat; ese número se captura en '
                  '**Datos de la cafetería** (/admin/cafeteria). Lo mismo aplica al código para recuperar la contraseña.'))
    o.append(para('Más adelante, cuando Meta apruebe la plantilla de acceso (requiere la verificación del negocio), '
                  'el acceso y el código también llegarán por WhatsApp.'))
    o.append(h2('2.1 Pantallas'))
    o.append(table([2600, 7480], ['Pantalla', 'Qué hace'], [
        ['Tutores (/admin)', 'Lista de tutores, altas incompletas y aviso si falta el horario de pedidos.'],
        ['Alta (/admin/alta)', 'Tutor (nombre, teléfono de 10 dígitos y/o correo) y hasta 5 hijos con '
                               '**Nombre(s), Apellido 1, Apellido 2 (opcional)** y matrícula opcional.'],
        ['Ficha del tutor', 'Reenviar acceso, agregar un hijo nuevo, **vincular un hijo que ya existe** (el '
                            'segundo papá o mamá), desvincular, activar o desactivar al tutor.'],
        ['Pedidos (/admin/pedidos)', 'Días de servicio, días de anticipación, hora límite para pedir y días sin '
                                     'servicio (vacaciones y festivos).'],
        ['Datos de la cafetería (/admin/cafeteria)', '**WhatsApp de la cafetería**, con lada de país (por '
                                                   'ejemplo +1 956 382 5162). Va en los correos de acceso y '
                                                   'de recuperación, con una liga que abre el chat.'],
        ['Recargas (/admin/recargas)', 'Recargas de Mercado Pago por estado: pendiente, acreditada, rechazada, '
                                       'expirada o **revisión** (entró el pago pero no se acreditó solo).'],
    ], sz=18))
    o.append(h2('2.2 Pruebas'))
    o.append(casos('2', [
        ['WhatsApp de la cafetería', 'Datos de la cafetería → capturar +1 956 382 5162 → Guardar. Probar '
         'también un número sin lada.', 'Se guarda y muestra la liga https://wa.me/19563825162?text=hola; sin lada '
         'se rechaza.'],
        ['Alta de tutor con dos hijos', 'Alta con teléfono y correo, y dos hijos con Nombre(s) y Apellido 1 '
         '(uno con Apellido 2).', 'Pantalla «Alta completada» con el usuario, la contraseña temporal y el código '
         'de caja de cada hijo. Los hijos aparecen en el TPV con su nombre por partes.'],
        ['Entrega del acceso', 'Revisar el correo del tutor.', 'Le llega su acceso **por correo**, con el número '
         'de WhatsApp de la cafetería y la liga; al tocarla se abre el chat con «hola» escrito. La pantalla de alta dice por qué canal salió.'],
        ['Validaciones del alta', 'Intentar un hijo sin Apellido 1, un teléfono de 9 dígitos y un teléfono ya '
         'registrado.', 'Cada caso se rechaza con su mensaje; no se crea nada en el TPV.'],
        ['Segundo tutor', 'Dar de alta a la mamá sin hijos nuevos; en su ficha, «Vincular un hijo que ya existe» '
         'eligiendo al papá.', 'La mamá ve a los mismos hijos con el mismo saldo; los avisos llegan a los dos.'],
        ['Agregar un hijo', 'En la ficha, «Agregar un hijo nuevo» con Nombre(s) y Apellido 1.',
         'Aparece en la lista con su código; el papá lo ve al entrar.'],
        ['Reenviar acceso', 'En la ficha, reenviar acceso.', 'Se genera una contraseña temporal nueva; la anterior '
         'deja de servir y se cierran sus sesiones.'],
        ['Desvincular', 'Desvincular a un hijo de un tutor.', 'Ese tutor deja de verlo y de recibir sus avisos; el '
         'otro tutor lo sigue viendo.'],
        ['Desactivar tutor', 'Desactivar a un tutor e intentar entrar con él.', 'No puede entrar ni usar WhatsApp.'],
        ['Horario de pedidos', 'Configurar días de servicio, anticipación y hora límite; agregar un día sin '
         'servicio.', 'En el portal y WhatsApp sólo se ofrecen los días permitidos; el día sin servicio no sale.'],
        ['Recargas', 'Después de las pruebas de la sección 3, revisar /admin/recargas.', 'Cada recarga con su '
         'estado y su reparto por hijo; ninguna en «revisión».'],
    ]))

    # ------------------------------------------------------------------ 3
    o.append(h1('3. El padre de familia: portal y WhatsApp'))
    o.append(para('El papá ve el saldo de sus hijos, pide con fecha, cancela, transfiere saldo entre hermanos, '
                  'decide si pueden comprar sin pedido, bloquea tarjetas y recarga con Mercado Pago. Puede hacerlo '
                  'en el **portal** o por **WhatsApp**; las dos vías usan las mismas reglas.'))
    o.append(h2('3.1 Portal web'))
    o.append(casos('3A', [
        ['Primer ingreso', 'Entrar en /entrar con el teléfono (10 dígitos) o el correo y la contraseña temporal.',
         'Pide cambiar la contraseña antes de seguir.'],
        ['Recuperar contraseña', '«¿Olvidaste tu contraseña?» con el teléfono.', 'Llega un código de 6 dígitos '
         '**por correo** que vence en 15 minutos; con él se fija una contraseña nueva.'],
        ['Inicio', 'Ver la pantalla principal.', 'Cada hijo con su disponible y apartado, y los botones «Pedir» y '
         '«Ver detalle».'],
        ['Movimientos', 'Ver detalle de un hijo → movimientos.', 'Recargas, compras, pedidos y ajustes con fecha; '
         '«Ver más» carga los anteriores.'],
        ['Pedir', 'Pedir → elegir fecha → sumar y restar productos.', 'El total se actualiza al momento. Al '
         'confirmar sale un folio (PC-…) y el saldo queda apartado.'],
        ['Pedir sin saldo', 'Pedir más de lo disponible.', 'Se rechaza indicando el disponible.'],
        ['Cancelar pedido', 'Ver detalle → pedido por recoger → cancelar.', 'El pedido queda «cancelado» y el '
         'saldo vuelve al disponible.'],
        ['Transferir', 'Transferir saldo de un hijo a otro.', 'Baja en uno y sube en el otro; no deja pasar más '
         'de lo disponible.'],
        ['Compras sin pedido', 'Quitar el permiso a un hijo y probarlo en la caja (prueba 1.4).',
         'En la caja ya no se ofrece el monedero para compras sueltas; el pedido sí se cobra.'],
        ['Bloquear tarjeta', 'Bloquear la credencial de un hijo y probarla en caja; desbloquear.',
         'Bloqueada no paga; desbloqueada sí.'],
        ['Recargar', 'Recargar → repartir entre los hijos ($50 a $2,000) → pagar con tarjeta de prueba `APRO`.',
         'Mercado Pago aprueba; en menos de un minuto se acredita a cada hijo y llega el aviso de recarga a los '
         'tutores.'],
        ['Recarga rechazada', 'Repetir con titular `FUND`.', 'El pago se rechaza y no se acredita nada.'],
        ['Mis recargas', 'Ver Mis recargas.', 'Cada recarga con su estado y reparto.'],
        ['Páginas legales', 'Abrir el aviso de privacidad y las condiciones desde la pantalla de entrada.',
         'Se ven sin iniciar sesión.'],
    ]))
    o.append(h2('3.2 WhatsApp'))
    o.append(para('Desde el teléfono registrado del papá, escribir **hola** al +1 956 382 5162. El bot contesta '
                  'con un menú numerado; se elige escribiendo el número. **menú** regresa al inicio en cualquier '
                  'momento; **gracias**, **salir** u **8** terminan la conversación. Las palabras que hay que '
                  'escribir van en negritas.'))
    o.append(casos('3B', [
        ['Número no registrado', 'Escribir desde un teléfono que no es de ningún tutor.', 'Responde que el número '
         'no está registrado.'],
        ['Menú', 'Escribir **hola**.', 'Saludo con el nombre y el menú del 1 al 8.'],
        ['Ver saldo', '**1**.', 'Disponible de cada hijo.'],
        ['Recargar', '**2** → total (por ejemplo 300) → cuánto a cada hijo.', '«Distribución de la recarga» con '
         'los saldos; al último hijo le toca el resto. Resumen, «Digita 1 o 2», y con **1** la liga de pago. La '
         'conversación termina y el aviso llega al acreditarse.'],
        ['Pedir por categorías', '**3** → hijo → día → categoría → productos (**2 3** = producto 2, 3 unidades) '
         '→ **categorías** para cambiar → **listo** → **1**.', 'Carrito numerado con total; al confirmar, folio y '
         'saldo apartado.'],
        ['Eliminar del carrito', 'Con varias piezas de un producto, **eliminar 1**.', 'Pregunta si eliminar todas '
         'o algunas, y cuántas.'],
        ['Cancelar pedido', '**4** → hijo → pedido → **1**.', 'Confirmación con la fecha larga; el pedido se '
         'cancela y el saldo vuelve.'],
        ['Transferir', '**5** → de quién → a quién → monto sin centavos → **1**.', '«Listo, transferiste $X» con '
         'los nuevos disponibles.'],
        ['Compras sin pedido', '**6** → hijo → **1**.', 'Cambia el permiso; coincide con el portal y la caja.'],
        ['Tarjetas', '**7** → hijo → tarjeta → **1**.', 'Bloquea o desbloquea; coincide con el portal.'],
        ['Terminar', '**8** o **gracias**.', 'Despedida; **hola** vuelve a empezar.'],
        ['Avisos', 'Hacer una compra con monedero en caja, una recarga y dejar un pedido sin recoger.',
         'Llegan los avisos de compra, recarga y pedido no recogido **a todos los tutores** del alumno.'],
    ]))
    o.append(note('Cuando algo no salga como dice el manual, anotar: número de prueba, hora, teléfono o usuario '
                  'usado y una captura. Con eso Softbot lo localiza en los registros del TPV, del portal y de '
                  'WhatsApp.'))
    return ''.join(o)
