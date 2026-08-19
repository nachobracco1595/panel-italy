import streamlit as st
import pandas as pd
import sqlalchemy
import altair as alt # Librería para gráficos lindos

# --- 1. CONFIGURACIÓN DEL SERVIDOR ---
DB_USER = st.secrets["DB_USER"]
DB_PASS = st.secrets["DB_PASS"]
DB_HOST = st.secrets["DB_HOST"]
DB_NAME = st.secrets["DB_NAME"]

@st.cache_resource
def get_connection():
    return sqlalchemy.create_engine(
        f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}/{DB_NAME}",
        pool_pre_ping=True, pool_recycle=1800
    )

engine = get_connection()

try:
    with engine.begin() as conn:
        conn.execute(sqlalchemy.text("""
        CREATE TABLE IF NOT EXISTS costos_base (
            id INT PRIMARY KEY,
            costo_pan_comun FLOAT DEFAULT 0, costo_pan_queso FLOAT DEFAULT 0,
            costo_salchicha FLOAT DEFAULT 0, costo_topping FLOAT DEFAULT 0,
            costo_descartable FLOAT DEFAULT 0
        )
        """))
        check_costos = pd.read_sql("SELECT count(*) as cant FROM costos_base", conn)
        if check_costos.iloc[0]['cant'] == 0:
            conn.execute(sqlalchemy.text("INSERT INTO costos_base (id) VALUES (1)"))
except: pass

# --- 2. FUNCIONES DE LECTURA (Caché inteligente para evitar parpadeos) ---
@st.cache_data(ttl=60, show_spinner=False) # show_spinner=False quita la animación de carga molesta
def load_ventas(fecha_desde, fecha_hasta, sucursal):
    if sucursal == "Ambas":
        query = f"SELECT * FROM ventas WHERE DATE(fecha) >= '{fecha_desde}' AND DATE(fecha) <= '{fecha_hasta}'"
    else:
        query = f"SELECT * FROM ventas WHERE DATE(fecha) >= '{fecha_desde}' AND DATE(fecha) <= '{fecha_hasta}' AND sucursal='{sucursal}'"
    return pd.read_sql(query, engine)

@st.cache_data(ttl=60, show_spinner=False)
def load_cajas(sucursal):
    if sucursal == "Ambas":
        return pd.read_sql("SELECT id, sucursal, empleado, fecha_apertura, saldo_inicial, fecha_cierre, ventas_efectivo, ventas_transferencia, saldo_final_esperado, saldo_final_real, diferencia, transferencia_declarada, diferencia_transferencia, estado FROM cajas ORDER BY id DESC LIMIT 30", engine)
    else:
        return pd.read_sql(f"SELECT id, sucursal, empleado, fecha_apertura, saldo_inicial, fecha_cierre, ventas_efectivo, ventas_transferencia, saldo_final_esperado, saldo_final_real, diferencia, transferencia_declarada, diferencia_transferencia, estado FROM cajas WHERE sucursal='{sucursal}' ORDER BY id DESC LIMIT 30", engine)

@st.cache_data(ttl=30, show_spinner=False)
def load_stock():
    return pd.read_sql("SELECT * FROM stock_sucursales", engine)

@st.cache_data(ttl=30, show_spinner=False)
def load_empleados():
    return pd.read_sql("SELECT id, nombre, pin, sucursal FROM empleados", engine)

@st.cache_data(ttl=30, show_spinner=False)
def load_costos_base():
    return pd.read_sql("SELECT * FROM costos_base WHERE id=1", engine).iloc[0]

# --- 3. SEGURIDAD Y DISEÑO ---
st.set_page_config(page_title="Panchería Italy", layout="wide", page_icon="🌭", initial_sidebar_state="expanded")

# Pequeño truco de CSS para ocultar bordes feos y hacer todo más "limpio"
st.markdown("""
    <style>
    div[data-testid="stMetricValue"] {font-size: 2rem; color: #1E3A8A;}
    div[data-testid="stMetricLabel"] {font-size: 1.1rem; color: #4B5563;}
    </style>
""", unsafe_allow_html=True)

