import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
import sqlalchemy
import pandas as pd
from datetime import datetime, timezone, timedelta
import requests

# --- 1. CONFIGURACIONES ---
TOKEN = "8868965069:AAEIiyQA2pl-W6QHadCqvA44yA-Jwhht_-k"
TOKEN_MP = "APP_USR-253719502231401-081801-8cd1c0de94adb689d058ad4865154ccb-24980278" 
ADMIN_ID = "8967444025" # ID del dueño para el /admin

bot = telebot.TeleBot(TOKEN)

# Base de datos
DB_USER = "braccoignaciocom_pancheriaitalyj"
DB_PASS = "aIlELsKLzGLnDnPI"
DB_HOST = "50.31.176.182"
DB_NAME = "braccoignaciocom_pancheria_italy_bd"

engine = sqlalchemy.create_engine(
    f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}/{DB_NAME}",
    pool_pre_ping=True, pool_recycle=1800
)


# --- 2. AUTO-CREAR Y ACTUALIZAR TABLAS ---
try:
    with engine.begin() as conn:
        # Tabla de Empleados (Nueva)
        conn.execute(sqlalchemy.text("""
        CREATE TABLE IF NOT EXISTS empleados (
            id INT AUTO_INCREMENT PRIMARY KEY,
            nombre VARCHAR(50), pin VARCHAR(10), sucursal VARCHAR(50)
        )
        """))
        
        conn.execute(sqlalchemy.text("""
        CREATE TABLE IF NOT EXISTS cajas (
            id INT AUTO_INCREMENT PRIMARY KEY,
            sucursal VARCHAR(50), empleado VARCHAR(50) DEFAULT 'Desconocido',
            fecha_apertura DATETIME, saldo_inicial FLOAT, fecha_cierre DATETIME NULL,
            ventas_efectivo FLOAT DEFAULT 0, ventas_transferencia FLOAT DEFAULT 0,
            saldo_final_esperado FLOAT DEFAULT 0, saldo_final_real FLOAT DEFAULT 0,
            diferencia FLOAT DEFAULT 0, 
            transferencia_declarada FLOAT DEFAULT 0, diferencia_transferencia FLOAT DEFAULT 0,
            estado VARCHAR(20) DEFAULT 'ABIERTA'
        )
        """))
        
        conn.execute(sqlalchemy.text("""
        CREATE TABLE IF NOT EXISTS ventas (
            id INT AUTO_INCREMENT PRIMARY KEY,
            sucursal VARCHAR(50), empleado VARCHAR(50) DEFAULT 'Desconocido', 
            fecha DATETIME, detalle TEXT, metodo_pago VARCHAR(50),
            ingreso_efectivo FLOAT, ingreso_transferencia FLOAT, costo_total FLOAT,
            estado_pago VARCHAR(20) DEFAULT 'VERIFICADO'
        )
        """))
        
        conn.execute(sqlalchemy.text("""
        CREATE TABLE IF NOT EXISTS stock_sucursales (
            sucursal VARCHAR(50) PRIMARY KEY,
            panes_comunes FLOAT DEFAULT 0, panes_queso FLOAT DEFAULT 0,
            salchichas FLOAT DEFAULT 0, cajas_aderezos INT DEFAULT 0
        )
        """))
        
        conn.execute(sqlalchemy.text("""
        CREATE TABLE IF NOT EXISTS productos (
            id INT AUTO_INCREMENT PRIMARY KEY, nombre VARCHAR(100),
            precio FLOAT, costo FLOAT
        )
        """))
        
        conn.execute(sqlalchemy.text("INSERT IGNORE INTO stock_sucursales (sucursal) VALUES ('Marasso'), ('San Francisco')"))
        
        # Empleados por defecto si está vacío
        check_emp = pd.read_sql("SELECT count(*) as cant FROM empleados", conn)
        if check_emp.iloc[0]['cant'] == 0:
            conn.execute(sqlalchemy.text("INSERT INTO empleados (nombre, pin, sucursal) VALUES ('Admin Marasso', '1111', 'Marasso'), ('Admin SF', '2222', 'San Francisco')"))
            
        check_prod = pd.read_sql("SELECT count(*) as cant FROM productos", conn)
        if check_prod.iloc[0]['cant'] == 0:
            conn.execute(sqlalchemy.text("INSERT INTO productos (nombre, precio, costo) VALUES ('Pancho Super', 2000, 800), ('Pancho Gigante', 2500, 1100), ('Pancho XL', 3000, 1400), ('Pancho de Queso', 2800, 1300)"))
