"""Contenido del manual de pruebas de la cafetería escolar (lo arma build_manual.py)."""

CASOS = [600, 2300, 3880, 3300]
CAB = ['#', 'Qué probar', 'Cómo', 'Resultado esperado']


def build(k):
    h1, h2, para, bullet, table, note, cover = (k[n] for n in (
        'h1', 'h2', 'para', 'bullet', 'table', 'note', 'cover'))

    def casos(pref, filas):
        return table(CASOS, CAB, [[f'{pref}.{i}', *f] for i, f in enumerate(filas, 1)], sz=18)

    o = [cover('CAFETERÍA ESCOLAR · MANUAL DE PRUEBAS',
               'TPV de la cafetería, administración, portal del padre de familia y WhatsApp',
               'Softbot · versión del 16 de noviembre de 2026')]

    # ------------------------------------------------------------------ 0
    o.append(h1('0. Antes de empezar'))
    o.append(para('Este manual recorre el sistema completo de la cafetería escolar para comprobar que todo '
                  'funciona antes de usarlo con padres de familia reales. Tiene **cuatro secciones**: el **TPV** '
                  '(configuración y caja), la **administración** de la cafetería en el portal, el **padre de '
                  'familia** en el portal web y por **WhatsApp**, y un repaso de la **recarga del monedero de cada '
                  'alumno** por todas sus vías. Cada prueba dice qué hacer y qué debe pasar; si el resultado no '
                  'coincide, anótalo con la hora y una captura.'))
    o.append(h2('Ambiente de prueba'))
    o.append(table([2600, 7480], ['Pieza', 'Dónde'], [
        ['TPV (Odoo)', 'https://cafeteria-demo.tpv.softbot.com.mx · usuario de administrador o de gerente'],
        ['Caja', 'En el TPV: Punto de venta → abrir la caja de la cafetería'],
        ['Portal del padre de familia', 'https://cafeteria.softbot.com.mx/entrar'],
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
        '**Dos teléfonos con WhatsApp**: el del papá y el de la mamá (dos padres de familia del mismo hijo), '
        'para comprobar que los avisos llegan a los dos.',
        '**Un correo** para cada padre de familia: el acceso y los códigos de recuperación se mandan por correo.',
        '**Una ventana de incógnito** para entrar como padre de familia mientras la administración está abierta '
        'en otra: en la misma ventana, una sesión cierra la otra.',
        '**Un celular** además de la computadora: el portal está pensado primero para celular, y la '
        'administración cambia el menú lateral por una barra inferior.',
        '**El Access Token de la cuenta de Mercado Pago** que va a cobrar, para la prueba 2C.8. No va en este '
        'documento ni se manda por correo o WhatsApp.',
        'Las contraseñas **no** van en este documento: se piden a Softbot.',
    ]:
        o.append(bullet(t))
    o.append(h2('Orden sugerido'))
    for t in [
        'Revisar la configuración del TPV (1.1) y de la administración (2.2, datos de la cafetería y horario).',
        'Dar de alta a un padre de familia con dos hijos (2.3) y entrar con su acceso (3.1).',
        'Recargar desde el portal, desde WhatsApp y desde la caja (sección 4) antes de las pruebas de compra.',
        'Probar la caja (1.3), los pedidos y el resto del portal y de WhatsApp.',
    ]:
        o.append(bullet(t))

    # ------------------------------------------------------------------ 1
    o.append(h1('1. TPV de la cafetería'))
    o.append(para('Todo lo de la cafetería está en el TPV en **Punto de venta → Cafetería**. El TPV guarda los '
                  'saldos (el **monedero** de cada alumno), cobra en la caja, recarga en caja y manda los avisos de '
                  'WhatsApp. El portal **no guarda saldos**: cada vez que el padre de familia abre una pantalla, el '
                  'portal se los pide al TPV, así que todo lo que pase en la caja se ve de inmediato en el portal.'))
    o.append(h2('1.1 Qué revisar en la configuración'))
    o.append(table([2900, 7180], ['Dónde', 'Qué debe estar'], [
        ['Ajustes → Empresas → (la empresa) → pestaña **Cafetería**',
         '**Hora de fin de servicio** (por ejemplo 15:00). A esa hora se liberan los pedidos del día que no se '
         'recogieron. Vacío: a la medianoche. Cerrar la caja **no** libera pedidos.'],
        ['Inventario/Punto de venta → Productos',
         'Los productos que se pueden pedir por adelantado tienen **«Se puede precomprar»**. El producto '
         '**«Recarga de saldo»** tiene **«Es recarga de saldo»**; no aparece entre los productos de la caja: '
         'se agrega con el botón **«Recargar saldo»**.'],
        ['Punto de venta → Configuración → Métodos de pago',
         'Existe un método con **«Monedero del alumno»** y está en la caja de la cafetería, junto con Efectivo '
         'y Tarjeta.'],
        ['Ajustes → Técnico → Parámetros del sistema',
         '`sb_cafe.api_secreto` y `sb_cafe.api_secreto_admin` (conexión con el portal); `sb_cafe.conduit_url`, '
         '`sb_cafe.conduit_client_id` y `sb_cafe.conduit_secreto` (avisos de WhatsApp); '
         '`sb_cafe.conduit_plantillas` = 1 (avisos con plantilla aprobada por Meta).'],
        ['Ajustes → Técnico → Acciones planificadas',
         '«Cafetería: enviar avisos a tutores» (cada minuto) y «Cafetería: liberar saldo apartado al cierre del '
         'día» (cada 15 minutos), las dos activas.'],
    ], sz=18))
    o.append(h2('1.2 Menús de la cafetería'))
    o.append(para('Los menús del TPV conservan el nombre «Tutores»: en el TPV así se llama al padre de familia.'))
    o.append(table([2600, 7480], ['Menú', 'Para qué'], [
        ['Alumnos', 'Ficha de cada alumno: Nombre(s), Apellido 1, Apellido 2, matrícula, **código** para la caja, '
                    'saldo disponible y apartado, credenciales y el botón **Ajustar saldo**.'],
        ['Tutores', 'Padres de familia con su teléfono y correo.'],
        ['Tutores y alumnos', 'Quién está vinculado con quién. Un alumno puede tener varios padres de familia.'],
        ['Credenciales', 'Tarjetas o códigos QR de cada alumno, activas o bloqueadas.'],
        ['Movimientos', 'Libro del monedero: recargas (portal, WhatsApp y caja), compras, pedidos, ajustes y '
                        'transferencias.'],
        ['Precompras', 'Pedidos hechos por los padres de familia: por recoger, cobrados, no recogidos o '
                       'cancelados.'],
        ['Monedero por revisar', 'Ventas de caja que no cuadraron con el monedero (por ejemplo, una recarga que '
                                 'no se pudo abonar) y que el gerente tiene que revisar a mano.'],
        ['Ajustar saldo', 'Abonar o descontar saldo a un alumno con motivo; queda en el libro y en contabilidad.'],
        ['Importar alumnos (CSV)', 'Alta masiva: matrícula, nombre, código, teléfono, correo, credencial, tipo, '
                                   'tutor, tutor_telefono, tutor_correo. Reimportar actualiza, no duplica.'],
    ], sz=18))
    o.append(h2('1.3 Pruebas en el TPV y en la caja'))
    o.append(casos('1', [
        ['Ficha de alumno por partes', 'Alumnos → abrir un alumno → pestaña Cafetería. Cambiar el Apellido 2 y '
         'guardar.', 'El nombre completo se rehace solo con Nombre(s), Apellido 1 y Apellido 2.'],
        ['Identificar al alumno', 'Abrir la caja → Cliente → buscar por nombre o escribir el **código** del '
         'alumno. Probar también pasando su credencial.', 'Aparece el alumno y una notificación «… · disponible '
         '$X» con su saldo, leído en ese momento.'],
        ['Venta sin alumno', 'Agregar un producto y validar sin elegir alumno.', 'Lo impide: «Falta el alumno». '
         'Toda venta va ligada a un alumno.'],
        ['Compra con monedero', 'Con un alumno que **puede comprar sin pedido**: agregar productos y pagar con '
         '«Monedero del alumno».', 'Se cobra; baja su disponible; en Movimientos aparece la compra; a los padres '
         'de familia les llega el aviso de compra por WhatsApp.'],
        ['Monedero sin saldo suficiente', 'Pagar con monedero más de lo que tiene el alumno.', 'La caja propone '
         'sólo lo que alcanza; el resto se cobra con otro método. No cierra la venta si el servidor no aparta el '
         'saldo.'],
        ['Alumno sin permiso de compra', 'Elegir un alumno al que el padre de familia le quitó «Comprar sin '
         'pedido».', 'Al elegirlo sale el aviso «Sin monedero para compras»; el monedero no se ofrece como pago.'],
        ['Pedido del padre de familia en caja', 'Elegir a un alumno con pedido para hoy.', 'Sale «Pedido por '
         'cobrar» con el folio y el total. «Cobrar ahora» carga los productos del pedido en su propio ticket.'],
        ['El pedido sólo con monedero', 'Con el pedido cargado, intentar pagar una parte en efectivo.',
         'Lo impide: «El pedido se cobra sólo con monedero…». Lo extra va en otra venta.'],
        ['Precio del pedido', 'Cambiar el precio de un producto después de que el padre de familia pidió; cobrar '
         'el pedido.', 'Se cobra al precio con que se hizo el pedido.'],
        ['Bloqueo de credencial', 'Bloquear una credencial (desde el portal o en Credenciales) y pasarla en caja.',
         'La caja la rechaza; desbloqueada vuelve a funcionar.'],
        ['Sin conexión', 'Desconectar la caja de internet; intentar cobrar con monedero.', 'No ofrece el '
         'monedero: avisa que sin conexión se cobra en efectivo o tarjeta.'],
        ['Cerrar caja no libera pedidos', 'Con un pedido de hoy sin recoger, cerrar la caja y abrir otra (cambio '
         'de turno).', 'El pedido sigue «por recoger» y su saldo sigue apartado; no sale aviso.'],
        ['Hora de fin de servicio', 'Poner la hora de fin de servicio unos minutos adelante; esperar a que pase '
         '(el proceso corre cada 15 minutos).', 'El pedido de hoy no recogido pasa a «Liberada»: el saldo vuelve '
         'y a los padres de familia les llega «pedido no recogido».'],
        ['Importar alumnos', 'Importar un CSV con dos alumnos y su padre de familia; importarlo otra vez.',
         'La primera vez los crea con su código; la segunda no duplica nada. Las filas con error se informan.'],
    ]))
    o.append(para('Las pruebas de **recarga en caja** y de **Ajustar saldo** están en la sección 4, junto con las '
                  'demás formas de recargar.'))
    o.append(note('Clientes archivados: la caja guarda la lista de clientes en el navegador. Si un alumno o padre '
                  'de familia archivado sigue apareciendo, borrar los datos del sitio en el navegador de la caja '
                  '(sin ventas pendientes de sincronizar).'))

    # ------------------------------------------------------------------ 2
    o.append(h1('2. Administración de la cafetería (portal)'))
    o.append(para('La administración da de alta a los padres de familia y a sus hijos, define cuándo se puede '
                  'pedir, revisa las recargas en línea, pausa o activa los pagos con tarjeta y da de alta la '
                  'cuenta de Mercado Pago, y configura los datos de la cafetería. Se entra en '
                  '**/admin/entrar** con el usuario de la cafetería. En computadora el menú va a la izquierda; en '
                  'celular, en una barra inferior con **padres**, **horario**, **recargas** y **cafetería**.'))
    o.append(h2('Cómo recibe el padre de familia su acceso'))
    o.append(para('**Por ahora, el padre de familia recibe su usuario y contraseña del portal por correo.** Por '
                  'eso, al darlo de alta, **captura siempre su correo**. El correo también le dice a qué número de '
                  'WhatsApp de la cafetería escribir y trae una liga para abrir el chat; ese número se captura en '
                  '**Datos de la cafetería**. Lo mismo aplica al código para recuperar la contraseña. Cuando Meta '
                  'apruebe la plantilla de acceso, también llegará por WhatsApp.'))
    o.append(h2('2.1 Pantallas'))
    o.append(table([2600, 7480], ['Pantalla', 'Qué hace'], [
        ['Padres de familia (/admin)', 'Lista con nombre, usuario, estado de la contraseña («Debe cambiarla» o «Ya '
                                       'la cambió») y altas incompletas. Avisa si falta configurar el horario de '
                                       'pedidos. Botón **Dar de alta**.'],
        ['Alta (/admin/alta)', 'Padre de familia con **Nombre(s), Apellido 1 y Apellido 2**, teléfono de 10 '
                               'dígitos y/o correo, y sus hijos **uno por uno** con «Agregar un hijo» (hasta 10): '
                               'Nombre(s), Apellido 1, Apellido 2 (opcional) y matrícula (opcional).'],
        ['Alta completa', 'Usuario, **contraseña temporal** (sólo se muestra esa vez, con «Copiar contraseña»), '
                          'canal por el que salió el acceso y el **código de caja** de cada hijo.'],
        ['Ficha del padre de familia', '**Corregir nombre**, sus hijos (agregar uno nuevo, **vincular uno que ya '
                                       'existe** para el segundo papá o mamá, desvincular), **Reenviar acceso**, '
                                       'último envío de acceso, y **Activar/Desactivar**.'],
        ['Horario de pedidos (/admin/pedidos)', 'Días con servicio, días de anticipación (**1 a 30**), hora límite '
                                                'para pedir para el mismo día, zona horaria y **días sin '
                                                'servicio** con motivo (próximos y pasados).'],
        ['Recargas (/admin/recargas)', 'Recargas en línea (Mercado Pago) con filtro por estado: revisión, '
                                       'aprobada, pendiente, acreditada, vencida y rechazada. Las de «revisión» y '
                                       'los pagos extra salen resaltados. Las recargas en caja no salen aquí: '
                                       'están en el TPV (Movimientos).'],
        ['Pagos con tarjeta (/admin/pagos)', 'Interruptor **Recargas en línea** para pausar o activar los pagos '
                                             'con tarjeta (web y WhatsApp), los datos de la **cuenta de Mercado '
                                             'Pago** que cobra (de prueba o real) y **Dar de alta o cambiar la '
                                             'cuenta** con su Access Token.'],
        ['Datos de la cafetería (/admin/cafeteria)', '**WhatsApp de la cafetería** con lada de país (va en los '
                                                     'correos con una liga al chat) y el **logo** (PNG, JPG o '
                                                     'WebP, hasta 512 KB), que sale al entrar y en los '
                                                     'encabezados.'],
    ], sz=18))
    o.append(h2('2.2 Configuración'))
    o.append(casos('2A', [
        ['Entrar', 'Entrar en /admin/entrar con el usuario de la cafetería; probar también una contraseña '
         'equivocada.', 'Entra a «Padres de familia». Con la contraseña equivocada no entra y lo dice.'],
        ['WhatsApp de la cafetería', 'Datos de la cafetería → capturar +1 956 382 5162 → Guardar. Probar '
         'también un número sin lada.', 'Se guarda y muestra la vista previa con la liga '
         'https://wa.me/19563825162?text=hola; sin lada se rechaza.'],
        ['Subir logo', 'Datos de la cafetería → elegir una imagen PNG o JPG → Subir logo.', '«Logo guardado.» El '
         'logo sale en la pantalla de entrar, en el encabezado del portal y en el menú de la administración.'],
        ['Logo inválido', 'Intentar subir un SVG, un archivo de más de 512 KB y un PDF con extensión .png.',
         'Se rechaza: «Sube una imagen PNG, JPG o WebP de hasta 512 KB.» El logo anterior no cambia.'],
        ['Quitar logo', 'Datos de la cafetería → Quitar logo; en el recuadro de confirmación tocar primero '
         'Cancelar y luego repetir con Quitar logo.', 'La confirmación sale con el estilo del portal (no el aviso '
         'gris del navegador). Cancelar no cambia nada; Quitar logo deja sólo el nombre de la cafetería.'],
        ['Horario de pedidos', 'Marcar los días con servicio (por ejemplo, lunes a viernes), anticipación 7 días '
         'y hora límite 09:00 → Guardar.', 'Se guarda. En el portal sólo se pueden elegir esos días, hasta 7 días '
         'adelante; el sábado y el domingo no salen si no tienen servicio.'],
        ['Anticipación fuera de rango', 'Capturar 0 y 31 días de anticipación.', 'Se rechaza: va del 1 al 30.'],
        ['Día sin servicio', 'Agregar una fecha próxima con motivo «Junta de maestros».', 'Aparece en «Próximos». '
         'En el portal ese día sale deshabilitado con su motivo; quitarlo lo vuelve a habilitar.'],
    ]))
    o.append(h2('2.3 Padres de familia e hijos'))
    o.append(casos('2B', [
        ['Alta con dos hijos', 'Dar de alta → Nombre(s), Apellido 1, Apellido 2, teléfono y correo → «Agregar un '
         'hijo» dos veces (uno con Apellido 2 y matrícula) → Dar de alta.', '«Alta completa» con el usuario, la '
         'contraseña temporal y el código de caja de cada hijo. Los hijos aparecen en el TPV con su nombre por '
         'partes.'],
        ['Entrega del acceso', 'Revisar el correo del padre de familia.', 'Le llega su acceso **por correo**, con '
         'el número de WhatsApp de la cafetería y la liga; al tocarla se abre el chat con «hola» escrito. La '
         'pantalla del alta dice por qué canal salió.'],
        ['Quitar un hijo antes de guardar', 'En el alta, agregar tres hijos y quitar el segundo.', 'Se quita sólo '
         'ese; los demás conservan lo escrito.'],
        ['Máximo de hijos', 'Intentar agregar un hijo número 11.', 'No lo permite: el máximo es 10 por alta.'],
        ['Validaciones del alta', 'Intentar un padre de familia sin teléfono ni correo, un hijo sin Apellido 1, un '
         'teléfono de 9 dígitos y un teléfono ya registrado.', 'Cada caso se rechaza con su mensaje y conserva '
         'lo escrito; no se crea nada en el TPV.'],
        ['Corregir nombre', 'Ficha → Corregir nombre → cambiar el Apellido 2 → Guardar nombre.', 'Se guarda en el '
         'portal y en el TPV; la ficha muestra el nombre nuevo.'],
        ['Segundo padre de familia', 'Dar de alta a la mamá (el alta pide al menos un hijo: capturar uno de '
         'prueba que no sea de los del papá); en su ficha, «Vincular un hijo que ya existe» eligiendo al papá y a '
         'cada hijo.', 'La mamá ve a los hijos del papá con el mismo saldo; los avisos llegan a los dos.'],
        ['Agregar un hijo', 'En la ficha, «Agregar un hijo nuevo» con Nombre(s) y Apellido 1.',
         'Aparece en la lista con su código; el padre de familia lo ve al entrar.'],
        ['Reenviar acceso', 'En la ficha, Reenviar acceso.', 'Se genera una contraseña temporal nueva (se muestra '
         'una sola vez); la anterior deja de servir y se cierran sus sesiones.'],
        ['Desvincular', 'Desvincular a un hijo de un padre de familia y confirmar en el recuadro.', 'Ese padre de '
         'familia deja de verlo y de recibir sus avisos; el otro lo sigue viendo. El hijo y su saldo no se borran.'],
        ['Desactivar', 'Desactivar a un padre de familia e intentar entrar con su usuario y por WhatsApp.',
         'No puede entrar ni usar WhatsApp. «Activar» lo devuelve.'],
        ['Recargas en línea', 'Después de las pruebas de la sección 4, revisar Recargas y filtrar por estado.',
         'Cada recarga con su fecha, reparto por hijo, total y estado; ninguna en «revisión».'],
        ['Administración en celular', 'Entrar a la administración desde el celular y recorrer las cinco '
         'secciones.', 'Barra inferior con padres, horario, recargas, pagos y cafetería; las tablas se leen sin '
         'desbordarse y los filtros de recargas se deslizan de lado.'],
    ]))
    o.append(h2('2.4 Pagos con tarjeta'))
    o.append(para('Aquí se pausan o activan las recargas en línea (las que el padre de familia paga con tarjeta '
                  'en la web o por WhatsApp) y se da de alta la cuenta de Mercado Pago que cobra. Las recargas en la '
                  'caja del TPV no dependen de esta pantalla. Para cambiar de cuenta se necesita su **Access '
                  'Token**: en Mercado Pago Developers, con la cuenta que va a cobrar → la aplicación de Checkout '
                  'Pro → Credenciales de producción. **Guardar cuenta ya lo comprueba con Mercado Pago**: no hay que '
                  'verificar después.'))
    o.append(casos('2C', [
        ['Pausar los pagos', 'Pagos con tarjeta → tocar «Recargas en línea» → Pausar en el recuadro de '
         'confirmación.', 'Queda en **NO** («En pausa · nadie puede recargar en línea»). En el portal del padre '
         'de familia desaparece «Recargar saldo» y abrir /recargar directo responde «Not Found»; por WhatsApp, **2** '
         'contesta «Las recargas no están disponibles por ahora». La recarga en caja sigue funcionando.'],
        ['Liga de pago con los pagos en pausa', 'Antes de pausar, armar una recarga y no pagarla; pausar y abrir '
         'esa recarga en «Mis recargas».', 'Ya no ofrece pagarla: «Los pagos con tarjeta están en pausa por '
         'ahora».'],
        ['Activar los pagos', 'Tocar «Recargas en línea» → Activar.', 'Queda en **SÍ**; vuelve «Recargar saldo» en '
         'el portal y la opción **2** de WhatsApp vuelve a permitir recargar.'],
        ['Cambiar de cuenta con los pagos activos', 'Con los pagos activos, ver «Dar de alta o cambiar la '
         'cuenta».', 'El formulario se ve deshabilitado con el aviso «Primero pausa los pagos con tarjeta (paso '
         '1)».'],
        ['Recargas en curso', 'Pausar con una recarga recién armada sin pagar (prueba anterior).', 'El formulario '
         'sigue deshabilitado: «Todavía hay recargas que la cuenta actual tiene que revisar…». Se habilita cuando '
         'ya no queda ninguna (una liga sin pagar se cierra sola en 24 horas).'],
        ['Access Token inválido', 'Con los pagos en pausa, pegar «hola» y luego el token inventado '
         '`APP_USR-1234567890-inventado`, con la contraseña correcta → Guardar cuenta.', '«Pega el Access Token completo…» y «Mercado '
         'Pago no reconoció esa credencial…». La cuenta guardada no cambia.'],
        ['Contraseña equivocada', 'Pegar un Access Token válido con una contraseña de administración equivocada.',
         '«La contraseña del administrador no es correcta.» No cambia nada; el intento cuenta para el bloqueo '
         'de 5 fallos, igual que al entrar.'],
        ['Dar de alta la cuenta', 'Con los pagos en pausa, pegar el Access Token de la cuenta que va a cobrar y '
         'la contraseña → Guardar cuenta.', '«Credenciales guardadas: cuenta real (o de prueba) …». A la '
         'izquierda salen el nombre, el número de cuenta y el tipo: **Real · cobra dinero real** o **De prueba · '
         'sólo tarjetas de prueba**. Los pagos siguen en pausa hasta activarlos.'],
        ['Activar con la cuenta real', 'Con una cuenta **real** guardada (2C.8 con el Access Token de producción '
         'de la cuenta que cobra), tocar «Recargas en línea» → Activar.', 'El recuadro avisa que se cobrará '
         'con la cuenta real. Una recarga de $50 con tarjeta real se acredita y el cobro aparece en esa cuenta '
         'de Mercado Pago (después, devolverla en Mercado Pago y descontarla con Ajustar saldo en el TPV).'],
        ['Cuenta guardada sin consultar', 'Si la cuenta se guardó antes de esta pantalla, aparece «Consultar '
         'datos de la cuenta guardada»: tocarlo.', 'Se llenan el nombre, el número y el tipo; el botón '
         'desaparece.'],
    ]))

    # ------------------------------------------------------------------ 3
    o.append(h1('3. El padre de familia: portal y WhatsApp'))
    o.append(para('El padre de familia ve el saldo de sus hijos, pide con fecha, cancela, transfiere saldo entre '
                  'hermanos, decide si pueden comprar sin pedido, bloquea credenciales y recarga con Mercado Pago. '
                  'Puede hacerlo en el **portal** o por **WhatsApp**; las dos vías usan las mismas reglas. Las '
                  'pruebas del portal conviene hacerlas en el celular.'))
    o.append(h2('3.1 Acceso'))
    o.append(casos('3A', [
        ['Primer ingreso', 'Entrar en /entrar con el teléfono (10 dígitos) o el correo y la contraseña temporal.',
         'Pide «Cambiar contraseña» antes de seguir (mínimo 8 caracteres, escrita dos veces).'],
        ['Contraseña equivocada', 'Entrar con una contraseña equivocada varias veces.', 'No entra y lo dice; tras '
         'varios intentos se bloquea un rato.'],
        ['Recuperar contraseña', '«¿Olvidaste tu contraseña?» → teléfono o correo → Mandar código.', 'Llega un '
         'código de 6 dígitos **por correo** que vence en 15 minutos; con él se fija una contraseña nueva.'],
        ['Páginas legales', 'Abrir el aviso de privacidad y las condiciones desde la pantalla de entrar.',
         'Se ven sin iniciar sesión.'],
        ['Marca de la cafetería', 'Ver la pantalla de entrar y el inicio.', 'Al entrar salen el logo y el nombre '
         'de la cafetería y, abajo, «Funciona con Cafetería by Softbot»; en el inicio, el logo y el nombre en el '
         'encabezado.'],
    ]))
    o.append(h2('3.2 Inicio y detalle del hijo'))
    o.append(casos('3B', [
        ['Inicio', 'Ver la pantalla principal.', 'Cada hijo con su disponible y los botones «Pedir» y «Ver '
         'detalle»; debajo de los hijos, «Recargar saldo», «Transferir saldo» y «Mis recargas». Un hijo sin '
         'permiso de comprar sin pedido dice «Sólo puede recoger lo que pidas».'],
        ['Detalle del hijo', 'Ver detalle de un hijo.', 'Disponible y apartado. «Pedidos» abierto; «Movimientos» '
         'cerrado hasta tocarlo, con recargas, compras, pedidos y ajustes con fecha.'],
        ['Permiso de comprar sin pedido', 'Detalle → Permisos → cambiar «Comprar sin pedido».', 'Pide '
         'confirmación; el interruptor cambia **sólo después de confirmar**. Cancelar lo deja como estaba. En la '
         'caja se refleja (prueba 1.6).'],
        ['Bloquear credencial', 'Detalle → «Con qué puede pagar» → Bloquear → confirmar. Probarla en caja y '
         'desbloquear.', 'Bloqueada: «nadie puede pagar con ella» y la caja la rechaza. Desbloqueada vuelve a '
         'pagar.'],
    ]))
    o.append(h2('3.3 Pedir'))
    o.append(casos('3C', [
        ['Elegir el día', 'Pedir → «1 · ¿Para qué día es el pedido?» → avanzar y regresar de semana con ‹ ›.',
         'Sólo se pueden elegir días con servicio y dentro de la anticipación; los pasados y los días sin '
         'servicio salen deshabilitados (con su motivo). Pasada la hora límite, hoy sale deshabilitado.'],
        ['Categorías y productos', '«2 · Elige una categoría» → sumar y restar productos; cambiar de categoría.',
         'El carrito conserva lo elegido entre categorías. La barra inferior muestra el total con **Cancelar** y '
         '**Listo**.'],
        ['Revisar y confirmar', 'Listo → revisar el pedido → Confirmar pedido.', 'Muestra para quién, el día y el '
         'total. Al confirmar sale el folio (PC-…) y el saldo queda apartado. «Seguir pidiendo» conserva el '
         'carrito.'],
        ['Seguir pidiendo y cancelar', 'En la revisión, tocar Seguir pidiendo; después repetir y tocar Cancelar.',
         'Seguir pidiendo regresa al pedido con lo elegido; Cancelar regresa al detalle del hijo sin pedir nada.'],
        ['Pedir sin saldo', 'Pedir más de lo disponible.', 'Se rechaza indicando el disponible.'],
        ['Cancelar pedido', 'Detalle → pedido por recoger → Cancelar pedido → confirmar.', 'El pedido queda '
         '«cancelado» y el saldo vuelve al disponible. Pasada la hora límite, el de hoy ya no se cancela.'],
        ['Pedido tras el fin de servicio', 'Dejar vacía por un momento la hora límite del portal (Horario de '
         'pedidos) y, pasada la hora de fin de servicio del TPV, pedir para hoy. Después volver a poner la hora '
         'límite.', 'Se rechaza: la cafetería ya terminó el servicio de hoy. Para otro día sí se puede.'],
    ]))
    o.append(h2('3.4 Transferir y recargar'))
    o.append(casos('3D', [
        ['Transferir', 'Transferir saldo → de un hijo a otro → importe → Listo → Confirmar transferencia.',
         '«Saldo transferido»: «Pasaste $X de … a …» con los nuevos disponibles. No deja pasar más de lo '
         'disponible.'],
        ['Cancelar una transferencia', 'En la confirmación, tocar Cancelar.', 'Regresa al inicio sin transferir.'],
        ['Recargar con tarjeta', 'Recargar saldo → importe para cada hijo (total de $50 a $2,000) → Listo → '
         'Pagar en línea → tarjeta de prueba con titular `APRO`.', 'La confirmación dice que **no se necesita '
         'cuenta de Mercado Pago**. Al regresar del pago sale «¡Listo! Se acreditaron $X». Si Mercado Pago tarda '
         'en confirmar, sale «Aún no vemos tu pago»; si ya lo confirmó y falta acreditarlo, «Pago recibido» con '
         'el aviso «En cuanto el pago se confirme recibirás una notificación en tu celular y el saldo quedará '
         'actualizado». Cada hijo recibe su parte y llega el aviso de WhatsApp.'],
        ['Recarga rechazada', 'Repetir con titular `FUND`.', '«Aún no vemos tu pago» con «Tu pago no se aprobó. '
         'Puedes intentar con otra tarjeta.» No se acredita nada; mientras la liga siga vigente se puede pagar '
         'con otra tarjeta.'],
        ['Cambiar o cancelar la recarga', 'En la confirmación, tocar Cambiar; después repetir y tocar Cancelar.',
         'Cambiar regresa con los importes escritos; Cancelar regresa al inicio.'],
        ['Mis recargas', 'Ver Mis recargas.', 'Cada recarga en línea con su estado y reparto. Las recargas hechas '
         'en la caja no salen aquí: se ven en los movimientos del hijo.'],
    ]))
    o.append(h2('3.5 WhatsApp'))
    o.append(para('Desde el teléfono registrado del padre de familia, escribir **hola** al +1 956 382 5162. El '
                  'bot contesta con un menú numerado; se elige escribiendo el número. **menú** regresa al inicio en '
                  'cualquier momento; **gracias**, **salir** u **8** terminan la conversación. Las palabras que hay '
                  'que escribir van en negritas.'))
    o.append(casos('3E', [
        ['Número no registrado', 'Escribir desde un teléfono que no es de ningún padre de familia.', 'Responde que '
         'el número no está registrado.'],
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
         'Llegan los avisos de compra, recarga y pedido no recogido **a todos los padres de familia** del alumno.'],
    ]))

    # ------------------------------------------------------------------ 4
    o.append(h1('4. Recarga del monedero por alumno'))
    o.append(para('Cada alumno tiene su propio monedero. Se recarga por cuatro vías, y todas terminan en el mismo '
                  'libro del TPV: el saldo nuevo se ve en el portal, en WhatsApp y en la caja en cuanto se acredita, '
                  'y a los padres de familia del alumno les llega el aviso de recarga.'))
    o.append(table([2200, 2600, 2700, 2580], ['Vía', 'Quién', 'Cómo se paga', 'Límites'], [
        ['Portal', 'Padre de familia', 'Mercado Pago (tarjeta), un pago repartido entre sus hijos; sólo con los '
         'pagos con tarjeta activos (2.4)',
         'Total de $50 a $2,000'],
        ['WhatsApp', 'Padre de familia', 'Liga de Mercado Pago, repartida entre sus hijos; sólo con los pagos '
         'con tarjeta activos (2.4)',
         'Total de $50 a $2,000'],
        ['Caja del TPV', 'Cualquier cajero, para un alumno por ticket', 'Efectivo o tarjeta (no monedero ni '
         'crédito); sólo con conexión', 'De $50 a $2,000 en pesos completos'],
        ['Ajustar saldo (TPV)', 'Gerente', 'No es un cobro: corrección con motivo (abonar o descontar)',
         'Para correcciones y devoluciones'],
    ], sz=18))
    o.append(h2('4.1 Pruebas'))
    o.append(casos('4', [
        ['Recarga en caja', 'En la caja, elegir al alumno → **Recargar saldo** (junto a Cliente; en pantalla '
         'angosta, en el menú ⋮) → $150 → Pago → Efectivo → Validar.', 'Se imprime el ticket. Sube su disponible; '
         'en el portal aparece en sus movimientos como «Recarga»; llega el aviso de recarga a sus padres de '
         'familia por WhatsApp.'],
        ['Recarga en caja con tarjeta', 'Repetir con $200 pagando con Tarjeta.', 'Igual que en efectivo.'],
        ['Recarga sin alumno', 'Sin elegir alumno, tocar Recargar saldo.', 'Lo impide: «Primero pasa la credencial '
         'del alumno.»'],
        ['Límites en caja', 'Intentar $49, $2,001 y $75.50.', 'El botón Confirmar no se activa: sólo de $50 a '
         '$2,000 en pesos completos.'],
        ['Recarga con monedero', 'Con la recarga en el ticket, intentar pagar con Monedero.', 'Lo impide: «La '
         'recarga se cobra en efectivo o tarjeta.»'],
        ['Recarga sola en su ticket', 'Con productos en el ticket, tocar Recargar saldo.', 'Lo impide: la recarga '
         'va sola; abrir una venta nueva y volver a elegir al alumno.'],
        ['Recarga sin conexión', 'Desconectar la caja de internet e intentar recargar o validar una recarga.',
         'No la cobra: avisa que sin conexión no se recarga saldo.'],
        ['Hermanos en caja', 'Recargar a dos hermanos.', 'Una venta por alumno; cada uno recibe su saldo y cada '
         'aviso dice a quién se recargó.'],
        ['Devolver una recarga', 'En la caja, Reembolso de una venta de recarga y Validar.', 'Lo impide: una '
         'recarga no se devuelve en caja.'],
        ['Devolución por el gerente', 'En el TPV, Cafetería → Ajustar saldo → descontar el importe con motivo; '
         'entregar el efectivo con una salida de caja.', 'Baja el saldo y queda como ajuste en Movimientos. Si el '
         'alumno ya gastó el saldo, no deja descontar más de lo disponible.'],
        ['Abono por el gerente', 'Ajustar saldo → abonar $50 con motivo.', 'Sube el saldo y queda como ajuste en '
         'Movimientos y en contabilidad.'],
        ['Recarga en el portal', 'Prueba 3D.3 (repartir entre dos hijos).', 'Cada hijo recibe su parte; un aviso '
         'por padre de familia con sólo sus hijos.'],
        ['Recarga por WhatsApp', 'Prueba 3E.4.', 'Igual que en el portal.'],
        ['Cuadre', 'Al final, comparar en el TPV los Movimientos de un alumno con su saldo en el portal.',
         'Coinciden: recargas de las cuatro vías, compras, pedidos y ajustes.'],
    ]))
    o.append(note('Cuando algo no salga como dice el manual, anotar: número de prueba, hora, teléfono o usuario '
                  'usado y una captura. Con eso Softbot lo localiza en los registros del TPV, del portal y de '
                  'WhatsApp.'))
    return ''.join(o)