def check_password():
    if "password_correct" not in st.session_state:
        st.markdown("<h2 style='text-align: center; color: #e63946;'>🔒 Acceso Gerencial</h2>", unsafe_allow_html=True)
        st.text_input("Ingresá tu contraseña secreta:", type="password", key="password")
        if st.session_state.get("password") == "adminitaly":
            st.session_state["password_correct"] = True
            st.rerun()
        return False
    return st.session_state["password_correct"]

if check_password():
    # Menú lateral amigable
    with st.sidebar:
        st.image("https://cdn-icons-png.flaticon.com/512/3014/3014491.png", width=100) # Un loguito simpático de pancho
        st.title("Panchería Italy")
        menu = st.radio("¿Qué querés ver hoy?", ["📈 Panel Principal (Plata)", "✅ Auditar Pagos", "📦 Contar Stock", "💲 Mis Precios y Recetas", "👥 Mis Empleados"])
        
        st.markdown("---")
        if st.button("🔄 Actualizar Todo Ahora", use_container_width=True):
            st.cache_data.clear()
            st.rerun()
        st.caption("Los datos se cargan suavemente para no trabar tu compu.")

    # ==========================================
    # SECCIÓN A: PANEL PRINCIPAL (VISUAL Y HERMOSO)
    # ==========================================
    if menu == "📈 Panel Principal (Plata)":
        st.markdown("## 💰 ¿Cómo venimos con la plata?")
        
        # Filtros en una barra limpia y discreta
        with st.container(border=True):
            col_f1, col_f2, col_f3 = st.columns([2, 1, 1])
            filtro_sucursal = col_f1.selectbox("🏢 Elegí el local a revisar:", ["Ambas", "Marasso", "San Francisco"])
            hoy = pd.Timestamp.now().date()
            fecha_desde = col_f2.date_input("🗓️ Desde el día:", value=hoy)
            fecha_hasta = col_f3.date_input("🗓️ Hasta el día:", value=hoy)
            
        df_ventas = load_ventas(fecha_desde, fecha_hasta, filtro_sucursal)
        
        if df_ventas.empty:
            st.warning("📭 No hay ventas registradas en esos días. ¡Avisale a los chicos que ofrezcan más combos!")
        else:
            tot_efectivo = df_ventas['ingreso_efectivo'].sum()
            tot_transf = df_ventas['ingreso_transferencia'].sum()
            total_caja = tot_efectivo + tot_transf
            costo_mercaderia = df_ventas['costo_total'].sum()
            ganancia_neta = total_caja - costo_mercaderia
            cant_ventas = len(df_ventas)
            
            # Semáforo de salud
            margen_porcentaje = (ganancia_neta / total_caja * 100) if total_caja > 0 else 0
            if margen_porcentaje >= 50: emoji_salud, color = "🌟 EXCELENTE", "green"
            elif margen_porcentaje >= 35: emoji_salud, color = "👍 BIEN", "orange"
            else: emoji_salud, color = "⚠️ PELIGRO (Costos Altos)", "red"

            # 1. TARJETAS GIGANTES (Métricas principales)
            col_kpi1, col_kpi2, col_kpi3 = st.columns(3)
            with col_kpi1:
                st.info("💵 **PLATA TOTAL QUE ENTRÓ**")
                st.markdown(f"<h1 style='color: #1E3A8A;'>${total_caja:,.0f}</h1>", unsafe_allow_html=True)
            with col_kpi2:
                st.warning("🛒 **GASTO EN MERCADERÍA**")
                st.markdown(f"<h1 style='color: #B45309;'>${costo_mercaderia:,.0f}</h1>", unsafe_allow_html=True)
            with col_kpi3:
                st.success("✨ **TU GANANCIA LIMPIA**")
                st.markdown(f"<h1 style='color: #047857;'>${ganancia_neta:,.0f}</h1>", unsafe_allow_html=True)

            st.markdown(f"<p style='text-align: right; color: {color};'><b>Salud del Negocio: {emoji_salud}</b> (Margen: {margen_porcentaje:.1f}%)</p>", unsafe_allow_html=True)

            # 2. PESTAÑAS (Para no amontonar información)
            tab1, tab2, tab3 = st.tabs(["📊 Gráfico de Ventas", "💳 ¿Cómo pagaron?", "📆 Turnos de los Chicos"])
            
            with tab1:
                st.write("Mira visualmente la diferencia entre lo que cobraste y lo que te costó la comida.")
                # Gráfico lindo y suave
                datos_grafico = pd.DataFrame({
                    "Concepto": ["Ingresos Totales", "Costo Mercadería", "Ganancia Neta"],
                    "Monto ($)": [total_caja, costo_mercaderia, ganancia_neta]
                })
                chart = alt.Chart(datos_grafico).mark_bar(cornerRadiusTopLeft=5, cornerRadiusTopRight=5).encode(
                    x=alt.X('Concepto', sort=None, title=""),
                    y=alt.Y('Monto ($)', title="Plata"),
                    color=alt.Color('Concepto', scale=alt.Scale(range=['#1E3A8A', '#B45309', '#047857']), legend=None),
                    tooltip=['Monto ($)']
                ).properties(height=300)
                st.altair_chart(chart, use_container_width=True)

            with tab2:
                col_p1, col_p2 = st.columns(2)
                col_p1.metric("Billetes Físicos en Caja", f"${tot_efectivo:,.0f}")
                col_p2.metric("Plata en MercadoPago", f"${tot_transf:,.0f}")
                st.caption("Si ves muchas transferencias, acordate de pasar por la pestaña 'Auditar Pagos' para asegurarte que nadie te mintió.")

            with tab3:
                df_cajas = load_cajas(filtro_sucursal)
                if not df_cajas.empty:
                    st.write("Acá controlás si tus empleados entregaron la caja en 0, o si se equivocaron dando vueltos.")
                    def color_rojo_faltante(val): return 'color: red' if val < 0 else 'color: green' if val > 0 else ''
                    
                    # CORREGIDO: applymap ahora es map
                    st.dataframe(df_cajas[['fecha_apertura', 'sucursal', 'empleado', 'diferencia', 'diferencia_transferencia']].style.map(color_rojo_faltante, subset=['diferencia', 'diferencia_transferencia']).format({
                        'diferencia': '${:,.0f} efvo',
                        'diferencia_transferencia': '${:,.0f} MP'
                    }), hide_index=True, use_container_width=True)

    # ==========================================
    # SECCIÓN B: AUDITORÍA DE TRANSFERENCIAS
    # ==========================================
    elif menu == "✅ Auditar Pagos":
        st.header("⏳ Revisión de Transferencias Sospechosas")
        st.write("Acá aparecen los pagos que los chicos marcaron como 'Demorados'. Revisá tu MercadoPago y aprobalos si la plata finalmente entró.")
        
        query_pendientes = "SELECT * FROM ventas WHERE estado_pago = 'PENDIENTE' ORDER BY fecha DESC"
        df_pendientes = pd.read_sql(query_pendientes, engine)
        
        if df_pendientes.empty:
            st.success("🎉 ¡Paz mental! No tenés ninguna transferencia pendiente. Todo lo que vendieron, se cobró.")
        else:
            for index, row in df_pendientes.iterrows():
                with st.container(border=True):
                    c1, c2, c3 = st.columns([3, 2, 2])
                    c1.write(f"📅 **{row['fecha']}** (Local: {row['sucursal']})")
                    c2.markdown(f"<h3 style='color: #e63946; margin:0;'>${row['ingreso_transferencia']:,.0f}</h3>", unsafe_allow_html=True)
                    if c3.button(f"✅ Aprobar y Guardar", key=f"btn_verif_{row['id']}", use_container_width=True):
                        with engine.begin() as conn:
                            conn.execute(sqlalchemy.text(f"UPDATE ventas SET estado_pago='VERIFICADO' WHERE id={row['id']}"))
                        st.cache_data.clear()
                        st.rerun()

    # ==========================================
    # SECCIÓN C: STOCK
    # ==========================================
    elif menu == "📦 Contar Stock":
        st.header("📦 Ajuste Rápido de Inventario")
        df_stock = load_stock()
        
        for index, s in df_stock.iterrows():
            with st.expander(f"🏢 GESTIONAR SUCURSAL: {s['sucursal']}", expanded=True):
                st.write("Si hiciste recuento en el local, anotá acá los números reales:")
                with st.form(f"form_stock_{s['sucursal']}"):
                    col1, col2, col3, col4 = st.columns(4)
                    n_pc = col1.number_input("Panes Comunes (Paquetes)", value=float(s['panes_comunes']), step=1.0)
                    n_pq = col2.number_input("Panes Queso", value=float(s['panes_queso']), step=1.0)
                    n_sal = col3.number_input("Salchichas", value=float(s['salchichas']), step=1.0)
                    n_ade = col4.number_input("Cajas Aderezos cerradas", value=int(s['cajas_aderezos']), step=1)
                    
                    if st.form_submit_button("💾 Guardar Conteo Final", type="primary"):
                        with engine.begin() as conn:
                            conn.execute(sqlalchemy.text(f"UPDATE stock_sucursales SET panes_comunes={n_pc}, panes_queso={n_pq}, salchichas={n_sal}, cajas_aderezos={n_ade} WHERE sucursal='{s['sucursal']}'"))
                        st.cache_data.clear()
                        st.rerun()

    # ==========================================
    # SECCIÓN D: COSTOS, RECETAS Y PRECIOS
    # ==========================================
    elif menu == "💲 Mis Precios y Recetas":
        st.header("🧠 El Cerebro del Negocio")
        st.write("Acá le decís al sistema cuánto te cuesta la mercadería y él se encarga de calcularte si estás ganando o perdiendo.")
        
        costos = load_costos_base()
        
        with st.expander("💸 1. Anotar mis compras al Proveedor (Costos)", expanded=True):
            with st.form("form_costos_base"):
                st.write("¿A cuánto pagaste la unidad de cada cosa esta semana?")
                c1, c2, c3, c4, c5 = st.columns(5)
                c_pan = c1.number_input("🥖 1 Pan Común ($)", value=float(costos['costo_pan_comun']), step=50.0)
                c_panq = c2.number_input("🧀 1 Pan Queso ($)", value=float(costos['costo_pan_queso']), step=50.0)
                c_sal = c3.number_input("🌭 1 Salchicha ($)", value=float(costos['costo_salchicha']), step=50.0)
                c_top = c4.number_input("🥫 Salsas x Pancho ($)", value=float(costos['costo_topping']), step=50.0)
                c_desc = c5.number_input("🥡 Caja/Bandeja ($)", value=float(costos['costo_descartable']), step=50.0)
                
                if st.form_submit_button("⚖️ Guardar Costos y Recalcular Menú"):
                    with engine.begin() as conn:
                        conn.execute(sqlalchemy.text(f"UPDATE costos_base SET costo_pan_comun={c_pan}, costo_pan_queso={c_panq}, costo_salchicha={c_sal}, costo_topping={c_top}, costo_descartable={c_desc} WHERE id=1"))
                        
                        # Magia oculta: recostea automáticamente
                        costo_super = (c_pan * 0.5) + (c_sal * 2) + c_top + c_desc
                        costo_gigante = (c_pan * 1.0) + (c_sal * 3) + c_top + c_desc
                        costo_xl = (c_pan * 1.0) + (c_sal * 4) + c_top + c_desc
                        costo_queso = (c_panq * 1.0) + (c_sal * 3) + c_top + c_desc
                        
                        conn.execute(sqlalchemy.text(f"UPDATE productos SET costo={costo_super} WHERE nombre LIKE '%Super%'"))
                        conn.execute(sqlalchemy.text(f"UPDATE productos SET costo={costo_gigante} WHERE nombre LIKE '%Gigante%'"))
                        conn.execute(sqlalchemy.text(f"UPDATE productos SET costo={costo_xl} WHERE nombre LIKE '%XL%'"))
                        conn.execute(sqlalchemy.text(f"UPDATE productos SET costo={costo_queso} WHERE nombre LIKE '%Queso%'"))
                        
                    st.cache_data.clear()
                    st.success("✅ Todo recalculado.")
                    st.rerun()
                
        st.subheader("📝 2. Mi Pizarra de Precios al Público")
        margen_deseado = st.slider("🎯 Tu meta: ¿Qué porcentaje de ganancia querés sacarle a cada pancho?", min_value=0, max_value=300, value=150, step=10)
        
        df_prod = pd.read_sql("SELECT id, nombre, costo, precio FROM productos", engine)
        df_prod['PRECIO IDEAL'] = df_prod['costo'] * (1 + (margen_deseado / 100))
        df_prod['Te queda limpio ($)'] = df_prod['precio'] - df_prod['costo']
        df_prod = df_prod[['id', 'nombre', 'costo', 'PRECIO IDEAL', 'precio', 'Te queda limpio ($)']]
        
        st.write("Si el 'Precio Real' está muy por debajo del Ideal, pensá en subirlo. **Solo podés editar la columna Precio Real.**")
        
        edited_df = st.data_editor(
            df_prod, hide_index=True, use_container_width=True, 
            disabled=["id", "nombre", "costo", "PRECIO IDEAL", "Te queda limpio ($)"],
            column_config={
                "costo": st.column_config.NumberColumn("Te cuesta hacerlo", format="$%.0f"),
                "PRECIO IDEAL": st.column_config.NumberColumn(f"Sugerido (+{margen_deseado}%)", format="$%.0f"),
                "precio": st.column_config.NumberColumn("✏️ PRECIO REAL", format="$%.0f"),
                "Te queda limpio ($)": st.column_config.NumberColumn("Bolsillo Real", format="$%.0f")
            }
        )
        
        if st.button("💾 Mandar precios nuevos a los locales", type="primary"):
            with engine.begin() as conn:
                for index, row in edited_df.iterrows():
                    conn.execute(sqlalchemy.text(f"UPDATE productos SET precio={row['precio']} WHERE id={row['id']}"))
            st.cache_data.clear()
            st.rerun()

    # ==========================================
    # SECCIÓN E: EMPLEADOS
    # ==========================================
    elif menu == "👥 Mis Empleados":
        st.header("👥 Gestión de Accesos (Los Chicos)")
        st.write("Acá controlás quién puede abrir la caja de Telegram y en qué sucursal.")
        
        df_emp = load_empleados()
        edited_emp = st.data_editor(df_emp, hide_index=True, use_container_width=True)
        
        if st.button("💾 Guardar Modificaciones"):
            with engine.begin() as conn:
                for index, row in edited_emp.iterrows():
                    conn.execute(sqlalchemy.text(f"UPDATE empleados SET nombre='{row['nombre']}', pin='{row['pin']}', sucursal='{row['sucursal']}' WHERE id={row['id']}"))
            st.cache_data.clear()
            st.rerun()
            
        with st.expander("➕ Dar de alta a un empleado nuevo"):
            with st.form("form_nuevo_emp"):
                n_nom = st.text_input("Nombre de pila")
                n_pin = st.text_input("PIN (Contraseña numérica)", max_chars=8)
                n_suc = st.selectbox("¿En qué local va a trabajar?", ["Marasso", "San Francisco"])
                if st.form_submit_button("Crear Acceso"):
                    with engine.begin() as conn:
                        conn.execute(sqlalchemy.text(f"INSERT INTO empleados (nombre, pin, sucursal) VALUES ('{n_nom}', '{n_pin}', '{n_suc}')"))
                    st.cache_data.clear()
                    st.rerun()