except Exception as e:
    pass

# Actualizar tablas viejas por si acaso
try:
    with engine.begin() as conn:
        conn.execute(sqlalchemy.text("ALTER TABLE cajas ADD COLUMN empleado VARCHAR(50) DEFAULT 'Desconocido'"))
        conn.execute(sqlalchemy.text("ALTER TABLE ventas ADD COLUMN empleado VARCHAR(50) DEFAULT 'Desconocido'"))
except: pass


# --- 3. MEMORIA RAM (CACHÉ) ---
cajeros_activos = {} 
carritos = {}
menu_cache = []

def cargar_menu_desde_db():
    global menu_cache
    try:
        df = pd.read_sql("SELECT * FROM productos", engine)
        menu_cache = df.to_dict('records')
        print(f"✅ Menú cargado en RAM: {len(menu_cache)} productos.")
    except Exception as e:
        pass

def obtener_carrito(user_id):
    if user_id not in carritos:
        carritos[user_id] = {'items': [], 'total_venta': 0, 'total_costo': 0, 'tipo_esperado': None, 'cierre_efectivo': 0, 'montos_banco': []}
    return carritos[user_id]

# --- 4. TECLADOS DE OPERACIÓN ---
def teclado_login():
    teclado = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    teclado.add(KeyboardButton('📍 Abrir Caja: Marasso'), KeyboardButton('📍 Abrir Caja: San Francisco'))
    return teclado

def teclado_caja():
    teclado = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    teclado.add(KeyboardButton('🌭 VENDER PANCHOS'), KeyboardButton('🛒 Cobrar Pedido'))
    teclado.add(KeyboardButton('🔍 Banco (Solo ver)'), KeyboardButton('🔒 Cerrar Turno'))
    teclado.add(KeyboardButton('🔄 Actualizar Menú')) # BOTÓN NUEVO
    return teclado

def teclado_cobro():
    teclado = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    teclado.add(KeyboardButton('💵 Cobrar Efectivo'), KeyboardButton('📱 Cobrar Transferencia'))
    teclado.add(KeyboardButton('💳 Cobrar Mixto'), KeyboardButton('❌ Cancelar Venta'))
    return teclado

def teclado_confirmar_pago():
    teclado = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    teclado.add(KeyboardButton('🔄 Volver a consultar banco'))
    teclado.add(KeyboardButton('✅ Confirmar (Transf. Verificada)'))
    teclado.add(KeyboardButton('⏳ Aprobar igual (Pago Demorado)'))
    teclado.add(KeyboardButton('❌ Cancelar Venta'))
    return teclado

def generar_teclado_panchos(carrito=None):
    teclado_inline = InlineKeyboardMarkup(row_width=1)
    for p in menu_cache:
        teclado_inline.add(InlineKeyboardButton(f"➕ {p['nombre']} - ${p['precio']:,.0f}", callback_data=f"prod_{p['id']}"))
    if carrito and len(carrito['items']) > 0:
        teclado_inline.add(
            InlineKeyboardButton("➖ Quitar el Último", callback_data="quitar_ultimo"),
            InlineKeyboardButton("🗑️ Vaciar Carrito", callback_data="vaciar_carrito")
        )
    return teclado_inline

def armar_resumen_carrito(c):
    if not c['items']:
        return "🛒 **Pedido actual:**\n_Vacío_\n\nTocá un pancho para empezar:"
    resumen = "🛒 **Pedido actual:**\n"
    for item in c['items']: resumen += f"- {item['nombre']}\n"
    resumen += f"\n💰 **Total provisorio: ${c['total_venta']:,.0f}**"
    return resumen


# --- 5. ACTUALIZAR MENÚ (BOTÓN) ---
@bot.message_handler(func=lambda m: m.text == '🔄 Actualizar Menú')
def refrescar_menu(mensaje):
    cargar_menu_desde_db()
    bot.send_message(mensaje.chat.id, "✅ Menú actualizado exitosamente desde la base de datos.", reply_markup=teclado_caja())


# --- 6. SISTEMA DE LOGIN CON DB ---
@bot.message_handler(commands=['start'])
def bienvenida(mensaje):
    user_id = mensaje.chat.id
    if user_id in cajeros_activos:
        bot.send_message(user_id, f"Ya estás operando en **{cajeros_activos[user_id]['sucursal']}**.", reply_markup=teclado_caja(), parse_mode="Markdown")
    else:
        bot.send_message(user_id, "🌭 **PANCHERÍA ITALY**\nIdentificate para comenzar tu turno:", reply_markup=teclado_login(), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text.startswith('📍 Abrir Caja:'))
def pedir_pin(mensaje):
    sucursal = mensaje.text.split(': ')[1]
    query = f"SELECT * FROM cajas WHERE sucursal='{sucursal}' AND estado='ABIERTA'"
    df = pd.read_sql(query, engine)
    
    if not df.empty:
        msg = bot.send_message(mensaje.chat.id, f"🔑 La caja de **{sucursal}** ya está en curso.\nIngresá tu PIN de empleado para retomar:", parse_mode="Markdown")
        bot.register_next_step_handler(msg, validar_pin_retomar, sucursal)
    else:
        msg = bot.send_message(mensaje.chat.id, f"🔑 Ingresá tu PIN de empleado para abrir **{sucursal}**:", parse_mode="Markdown")
        bot.register_next_step_handler(msg, validar_pin_apertura, sucursal)

def validar_pin_retomar(mensaje, sucursal):
    pin_ingresado = mensaje.text.strip()
    query = f"SELECT nombre FROM empleados WHERE pin='{pin_ingresado}' AND sucursal='{sucursal}'"
    df = pd.read_sql(query, engine)
    
    if not df.empty:
        nombre_empleado = df.iloc[0]['nombre']
        cajeros_activos[mensaje.chat.id] = {'sucursal': sucursal, 'empleado': nombre_empleado}
        bot.send_message(mensaje.chat.id, f"✅ Hola, **{nombre_empleado}**. Reingresaste a la caja de **{sucursal}**.", reply_markup=teclado_caja(), parse_mode="Markdown")
    else:
        bot.send_message(mensaje.chat.id, "❌ PIN Incorrecto o no pertenecés a esta sucursal.", reply_markup=teclado_login())

def validar_pin_apertura(mensaje, sucursal):
    pin_ingresado = mensaje.text.strip()
    query = f"SELECT nombre FROM empleados WHERE pin='{pin_ingresado}' AND sucursal='{sucursal}'"
    df = pd.read_sql(query, engine)
    
    if not df.empty:
        nombre_empleado = df.iloc[0]['nombre']
        msg = bot.send_message(mensaje.chat.id, f"✅ Hola, **{nombre_empleado}**.\n💰 **¿Con cuánto dinero en EFECTIVO arrancás el turno?** (Solo números):", parse_mode="Markdown")
        bot.register_next_step_handler(msg, registrar_apertura, sucursal, nombre_empleado)
    else:
        bot.send_message(mensaje.chat.id, "❌ PIN Incorrecto o no pertenecés a esta sucursal.", reply_markup=teclado_login())

def registrar_apertura(mensaje, sucursal, nombre_empleado):
    try:
        saldo_inicial = float(mensaje.text.strip())
        fecha_ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text(f"INSERT INTO cajas (sucursal, empleado, fecha_apertura, saldo_inicial, estado) VALUES ('{sucursal}', '{nombre_empleado}', '{fecha_ahora}', {saldo_inicial}, 'ABIERTA')"))
        
        cajeros_activos[mensaje.chat.id] = {'sucursal': sucursal, 'empleado': nombre_empleado}
        bot.send_message(mensaje.chat.id, f"🌭 **Turno Abierto en {sucursal}**\nFondo inicial: ${saldo_inicial:,.0f}\nCajero: {nombre_empleado}\n\n¡Buenas ventas!", reply_markup=teclado_caja(), parse_mode="Markdown")
    except ValueError:
        msg = bot.send_message(mensaje.chat.id, "❌ Error. Escribí solo números:")
        bot.register_next_step_handler(msg, registrar_apertura, sucursal, nombre_empleado)

# --- 7. FLUJO DE VENTAS RÁPIDO ---
@bot.message_handler(func=lambda m: m.text == '🌭 VENDER PANCHOS')
def mostrar_menu_panchos(mensaje):
    user_id = mensaje.chat.id
    if user_id not in cajeros_activos: return
    c = obtener_carrito(user_id)
    bot.send_message(user_id, armar_resumen_carrito(c), reply_markup=generar_teclado_panchos(c), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith('prod_'))
def agregar_al_carrito(call):
    user_id = call.message.chat.id
    prod_id = int(call.data.split('_')[1])
    producto = next((p for p in menu_cache if p['id'] == prod_id), None)
    
    if producto:
        c = obtener_carrito(user_id)
        c['items'].append(producto)
        c['total_venta'] += producto['precio']
        c['total_costo'] += producto['costo']
        bot.answer_callback_query(call.id, f"✅ {producto['nombre']} sumado!")
        try: bot.edit_message_text(chat_id=user_id, message_id=call.message.message_id, text=armar_resumen_carrito(c), reply_markup=generar_teclado_panchos(c), parse_mode="Markdown")
        except: pass

@bot.callback_query_handler(func=lambda call: call.data == 'quitar_ultimo')
def quitar_ultimo_item(call):
    user_id = call.message.chat.id
    c = obtener_carrito(user_id)
    if c['items']:
        item_removido = c['items'].pop()
        c['total_venta'] -= item_removido['precio']
        c['total_costo'] -= item_removido['costo']
        bot.answer_callback_query(call.id, f"➖ {item_removido['nombre']} eliminado.")
        try: bot.edit_message_text(chat_id=user_id, message_id=call.message.message_id, text=armar_resumen_carrito(c), reply_markup=generar_teclado_panchos(c), parse_mode="Markdown")
        except: pass
    else:
        bot.answer_callback_query(call.id, "El carrito ya está vacío.")

@bot.callback_query_handler(func=lambda call: call.data == 'vaciar_carrito')
def vaciar_carrito_inline(call):
    user_id = call.message.chat.id
    carritos[user_id] = {'items': [], 'total_venta': 0, 'total_costo': 0, 'tipo_esperado': None, 'montos_banco': []}
    bot.answer_callback_query(call.id, "🗑️ Carrito vaciado.")
    try: bot.edit_message_text(chat_id=user_id, message_id=call.message.message_id, text=armar_resumen_carrito(carritos[user_id]), reply_markup=generar_teclado_panchos(carritos[user_id]), parse_mode="Markdown")
    except: pass

@bot.message_handler(func=lambda m: m.text == '🛒 Cobrar Pedido')
def ver_resumen_cobro(mensaje):
    user_id = mensaje.chat.id
    c = obtener_carrito(user_id)
    if not c['items']:
        bot.send_message(user_id, "⚠️ El carrito está vacío.")
        return
        
    resumen = f"🧾 **RESUMEN DE VENTA:**\n\n"
    for item in c['items']: resumen += f"🌭 {item['nombre']} (${item['precio']:,.0f})\n"
    resumen += f"\n💰 **TOTAL A COBRAR: ${c['total_venta']:,.0f}**\n\n¿Cómo paga el cliente?"
    bot.send_message(user_id, resumen, reply_markup=teclado_cobro(), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text == '❌ Cancelar Venta')
def cancelar_venta(mensaje):
    user_id = mensaje.chat.id
    carritos[user_id] = {'items': [], 'total_venta': 0, 'total_costo': 0, 'tipo_esperado': None, 'montos_banco': []}
    bot.send_message(user_id, "🗑️ Pedido cancelado.", reply_markup=teclado_caja())

# --- 8. PAGOS Y BANCO ---
def obtener_texto_banco():
    montos_encontrados = []
    try:
        headers = {'Authorization': 'Bearer ' + TOKEN_MP}
        url = "https://api.mercadopago.com/v1/payments/search?status=approved&sort=date_created&criteria=desc&limit=10"
        res = requests.get(url, headers=headers)
        datos = res.json()
        
        if 'results' in datos and len(datos['results']) > 0:
            resumen = "🏦 **ÚLTIMAS TRANSFERENCIAS (Hora Argentina):**\n\n"
            encontrados = 0
            hora_actual_utc = datetime.now(timezone.utc)
            tz_ar = timezone(timedelta(hours=-3)) 
            
            for pago in datos['results']:
                if pago.get('payment_type_id') == 'bank_transfer':
                    fecha_pago_str = pago.get('date_approved', '')
                    if fecha_pago_str:
                        try:
                            fecha_pago_utc = datetime.fromisoformat(fecha_pago_str.replace('Z', '+00:00'))
                            if (hora_actual_utc - fecha_pago_utc).total_seconds() / 3600 <= 3 and encontrados < 3:
                                fecha_pago_ar = fecha_pago_utc.astimezone(tz_ar)
                                monto = float(pago.get('transaction_amount', 0))
                                hora = fecha_pago_ar.strftime('%H:%M')
                                nombre = pago.get('payer', {}).get('first_name')
                                if not nombre: nombre = 'Cliente/Banco'
                                
                                resumen += f"🔹 {hora} hs | **${monto:,.0f}** ({nombre})\n"
                                montos_encontrados.append(monto)
                                encontrados += 1
                        except: pass
            
            if encontrados > 0: return resumen, montos_encontrados
            else: return "⚠️ No entró ninguna transferencia reciente.", []
        else: return "No se encontraron pagos aprobados.", []
    except: return "❌ Error conectando con MercadoPago.", []

@bot.message_handler(func=lambda m: m.text == '🔍 Banco (Solo ver)')
def ver_banco_solo(mensaje):
    bot.send_message(mensaje.chat.id, "⏳ Consultando MercadoPago...", reply_markup=teclado_caja())
    texto, _ = obtener_texto_banco()
    bot.send_message(mensaje.chat.id, texto, parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text in ['💵 Cobrar Efectivo', '💳 Cobrar Mixto', '📱 Cobrar Transferencia'])
def iniciar_cobro(mensaje):
    user_id = mensaje.chat.id
    if user_id not in cajeros_activos: return
    metodo = mensaje.text
    c = obtener_carrito(user_id)
    if not c.get('items'): return
    total = c['total_venta']
    
    if 'Efectivo' in metodo:
        finalizar_venta_italy(user_id, total, 0, "EFECTIVO", "VERIFICADO")
    elif 'Transferencia' in metodo:
        c['tipo_esperado'] = 'TRANSFERENCIA'
        bot.send_message(user_id, "⏳ Consultando banco...")
        texto, montos = obtener_texto_banco()
        c['montos_banco'] = montos
        bot.send_message(user_id, texto, parse_mode="Markdown")
        bot.send_message(user_id, f"💰 A cobrar: **${total:,.0f}**\n\n¿La transferencia ingresó correctamente?", reply_markup=teclado_confirmar_pago(), parse_mode="Markdown")
    elif 'Mixto' in metodo:
        msg = bot.send_message(user_id, f"💳 El total es **${total:,.0f}**.\nEscribí cuánto paga en **EFECTIVO** (solo números):", parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('❌ Cancelar Venta'))
        bot.register_next_step_handler(msg, preparar_mixto)

def preparar_mixto(mensaje):
    if mensaje.text == '❌ Cancelar Venta':
        cancelar_venta(mensaje)
        return
    user_id = mensaje.chat.id
    c = obtener_carrito(user_id)
    try:
        efectivo = float(mensaje.text.strip())
        if efectivo > c['total_venta']: efectivo = c['total_venta']
        c['efectivo_mixto'] = efectivo
        c['transferencia_mixto'] = c['total_venta'] - efectivo
        c['tipo_esperado'] = 'MIXTO'
        
        bot.send_message(user_id, "⏳ Consultando banco...")
        texto, montos = obtener_texto_banco()
        c['montos_banco'] = montos
        bot.send_message(user_id, texto, parse_mode="Markdown")
        bot.send_message(user_id, f"💳 Efectivo: ${efectivo:,.0f} | Falta Transf: **${c['transferencia_mixto']:,.0f}**\n\n¿La transferencia ingresó correctamente?", reply_markup=teclado_confirmar_pago(), parse_mode="Markdown")
    except ValueError:
        msg = bot.send_message(user_id, "Error. Escribí solo números para el efectivo:")
        bot.register_next_step_handler(msg, preparar_mixto)

@bot.message_handler(func=lambda m: m.text == '🔄 Volver a consultar banco')
def refrescar_banco(mensaje):
    user_id = mensaje.chat.id
    if user_id not in cajeros_activos: return
    c = obtener_carrito(user_id)
    
    bot.send_message(user_id, "⏳ Recargando últimos pagos...")
    texto, montos = obtener_texto_banco()
    c['montos_banco'] = montos
    bot.send_message(user_id, texto, parse_mode="Markdown")
    bot.send_message(user_id, "¿La transferencia ingresó correctamente?", reply_markup=teclado_confirmar_pago(), parse_mode="Markdown")

@bot.message_handler(func=lambda m: m.text in ['✅ Confirmar (Transf. Verificada)', '⏳ Aprobar igual (Pago Demorado)'])
def procesar_decision_humana(mensaje):
    user_id = mensaje.chat.id
    if user_id not in cajeros_activos: return
    c = obtener_carrito(user_id)
    if not c.get('items'): return
    
    monto_esperado = float(c.get('transferencia_mixto', 0)) if c.get('tipo_esperado') == 'MIXTO' else float(c['total_venta'])
    
    if 'Verificada' in mensaje.text:
        if monto_esperado not in c.get('montos_banco', []):
            bot.send_message(user_id, f"⚠️ El banco NO registra un ingreso exacto de **${monto_esperado:,.0f}** recién.\nSi el cliente ya pagó, tocá '⏳ Aprobar igual' para pasarla como pendiente, o '🔄 Volver a consultar' si está tardando.", reply_markup=teclado_confirmar_pago(), parse_mode="Markdown")
            return
        estado_pago = 'VERIFICADO'
    else:
        estado_pago = 'PENDIENTE'
    
    if c.get('tipo_esperado') == 'MIXTO':
        efectivo = c.get('efectivo_mixto', 0)
        transf = c.get('transferencia_mixto', 0)
        metodo = f"MIXTO (Ef: ${efectivo:,.0f} | Tr: ${transf:,.0f})"
        finalizar_venta_italy(user_id, efectivo, transf, metodo, estado_pago)
    elif c.get('tipo_esperado') == 'TRANSFERENCIA':
        finalizar_venta_italy(user_id, 0, c['total_venta'], "TRANSFERENCIA", estado_pago)

def finalizar_venta_italy(user_id, efectivo, transferencia, metodo_nombre, estado_pago):
    c = obtener_carrito(user_id)
    sucursal = cajeros_activos[user_id]['sucursal']
    empleado = cajeros_activos[user_id]['empleado']
    
    total = c['total_venta']
    costo = c['total_costo']
    fecha_ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    r_pan_c = 0.0; r_pan_q = 0.0; r_salch = 0.0
    detalle_db = ""
    for item in c['items']:
        nombre_prod = item['nombre'].lower()
        detalle_db += f"{item['nombre']}, "
        if 'super' in nombre_prod: r_pan_c += 0.5; r_salch += 2.0
        elif 'gigante' in nombre_prod: r_pan_c += 1.0; r_salch += 3.0
        elif 'xl' in nombre_prod: r_pan_c += 1.0; r_salch += 4.0
        elif 'queso' in nombre_prod: r_pan_q += 1.0; r_salch += 3.0

    try:
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text(f"INSERT INTO ventas (sucursal, empleado, fecha, detalle, metodo_pago, ingreso_efectivo, ingreso_transferencia, costo_total, estado_pago) VALUES ('{sucursal}', '{empleado}', '{fecha_ahora}', '{detalle_db}', '{metodo_nombre}', {efectivo}, {transferencia}, {costo}, '{estado_pago}')"))
            conn.execute(sqlalchemy.text(f"UPDATE stock_sucursales SET panes_comunes = panes_comunes - {r_pan_c}, panes_queso = panes_queso - {r_pan_q}, salchichas = salchichas - {r_salch} WHERE sucursal = '{sucursal}'"))
        
        estado_txt = "✅ VENTA VERIFICADA" if estado_pago == 'VERIFICADO' else "⏳ VENTA REGISTRADA COMO PENDIENTE"
        bot.send_message(user_id, f"{estado_txt}\nSucursal: {sucursal}", parse_mode="Markdown", reply_markup=teclado_caja())
        
        carritos[user_id] = {'items': [], 'total_venta': 0, 'total_costo': 0, 'tipo_esperado': None, 'montos_banco': []}
    except Exception as e:
        bot.send_message(user_id, f"❌ Error guardando venta: {e}")

# --- 9. CIERRE DE TURNO EN DOS PASOS ---
@bot.message_handler(func=lambda m: m.text == '🔒 Cerrar Turno')
def pedir_cierre_efectivo(mensaje):
    user_id = mensaje.chat.id
    if user_id not in cajeros_activos: return
    msg = bot.send_message(user_id, "🔒 **CIERRE DE CAJA (Paso 1 de 2)**\nContá los billetes del cajón y escribí el total exacto en **EFECTIVO**:", parse_mode="Markdown", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True).add('❌ Cancelar Cierre'))
    bot.register_next_step_handler(msg, pedir_cierre_transferencia)

def pedir_cierre_transferencia(mensaje):
    if mensaje.text == '❌ Cancelar Cierre':
        bot.send_message(mensaje.chat.id, "Cancelado.", reply_markup=teclado_caja())
        return
    user_id = mensaje.chat.id
    try:
        c = obtener_carrito(user_id)
        c['cierre_efectivo'] = float(mensaje.text.strip())
        msg = bot.send_message(user_id, "🔒 **CIERRE DE CAJA (Paso 2 de 2)**\nMirá tu cuenta de la panchería y escribí el monto ingresado hoy por **TRANSFERENCIAS** en tu turno:", parse_mode="Markdown")
        bot.register_next_step_handler(msg, procesar_cierre_final)
    except ValueError:
        msg = bot.send_message(user_id, "Error. Escribí solo números para el efectivo:")
        bot.register_next_step_handler(msg, pedir_cierre_transferencia)

def procesar_cierre_final(mensaje):
    if mensaje.text == '❌ Cancelar Cierre':
        bot.send_message(mensaje.chat.id, "Cancelado.", reply_markup=teclado_caja())
        return
    user_id = mensaje.chat.id
    sucursal = cajeros_activos[user_id]['sucursal']
    empleado = cajeros_activos[user_id]['empleado']
    c = obtener_carrito(user_id)
    efectivo_declarado = c.get('cierre_efectivo', 0)
    
    try:
        transf_declarada = float(mensaje.text.strip())
        caja = pd.read_sql(f"SELECT * FROM cajas WHERE sucursal='{sucursal}' AND estado='ABIERTA' ORDER BY id DESC LIMIT 1", engine).iloc[0]
        df_v = pd.read_sql(f"SELECT SUM(ingreso_efectivo) as tot_ef, SUM(ingreso_transferencia) as tot_tr FROM ventas WHERE sucursal='{sucursal}' AND fecha >= '{caja['fecha_apertura']}'", engine)
        
        tot_ef = df_v.iloc[0]['tot_ef'] or 0
        tot_tr = df_v.iloc[0]['tot_tr'] or 0
        esperado_efectivo = caja['saldo_inicial'] + tot_ef
        esperado_transf = tot_tr
        
        dif_efectivo = efectivo_declarado - esperado_efectivo
        dif_transf = transf_declarada - esperado_transf
        fecha_ahora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        with engine.begin() as conn:
            conn.execute(sqlalchemy.text(f"""
                UPDATE cajas SET 
                fecha_cierre='{fecha_ahora}', ventas_efectivo={tot_ef}, ventas_transferencia={tot_tr}, 
                saldo_final_esperado={esperado_efectivo}, saldo_final_real={efectivo_declarado}, 
                diferencia={dif_efectivo}, transferencia_declarada={transf_declarada}, 
                diferencia_transferencia={dif_transf}, estado='CERRADA' 
                WHERE id={caja['id']}
            """))
        
        res = f"🔒 **TURNO CERRADO - {sucursal}**\nCajero: {empleado}\n\n"
        res += "💵 **EFECTIVO:**\n"
        res += f"Esperado: ${esperado_efectivo:,.0f} | Informado: ${efectivo_declarado:,.0f}\n"
        res += "🎯 EXACTO" if dif_efectivo == 0 else f"🟢 SOBRAN: ${dif_efectivo:,.0f}" if dif_efectivo > 0 else f"🔴 FALTAN: ${abs(dif_efectivo):,.0f}"
        res += "\n\n📱 **TRANSFERENCIAS:**\n"
        res += f"Esperado: ${esperado_transf:,.0f} | Informado: ${transf_declarada:,.0f}\n"
        res += "🎯 EXACTO" if dif_transf == 0 else f"🟢 SOBRAN: ${dif_transf:,.0f}" if dif_transf > 0 else f"🔴 FALTAN: ${abs(dif_transf):,.0f}"
        
        del cajeros_activos[user_id]
        bot.send_message(user_id, res, reply_markup=teclado_login(), parse_mode="Markdown")
        
    except ValueError:
        msg = bot.send_message(user_id, "Error. Escribí solo números para las transferencias:")
        bot.register_next_step_handler(msg, procesar_cierre_final)

# ==============================================================
# ================= MODO JEFE (ADMINISTRADOR) ==================
# ==============================================================
def teclado_admin_italy():
    teclado = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    teclado.add(KeyboardButton('📊 Reporte Marasso'), KeyboardButton('📊 Reporte San Francisco'))
    teclado.add(KeyboardButton('📦 Stock Marasso'), KeyboardButton('📦 Stock San Francisco'))
    teclado.add(KeyboardButton('⬅️ Salir Modo Jefe'))
    return teclado

@bot.message_handler(commands=['admin'])
def entrar_modo_jefe(mensaje):
    if str(mensaje.chat.id) == str(ADMIN_ID):
        bot.send_message(mensaje.chat.id, "👨‍💻 **BIENVENIDO JEFE**\nSeleccioná qué local querés auditar:", reply_markup=teclado_admin_italy(), parse_mode="Markdown")
    else:
        bot.send_message(mensaje.chat.id, "⛔ Permiso denegado.")

@bot.message_handler(func=lambda m: m.text == '⬅️ Salir Modo Jefe')
def salir_jefe(mensaje):
    bot.send_message(mensaje.chat.id, "Saliendo...", reply_markup=teclado_login())

@bot.message_handler(func=lambda m: m.text.startswith('📊 Reporte '))
def reporte_jefe_sucursal(mensaje):
    if str(mensaje.chat.id) != str(ADMIN_ID): return
    sucursal = mensaje.text.replace('📊 Reporte ', '')
    try:
        caja = pd.read_sql(f"SELECT * FROM cajas WHERE sucursal='{sucursal}' AND estado='ABIERTA' ORDER BY id DESC LIMIT 1", engine)
        if caja.empty:
            bot.send_message(mensaje.chat.id, f"No hay turno abierto en {sucursal}.")
            return
            
        fecha_ap = caja.iloc[0]['fecha_apertura']
        empleado = caja.iloc[0]['empleado']
        df_v = pd.read_sql(f"SELECT SUM(ingreso_efectivo) as ef, SUM(ingreso_transferencia) as tr, SUM(costo_total) as costo FROM ventas WHERE sucursal='{sucursal}' AND fecha >= '{fecha_ap}'", engine)
        df_pendientes = pd.read_sql(f"SELECT sum(ingreso_transferencia) as pend FROM ventas WHERE sucursal='{sucursal}' AND fecha >= '{fecha_ap}' AND estado_pago='PENDIENTE'", engine)
        
        ef = df_v.iloc[0]['ef'] or 0
        tr = df_v.iloc[0]['tr'] or 0
        costo = df_v.iloc[0]['costo'] or 0
        pend = df_pendientes.iloc[0]['pend'] or 0
        neto = (ef + tr) - costo
        
        res = f"📊 **REPORTE EN VIVO: {sucursal.upper()}**\nCajero: {empleado}\n\n"
        res += f"Ingresos Físicos: ${ef:,.0f}\nIngresos MP: ${tr:,.0f}\n"
        res += f"💰 **FACTURACIÓN: ${(ef+tr):,.0f}**\n\n"
        res += f"📉 Costo Mercadería: ${costo:,.0f}\n✨ **GANANCIA NETA TURNO: ${neto:,.0f}**\n\n"
        if pend > 0:
            res += f"⚠️ **¡ATENCIÓN!** Hay **${pend:,.0f}** marcados como 'Demorados/Pendientes' por los empleados."
        bot.send_message(mensaje.chat.id, res, parse_mode="Markdown")
    except Exception as e:
        bot.send_message(mensaje.chat.id, f"Error: {e}")

@bot.message_handler(func=lambda m: m.text.startswith('📦 Stock '))
def stock_jefe_sucursal(mensaje):
    if str(mensaje.chat.id) != str(ADMIN_ID): return
    sucursal = mensaje.text.replace('📦 Stock ', '')
    try:
        s = pd.read_sql(f"SELECT * FROM stock_sucursales WHERE sucursal='{sucursal}'", engine).iloc[0]
        res = f"📦 **STOCK EN {sucursal.upper()}**\n\n"
        res += f"🥖 Panes Comunes: {s['panes_comunes']:.1f}\n"
        res += f"🧀 Panes de Queso: {s['panes_queso']:.1f}\n"
        res += f"🌭 Salchichas sueltas: {s['salchichas']:.1f}\n"
        res += f"📦 Cajas Aderezos cerradas: {s['cajas_aderezos']}\n"
        bot.send_message(mensaje.chat.id, res, parse_mode="Markdown")
    except:
        bot.send_message(mensaje.chat.id, "Error leyendo stock.")

# Ejecutar carga de menú inicial y arrancar
cargar_menu_desde_db()
bot.infinity_polling()