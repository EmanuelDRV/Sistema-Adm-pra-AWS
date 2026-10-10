import os
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import urllib.parse
from sqlalchemy import text
from sqlalchemy import func
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy import or_
import re

app = Flask(__name__)

# Obtenemos la URL de la base de datos desde las variables de entorno de Fly.io
# Si no la encuentra (porque estás en tu PC local), usará SQLite por defecto.
database_url = os.getenv('DATABASE_URL', 'sqlite:///panel_ventas.db')

# IMPORTANTE: SQLAlchemy requiere que el prefijo sea 'postgresql://' en lugar de 'postgres://'
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Pool de conexiones: solo para PostgreSQL (en SQLite local estas opciones
# no aplican y pueden romper el arranque, por eso se omiten ahí).
if not database_url.startswith('sqlite'):
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True,   # detecta conexiones muertas antes de usarlas
        'pool_recycle': 280,     # renueva conexiones antes de que el LB de Fly las corte
        'pool_size': 10,
        'max_overflow': 5,
    }

# También es buena práctica ocultar la secret_key en producción
app.secret_key = os.getenv('SECRET_KEY', 'clave_secreta_super_segura_local')

db = SQLAlchemy(app)

@app.template_filter('dinero')
def formato_dinero(valor):
    try:
        if valor is None:
            return "0"
        s = f"{float(valor):,.2f}"
        return s[:-3] if s.endswith('.00') else s
    except (ValueError, TypeError):
        return str(valor)

# --- MODELOS DE LA BASE DE DATOS ---

class Vendedor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(50), nullable=False)
    contacto = db.Column(db.String(50))
    ventas = db.relationship('Venta', backref='vendedor', lazy=True)

class Cliente(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    telefono_original = db.Column(db.String(50), nullable=False) 
    telefono_limpio = db.Column(db.String(50), nullable=False)   
    fecha_registro = db.Column(db.DateTime, default=datetime.utcnow)
    ventas = db.relationship('Venta', backref='cliente', lazy=True)

class Cuenta(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(50), nullable=False)
    plataforma = db.Column(db.String(50), nullable=False)
    correo = db.Column(db.String(100), nullable=False)
    contrasena = db.Column(db.String(100), nullable=False)
    perfiles_maximos = db.Column(db.Integer, nullable=False, default=1)
    perfiles_ocupados = db.Column(db.Integer, default=0)
    estado = db.Column(db.String(20), default='Activo')
    
    # NUEVOS CAMPOS DE INVERSIÓN
    costo = db.Column(db.Float, default=0.0)
    moneda_costo = db.Column(db.String(10), default='USDT')
    
    # Quién compró la cuenta y vigencia
    vendedor_id = db.Column(db.Integer, db.ForeignKey('vendedor.id'), nullable=True)
    comprador = db.relationship('Vendedor', foreign_keys=[vendedor_id], backref='cuentas_compradas', lazy=True)
    fecha_compra = db.Column(db.DateTime, default=datetime.utcnow)
    dias_duracion = db.Column(db.Integer, default=30)
    fecha_vencimiento_cuenta = db.Column(db.DateTime, nullable=True)
    
    # Renovación de la cuenta
    vendedor_renovacion_id = db.Column(db.Integer, db.ForeignKey('vendedor.id'), nullable=True)
    renovador = db.relationship('Vendedor', foreign_keys=[vendedor_renovacion_id], backref='cuentas_renovadas', lazy=True)
    ultima_renovacion_fecha = db.Column(db.DateTime, nullable=True)
    ultimo_costo_renovacion = db.Column(db.Float, default=0.0)
    ultima_moneda_renovacion = db.Column(db.String(10), default='USDT')
    
    ventas = db.relationship('Venta', backref='cuenta', lazy=True)

    __table_args__ = (
        db.UniqueConstraint('plataforma', 'correo', name='uq_plataforma_correo'),
    )

class Venta(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=False)
    cuenta_id = db.Column(db.Integer, db.ForeignKey('cuenta.id'), nullable=False)
    vendedor_id = db.Column(db.Integer, db.ForeignKey('vendedor.id'), nullable=False)
    
    nombre_perfil = db.Column(db.String(50))
    pin = db.Column(db.String(10))
    
    fecha_inicio = db.Column(db.DateTime, default=datetime.utcnow)
    dias_suscripcion = db.Column(db.Integer, nullable=False)
    fecha_vencimiento = db.Column(db.DateTime, nullable=False)
    
    precio = db.Column(db.Float, nullable=False)
    moneda = db.Column(db.String(10), nullable=False) 
    estado_pago = db.Column(db.String(20), default='Pagado')
    estado = db.Column(db.String(20), default='Activa')

class Plantilla(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tipo = db.Column(db.String(50), unique=True, nullable=False) 
    mensaje = db.Column(db.Text, nullable=False)

# --- SISTEMA DE MIGRACIONES IDEMPOTENTES ---
# Para agregar una columna en el futuro:
#   1. Agrégala al modelo (ej. col = db.Column(db.String(50)))
#   2. Agrega una línea en MIGRACIONES (abajo) con su ALTER
# Se aplica sola al arrancar, en SQLite local y PostgreSQL de producción,
# y es seguro repetirla (no rompe si la columna ya existe).

def _es_sqlite():
    return 'sqlite' in db.engine.url.drivername

def tabla_existe(tabla):
    if _es_sqlite():
        return db.session.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"), {"t": tabla}).fetchone() is not None
    return db.session.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": tabla}).scalar()

def columna_existe(tabla, columna):
    if _es_sqlite():
        filas = db.session.execute(text(f"PRAGMA table_info({tabla})")).fetchall()
        return any(fila[1] == columna for fila in filas)
    return db.session.execute(text(
        "SELECT 1 FROM information_schema.columns WHERE table_name=:t AND column_name=:c"
    ), {"t": tabla, "c": columna}).fetchone() is not None

def agregar_columna(tabla, columna, definicion):
    if columna_existe(tabla, columna):
        return False
    db.session.execute(text(f"ALTER TABLE {tabla} ADD COLUMN {columna} {definicion}"))
    return True

MIGRACIONES = [
    ("venta.estado", lambda: agregar_columna('venta', 'estado', "VARCHAR(20) DEFAULT 'Activa'")),
    ("cuenta.vendedor_id", lambda: agregar_columna('cuenta', 'vendedor_id', 'INTEGER')),
    ("cuenta.fecha_compra", lambda: agregar_columna('cuenta', 'fecha_compra', 'DATETIME')),
    ("cuenta.dias_duracion", lambda: agregar_columna('cuenta', 'dias_duracion', 'INTEGER DEFAULT 30')),
    ("cuenta.fecha_vencimiento_cuenta", lambda: agregar_columna('cuenta', 'fecha_vencimiento_cuenta', 'DATETIME')),
    ("cuenta.vendedor_renovacion_id", lambda: agregar_columna('cuenta', 'vendedor_renovacion_id', 'INTEGER')),
    ("cuenta.ultima_renovacion_fecha", lambda: agregar_columna('cuenta', 'ultima_renovacion_fecha', 'DATETIME')),
    ("cuenta.ultimo_costo_renovacion", lambda: agregar_columna('cuenta', 'ultimo_costo_renovacion', 'FLOAT DEFAULT 0.0')),
    ("cuenta.ultima_moneda_renovacion", lambda: agregar_columna('cuenta', 'ultima_moneda_renovacion', "VARCHAR(10) DEFAULT 'USDT'")),
]

def ejecutar_migraciones():
    aplicadas = 0
    for nombre, migracion in MIGRACIONES:
        try:
            if migracion():
                aplicadas += 1
        except Exception:
            db.session.rollback()
            print(f"[migraciones] ERROR en '{nombre}' (se omite, revisar manualmente)")
    if aplicadas:
        db.session.commit()
        print(f"[migraciones] {aplicadas} cambio(s) de schema aplicado(s)")

# Índices para acelerar los filtros, ordenamientos y JOINs que se usan a diario.
# CREATE INDEX IF NOT EXISTS funciona igual en SQLite y PostgreSQL, y es seguro
# ejecutarlo en cada arranque (si el índice ya existe, no hace nada).
INDICES = [
    "CREATE INDEX IF NOT EXISTS ix_venta_estado ON venta (estado)",
    "CREATE INDEX IF NOT EXISTS ix_venta_fecha_vencimiento ON venta (fecha_vencimiento)",
    "CREATE INDEX IF NOT EXISTS ix_venta_fecha_inicio ON venta (fecha_inicio)",
    "CREATE INDEX IF NOT EXISTS ix_venta_cuenta_id ON venta (cuenta_id)",
    "CREATE INDEX IF NOT EXISTS ix_venta_cliente_id ON venta (cliente_id)",
    "CREATE INDEX IF NOT EXISTS ix_venta_vendedor_id ON venta (vendedor_id)",
    "CREATE INDEX IF NOT EXISTS ix_cuenta_estado ON cuenta (estado)",
    "CREATE INDEX IF NOT EXISTS ix_cuenta_fecha_vencimiento_cuenta ON cuenta (fecha_vencimiento_cuenta)",
    "CREATE INDEX IF NOT EXISTS ix_cuenta_vendedor_id ON cuenta (vendedor_id)",
    "CREATE INDEX IF NOT EXISTS ix_cliente_telefono_limpio ON cliente (telefono_limpio)",
]

def ejecutar_indices():
    for ddl in INDICES:
        try:
            db.session.execute(text(ddl))
        except Exception:
            db.session.rollback()
            print(f"[indices] ERROR al crear índice: {ddl.split(' ON ')[0]}... (se omite)")
    db.session.commit()

def reconciliar_slots():
    # Reconciliación en UNA sola sentencia (sin bucle de COUNTs por cuenta).
    # Recalcula perfiles_ocupados a partir de las ventas Activas reales.
    db.session.execute(text(
        "UPDATE cuenta SET perfiles_ocupados = ("
        "SELECT COUNT(*) FROM venta v WHERE v.cuenta_id = cuenta.id AND v.estado = 'Activa'"
        ") WHERE cuenta.estado = 'Activo'"
    ))
    db.session.commit()

def reconciliar_cuentas_vencidas():
    # Auto-desactiva cuentas cuya vigencia de adquisición ya venció
    fecha_hoy = datetime.utcnow()
    vencidas = Cuenta.query.filter(
        Cuenta.estado == 'Activo',
        Cuenta.fecha_vencimiento_cuenta.isnot(None),
        Cuenta.fecha_vencimiento_cuenta < fecha_hoy
    ).all()
    if vencidas:
        for c in vencidas:
            c.estado = 'Inactivo'
        db.session.commit()

# Código para crear las tablas y un vendedor por defecto si no existen
with app.app_context():
    db.create_all()
    ejecutar_migraciones()
    ejecutar_indices()
    if not Vendedor.query.first():
        vendedor_admin = Vendedor(nombre='Jorge', contacto='Admin')
        db.session.add(vendedor_admin)
        db.session.commit()
        
    plantilla_antigua_s = Plantilla.query.filter_by(tipo='entrega').first()
    if plantilla_antigua_s:
        plantilla_antigua_s.tipo = 'entrega_streaming'
        
    plantilla_antigua_i = Plantilla.query.filter_by(tipo='entrega_iptv').first()
    if plantilla_antigua_i:
        plantilla_antigua_i.tipo = 'entrega_iptv_1'
        db.session.commit()
        
    if not Plantilla.query.filter_by(tipo='entrega_streaming').first():
        p_entrega_s = Plantilla(tipo='entrega_streaming', mensaje="¡Hola [CLIENTE]! 👋\nAquí tienes los datos de tu cuenta de [PLATAFORMA]:\n\n📧 Correo: [CORREO]\n🔑 Contraseña: [CONTRASENA]\n📺 Perfil: [PERFIL]\n🔒 PIN: [PIN]\n\nTu servicio vence el: [VENCIMIENTO].\n¡Disfruta tu contenido! 🍿")
        db.session.add(p_entrega_s)
        
    if not Plantilla.query.filter_by(tipo='entrega_iptv_1').first():
        p_entrega_i1 = Plantilla(tipo='entrega_iptv_1', mensaje="¡Hola [CLIENTE]! 📺\nAquí tienes los accesos para tu servicio de [PLATAFORMA] (IPTV 1):\n\n👤 Usuario: [CORREO]\n🔑 Contraseña: [CONTRASENA]\n\n🔗 Descarga la app aquí: (Link 1)\n\nTu servicio vence el: [VENCIMIENTO].")
        db.session.add(p_entrega_i1)

    if not Plantilla.query.filter_by(tipo='entrega_iptv_2').first():
        p_entrega_i2 = Plantilla(tipo='entrega_iptv_2', mensaje="¡Hola [CLIENTE]! 📺\nAquí tienes los accesos para tu servicio de [PLATAFORMA] (IPTV 2):\n\n👤 Usuario: [CORREO]\n🔑 Contraseña: [CONTRASENA]\n\n🔗 Descarga la app aquí: (Link 2)\n\nTu servicio vence el: [VENCIMIENTO].")
        db.session.add(p_entrega_i2)
        
    if not Plantilla.query.filter_by(tipo='recordatorio').first():
        p_recordatorio = Plantilla(tipo='recordatorio', mensaje="¡Hola [CLIENTE]! 🔔\nTe recordamos que tu suscripción de [PLATAFORMA] vence el [VENCIMIENTO].\n\nPara seguir disfrutando sin interrupciones, puedes renovar por [PRECIO] [MONEDA]. \n¡Quedamos atentos a tu mensaje! 😊")
        db.session.add(p_recordatorio)
        
    db.session.commit()
    reconciliar_slots()
    reconciliar_cuentas_vencidas()


# ==========================================
# RUTA PRINCIPAL (DASHBOARD)
# ==========================================
@app.route('/')
def dashboard():
    fecha_hoy = datetime.utcnow()
    reconciliar_cuentas_vencidas()
    
    ventas_activas = Venta.query.filter(Venta.fecha_vencimiento >= fecha_hoy).count()
    
    # OPTIMIZACIÓN: ingresos del mes agregados en la BD (sin cargar todas las ventas)
    inicio_mes = fecha_hoy.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    proximo_mes = (inicio_mes + timedelta(days=31)).replace(day=1)
    
    ingresos = {'USDT': 0, 'BOB': 0, 'ARS': 0, 'CLP': 0}
    sumas_mes = db.session.query(Venta.moneda, func.sum(Venta.precio)).filter(
        Venta.fecha_inicio >= inicio_mes,
        Venta.fecha_inicio < proximo_mes
    ).group_by(Venta.moneda).all()
    for moneda, total in sumas_mes:
        if moneda in ingresos and total:
            ingresos[moneda] = round(total, 2)
            
    cuentas_activas = Cuenta.query.filter_by(estado='Activo').all()
    slots_disponibles = sum((c.perfiles_maximos - c.perfiles_ocupados) for c in cuentas_activas)
    
    limite_vencimiento = fecha_hoy + timedelta(days=3)
    # OPTIMIZACIÓN: Carga unificada de ventas para vencer
    ventas_por_vencer = Venta.query.options(
        joinedload(Venta.cliente),
        joinedload(Venta.cuenta)
    ).filter(
        Venta.fecha_vencimiento >= fecha_hoy, 
        Venta.fecha_vencimiento <= limite_vencimiento
    ).order_by(Venta.fecha_vencimiento.asc()).all()
    
    return render_template('dashboard.html', 
                           ventas_activas=ventas_activas,
                           ingresos=ingresos,
                           slots_disponibles=slots_disponibles,
                           ventas_por_vencer=ventas_por_vencer,
                           current_time=fecha_hoy)


# ==========================================
# RUTAS DE CUENTAS E INVENTARIO
# ==========================================
@app.route('/cuentas')
def vista_cuentas():
    reconciliar_cuentas_vencidas()
    query = request.args.get('q', '').strip()
    # OPTIMIZACIÓN: eager-load de comprador/renovador (evita 2 consultas lazy por fila)
    if query:
        termino = f"%{query}%"
        cuentas = Cuenta.query.options(
            joinedload(Cuenta.comprador),
            joinedload(Cuenta.renovador)
        ).filter(
            or_(
                Cuenta.correo.ilike(termino),
                Cuenta.plataforma.ilike(termino)
            )
        ).order_by(Cuenta.estado.asc()).all()
    else:
        cuentas = Cuenta.query.options(
            joinedload(Cuenta.comprador),
            joinedload(Cuenta.renovador)
        ).order_by(Cuenta.estado.asc()).all()
    vendedores = Vendedor.query.order_by(Vendedor.nombre.asc()).all()
    # OPTIMIZACIÓN: un solo query para saber qué cuentas tienen ventas (antes: 1 lazy por fila)
    cuentas_con_ventas = set(
        fila[0] for fila in db.session.query(Venta.cuenta_id).distinct().all()
    )
    return render_template('cuentas.html', cuentas=cuentas, vendedores=vendedores, query=query, cuentas_con_ventas=cuentas_con_ventas)

@app.route('/cuentas/nueva', methods=['POST'])
def nueva_cuenta():
    tipo_cuenta = request.form['tipo']             
    plataforma_ingresada = request.form['plataforma'].strip()
    
    credencial_limpia = request.form['correo_o_usuario'].strip() 
    
    cuenta_duplicada = Cuenta.query.filter(
        Cuenta.plataforma == plataforma_ingresada,
        db.func.lower(Cuenta.correo) == credencial_limpia.lower()
    ).first()
    
    if cuenta_duplicada:
        flash(f'¡Error! Ya tienes registrada la cuenta {credencial_limpia} en la plataforma {plataforma_ingresada}.', 'error')
        return redirect(url_for('vista_cuentas'))
    
    costo = request.form.get('costo', 0.0)
    moneda_costo = request.form.get('moneda_costo', 'USDT')
    
    vendedor_id = request.form.get('vendedor_id', type=int)
    dias_duracion = int(request.form.get('dias_duracion', 30))
    fecha_compra = datetime.utcnow()
    fecha_vencimiento_cuenta = fecha_compra + timedelta(days=dias_duracion)
    
    nueva = Cuenta(
        tipo=tipo_cuenta,
        plataforma=plataforma_ingresada,
        correo=credencial_limpia, 
        contrasena=request.form['contrasena'].strip(),
        perfiles_maximos=int(request.form.get('perfiles_maximos', 1)),
        costo=float(costo),
        moneda_costo=moneda_costo,
        vendedor_id=vendedor_id,
        dias_duracion=dias_duracion,
        fecha_compra=fecha_compra,
        fecha_vencimiento_cuenta=fecha_vencimiento_cuenta
    )
    
    db.session.add(nueva)
    db.session.commit()
    
    flash('¡Cuenta registrada con éxito!', 'success')
    return redirect(url_for('vista_cuentas'))

@app.route('/cuentas/editar/<int:id>', methods=['POST'])
def editar_cuenta(id):
    cuenta = Cuenta.query.get_or_404(id)
    
    cuenta.correo = request.form['correo_o_usuario'].strip()
    cuenta.contrasena = request.form['contrasena'].strip()
    cuenta.perfiles_maximos = int(request.form.get('perfiles_maximos', 1))
    
    cuenta.vendedor_id = request.form.get('vendedor_id', type=int)
    cuenta.dias_duracion = int(request.form.get('dias_duracion', 30))
    
    cuenta.costo = float(request.form.get('costo', 0.0))
    cuenta.moneda_costo = request.form.get('moneda_costo', 'USDT')
    
    cuenta.estado = request.form['estado']
    
    db.session.commit()
    flash('¡Cuenta actualizada correctamente!', 'success')
    return redirect(url_for('vista_cuentas'))

@app.route('/cuentas/renovar/<int:id>', methods=['POST'])
def renovar_cuenta(id):
    cuenta = Cuenta.query.get_or_404(id)
    dias = int(request.form.get('dias_duracion', cuenta.dias_duracion))
    costo = float(request.form.get('ultimo_costo_renovacion', 0))
    moneda = request.form.get('ultima_moneda_renovacion', 'USDT')
    vendedor_renovacion_id = request.form.get('vendedor_renovacion_id', type=int)
    
    fecha_base = cuenta.fecha_vencimiento_cuenta
    if not fecha_base or fecha_base < datetime.utcnow():
        fecha_base = datetime.utcnow()
    cuenta.fecha_vencimiento_cuenta = fecha_base + timedelta(days=dias)
    cuenta.vendedor_renovacion_id = vendedor_renovacion_id
    cuenta.ultima_renovacion_fecha = datetime.utcnow()
    cuenta.ultimo_costo_renovacion = costo
    cuenta.ultima_moneda_renovacion = moneda
    cuenta.dias_duracion = dias
    cuenta.estado = 'Activo'
    
    db.session.commit()
    flash(f'¡Cuenta {cuenta.correo} renovada y reactivada hasta el {cuenta.fecha_vencimiento_cuenta.strftime("%d/%m/%Y")}!', 'success')
    return redirect(url_for('vista_cuentas'))

@app.route('/cuentas/cambiar_estado/<int:id>', methods=['POST'])
def cambiar_estado_cuenta(id):
    cuenta = Cuenta.query.get_or_404(id)
    if request.form.get('estado'):
        cuenta.estado = 'Activo'
        flash(f'¡Cuenta {cuenta.correo} activada!', 'success')
    else:
        cuenta.estado = 'Inactivo'
        flash(f'¡Cuenta {cuenta.correo} desactivada!', 'warning')
    db.session.commit()
    return redirect(url_for('vista_cuentas'))

@app.route('/cuentas/eliminar/<int:id>', methods=['POST'])
def eliminar_cuenta(id):
    cuenta = Cuenta.query.get_or_404(id)
    
    if cuenta.ventas:
        flash(f'¡Error! No puedes eliminar la cuenta {cuenta.correo} porque tiene historial de ventas. Ponla en "Inactivo" en su lugar.', 'error')
    else:
        db.session.delete(cuenta)
        db.session.commit()
        flash('¡Cuenta eliminada correctamente del inventario!', 'success')
        
    return redirect(url_for('vista_cuentas'))

# ==========================================
# NUEVA API: BUSCADOR PREDICTIVO LIGERO
# ==========================================
@app.route('/api/buscar_clientes')
def api_buscar_clientes():
    query = request.args.get('q', '').strip()
    if not query:
        return {"resultados": []}
    
    termino = f"%{query}%"
    # Busca máximo 10 coincidencias por nombre o teléfono
    clientes = Cliente.query.filter(
        or_(
            Cliente.nombre.ilike(termino), 
            Cliente.telefono_limpio.ilike(termino),
            Cliente.telefono_original.ilike(termino)
        )
    ).limit(10).all()
    
    resultados = [{"id": c.id, "nombre": c.nombre, "telefono": c.telefono_original} for c in clientes]
    return {"resultados": resultados}

# ==========================================
# RUTAS DE CLIENTES
# ==========================================

@app.route('/clientes')
def vista_clientes():
    fecha_hoy = datetime.utcnow()
    page = request.args.get('page', 1, type=int)
    query = request.args.get('q', '').strip() # Captura lo que el usuario busca

    base_query = Cliente.query.options(
        selectinload(Cliente.ventas).joinedload(Venta.vendedor),
        selectinload(Cliente.ventas).joinedload(Venta.cuenta)
    )

    if query:
        termino = f"%{query}%"
        base_query = base_query.filter(
            or_(
                Cliente.nombre.ilike(termino),
                Cliente.telefono_limpio.ilike(termino),
                Cliente.telefono_original.ilike(termino)
            )
        )

    clientes_paginados = base_query.order_by(Cliente.nombre.asc()).paginate(page=page, per_page=20, error_out=False)
    return render_template('clientes.html', clientes_paginados=clientes_paginados, current_time=fecha_hoy, query=query)

@app.route('/clientes/editar/<int:id>', methods=['POST'])
def editar_cliente(id):
    cliente = Cliente.query.get_or_404(id)
    cliente.nombre = request.form['nombre'].strip().title()
    
    telefono_nuevo = request.form['telefono_original'].strip()
    cliente.telefono_original = telefono_nuevo
    cliente.telefono_limpio = ''.join(filter(str.isdigit, telefono_nuevo))
    
    db.session.commit()
    flash(f'¡Datos de {cliente.nombre} actualizados!', 'success')
    return redirect(url_for('vista_clientes'))

@app.route('/clientes/eliminar/<int:id>', methods=['POST'])
def eliminar_cliente(id):
    cliente = Cliente.query.get_or_404(id)
    
    if cliente.ventas:
        flash(f'¡Error! No puedes eliminar a {cliente.nombre} porque tiene un historial de compras registrado.', 'error')
    else:
        db.session.delete(cliente)
        db.session.commit()
        flash(f'¡Cliente {cliente.nombre} eliminado correctamente!', 'success')
        
    return redirect(url_for('vista_clientes'))

# ==========================================
# RUTAS DE VENTAS
# ==========================================

@app.route('/ventas')
def vista_ventas():
    fecha_hoy = datetime.utcnow()
    reconciliar_cuentas_vencidas()
    query = request.args.get('q', '').strip()
    query_inactiva = request.args.get('qi', '').strip()
    page = request.args.get('page', 1, type=int)
    
    # --- 1. MOTOR DE AUTO-LIMPIEZA (en SQL masivo, sin bucles ni N+1) ---
    vencidas_actualizadas = Venta.query.filter(
        Venta.estado == 'Activa', Venta.fecha_vencimiento < fecha_hoy
    ).update({'estado': 'Inactiva'}, synchronize_session=False)

    # Reconciliación de slots: recalcula perfiles_ocupados en una sola sentencia
    # (self-healing de datos, corrige cualquier desfase).
    slots_corregidos = db.session.execute(text(
        "UPDATE cuenta SET perfiles_ocupados = ("
        "SELECT COUNT(*) FROM venta v WHERE v.cuenta_id = cuenta.id AND v.estado = 'Activa'"
        ") WHERE cuenta.estado = 'Activo'"
    )).rowcount

    if vencidas_actualizadas or slots_corregidos:
        db.session.commit()

    # --- 2. CARGAR ACTIVAS (Con buscador SQL y Paginación) ---
    base_activas = Venta.query.options(
        joinedload(Venta.cliente),
        joinedload(Venta.cuenta),
        joinedload(Venta.vendedor)
    ).join(Cliente).join(Cuenta).filter(Venta.estado == 'Activa')

    if query:
        termino = f"%{query}%"
        base_activas = base_activas.filter(
            or_(
                Cliente.nombre.ilike(termino),
                Cliente.telefono_limpio.ilike(termino),
                Cuenta.correo.ilike(termino),
                Cuenta.plataforma.ilike(termino),
                Venta.nombre_perfil.ilike(termino),
                Venta.pin.ilike(termino)
            )
        )
    
    ventas_paginadas = base_activas.order_by(Venta.fecha_inicio.desc(), Venta.id.desc()).paginate(page=page, per_page=30, error_out=False)
    
    # Inactivas (con buscador propio independiente del de activas)
    base_inactivas = Venta.query.options(
        joinedload(Venta.cliente),
        joinedload(Venta.cuenta)
    ).join(Cliente).join(Cuenta).filter(Venta.estado == 'Inactiva')
    
    if query_inactiva:
        termino_i = f"%{query_inactiva}%"
        base_inactivas = base_inactivas.filter(
            or_(
                Cliente.nombre.ilike(termino_i),
                Cliente.telefono_limpio.ilike(termino_i),
                Cuenta.correo.ilike(termino_i),
                Cuenta.plataforma.ilike(termino_i),
                Venta.nombre_perfil.ilike(termino_i),
                Venta.pin.ilike(termino_i)
            )
        )
    
    ventas_inactivas = base_inactivas.order_by(Venta.fecha_inicio.desc(), Venta.id.desc()).limit(30).all()
    
    # ¡YA NO CARGAMOS A TODOS LOS CLIENTES AQUÍ!
    # Solo cuentas con slots libres (las llenas no se ofrecen al vender ni hacer mudanza)
    cuentas = Cuenta.query.filter(
        Cuenta.estado == 'Activo',
        Cuenta.perfiles_ocupados < Cuenta.perfiles_maximos
    ).order_by(Cuenta.tipo.asc(), Cuenta.plataforma.asc(), Cuenta.correo.asc()).all()
    vendedores = Vendedor.query.all()
    
    return render_template('ventas.html', 
                           ventas_paginadas=ventas_paginadas, 
                           ventas_inactivas=ventas_inactivas, 
                           cuentas=cuentas, 
                           vendedores=vendedores, 
                           current_time=fecha_hoy,
                           query=query,
                           query_inactiva=query_inactiva)

@app.route('/ventas/liberar/<int:id>', methods=['POST'])
def liberar_venta(id):
    venta = Venta.query.get_or_404(id)
    
    if venta.estado == 'Activa':
        venta.estado = 'Inactiva' 
        if venta.cuenta.perfiles_ocupados > 0:
            venta.cuenta.perfiles_ocupados -= 1
            
        db.session.commit()
        flash(f'¡Perfil liberado! El espacio ha regresado al inventario de {venta.cuenta.plataforma}.', 'success')
        
    return redirect(url_for('vista_ventas'))

@app.route('/ventas/nueva', methods=['POST'])
def nueva_venta():
    tipo_cliente = request.form['tipo_cliente']
    
    if tipo_cliente == 'nuevo':
        nombre = request.form['nombre_nuevo'].strip().title()
        telefono_raw = request.form['telefono_nuevo'].strip()
        telefono_limpio = ''.join(caracter for caracter in telefono_raw if caracter.isdigit())
        
        nuevo_cliente = Cliente(nombre=nombre, telefono_original=telefono_raw, telefono_limpio=telefono_limpio)
        db.session.add(nuevo_cliente)
        db.session.flush() 
        cliente_final_id = nuevo_cliente.id
    else:
        cliente_final_id = request.form['cliente_id']

    vendedor_id = request.form['vendedor_id'] 
    fecha_inicio_str = request.form['fecha_inicio']
    fecha_inicio = datetime.strptime(fecha_inicio_str, '%Y-%m-%d')
    
    cliente_obj = Cliente.query.get(cliente_final_id)
    nombre_cliente = cliente_obj.nombre if cliente_obj else ''
    
    cuenta_ids = request.form.getlist('cuenta_id[]')
    nombres_perfil = request.form.getlist('nombre_perfil[]')
    pins = request.form.getlist('pin[]')
    dias_list = request.form.getlist('dias[]')
    precios = request.form.getlist('precio[]')
    monedas = request.form.getlist('moneda[]')
    
    ventas_creadas = 0
    errores = []
    for i in range(len(cuenta_ids)):
        cuenta_id = cuenta_ids[i]
        cuenta = Cuenta.query.get(cuenta_id)
        
        if not cuenta or cuenta.estado != 'Activo' or cuenta.perfiles_ocupados >= cuenta.perfiles_maximos:
            errores.append(f'Cuenta ID {cuenta_id} llena, inactiva o no encontrada.')
            continue
            
        try:
            nombre_perfil = nombres_perfil[i].strip() if i < len(nombres_perfil) else ''
            if not nombre_perfil:
                nombre_perfil = nombre_cliente
            pin = pins[i].strip() if i < len(pins) else ''
            dias = int(dias_list[i]) if i < len(dias_list) else 30
            precio = float(precios[i]) if i < len(precios) else 0
            moneda = monedas[i] if i < len(monedas) else 'BOB'
        except (ValueError, TypeError):
            errores.append(f'Datos inválidos (perfil, días o precio) en la fila de {cuenta.plataforma}. Venta omitida.')
            continue

        cuenta.perfiles_ocupados += 1
        
        fecha_vencimiento = fecha_inicio + timedelta(days=dias)
        
        venta = Venta(
            cliente_id=cliente_final_id,
            cuenta_id=cuenta_id,
            vendedor_id=vendedor_id,
            nombre_perfil=nombre_perfil,
            pin=pin,
            fecha_inicio=fecha_inicio,
            dias_suscripcion=dias,
            fecha_vencimiento=fecha_vencimiento,
            precio=precio,
            moneda=moneda
        )
        
        db.session.add(venta)
        ventas_creadas += 1
    
    db.session.commit()
    
    if errores:
        for e in errores:
            flash(e, 'error')
    flash(f'¡{ventas_creadas} venta(s) registrada(s) exitosamente!', 'success')
    return redirect(url_for('vista_ventas'))

@app.route('/ventas/renovar/<int:id>', methods=['POST'])
def renovar_venta(id):
    venta = Venta.query.get_or_404(id)
    dias = int(request.form['dias'])
    precio = float(request.form['precio'])
    moneda = request.form['moneda']
    
    hacer_mudanza = request.form.get('hacer_mudanza') == 'si'
    nueva_cuenta_id = request.form.get('nueva_cuenta_id')
    
    if hacer_mudanza and nueva_cuenta_id:
        nueva_cuenta = Cuenta.query.get_or_404(nueva_cuenta_id)
        if nueva_cuenta.perfiles_ocupados >= nueva_cuenta.perfiles_maximos or nueva_cuenta.estado != 'Activo':
            flash('Error: La nueva cuenta seleccionada ya está llena o inactiva.', 'error')
            return redirect(url_for('vista_ventas'))
            
        if venta.estado == 'Activa' and venta.cuenta.perfiles_ocupados > 0:
            venta.cuenta.perfiles_ocupados -= 1 
            
        nueva_cuenta.perfiles_ocupados += 1 
        venta.cuenta_id = nueva_cuenta.id
    
    else:
        if venta.estado == 'Inactiva':
            if venta.cuenta.perfiles_ocupados >= venta.cuenta.perfiles_maximos:
                flash('¡El cupo original ya fue vendido a otra persona! Debes hacer una mudanza obligatoria.', 'error')
                return redirect(url_for('vista_ventas'))
            else:
                venta.cuenta.perfiles_ocupados += 1 

    venta.estado = 'Activa'
    venta.precio = precio
    venta.moneda = moneda
    
    fecha_base = datetime.utcnow() if venta.fecha_vencimiento < datetime.utcnow() else venta.fecha_vencimiento
    venta.fecha_vencimiento = fecha_base + timedelta(days=dias)
    
    db.session.commit()
    flash('¡Suscripción renovada y reactivada con éxito!', 'success')
    return redirect(url_for('vista_ventas'))

@app.route('/ventas/editar/<int:id>', methods=['POST'])
def editar_venta(id):
    venta = Venta.query.get_or_404(id)
    
    venta.nombre_perfil = request.form['nombre_perfil'].strip()
    venta.pin = request.form['pin'].strip()
    venta.precio = float(request.form['precio'])
    venta.moneda = request.form['moneda']
    
    fecha_vencimiento_str = request.form['fecha_vencimiento']
    venta.fecha_vencimiento = datetime.strptime(fecha_vencimiento_str, '%Y-%m-%d')
    
    db.session.commit()
    
    flash('¡Los datos de la venta fueron corregidos correctamente!', 'success')
    return redirect(url_for('vista_ventas'))

@app.route('/ventas/eliminar/<int:id>', methods=['POST'])
def eliminar_venta(id):
    venta = Venta.query.get_or_404(id)
    cuenta = Cuenta.query.get(venta.cuenta_id)
    
    if cuenta.perfiles_ocupados > 0:
        cuenta.perfiles_ocupados -= 1
        
    db.session.delete(venta)
    db.session.commit()
    
    flash('¡Venta eliminada correctamente! El perfil vuelve a estar disponible en tu inventario.', 'success')
    return redirect(url_for('vista_ventas'))

@app.route('/ventas/mudanza/<int:id>', methods=['POST'])
def mudanza_venta(id):
    venta = Venta.query.get_or_404(id)
    nueva_cuenta_id = request.form['nueva_cuenta_id']
    
    cuenta_antigua = Cuenta.query.get(venta.cuenta_id)
    cuenta_nueva = Cuenta.query.get(nueva_cuenta_id)
    
    if cuenta_nueva.perfiles_ocupados >= cuenta_nueva.perfiles_maximos or cuenta_nueva.estado != 'Activo':
        flash('¡Error! La cuenta de destino no tiene slots libres o está inactiva.', 'error')
        return redirect(url_for('vista_ventas'))
        
    if cuenta_antigua.perfiles_ocupados > 0:
        cuenta_antigua.perfiles_ocupados -= 1
        
    cuenta_nueva.perfiles_ocupados += 1
    venta.cuenta_id = cuenta_nueva.id
    
    db.session.commit()
    
    flash(f'¡Mudanza exitosa! El cliente fue trasladado a la cuenta {cuenta_nueva.correo}.', 'success')
    return redirect(url_for('vista_ventas'))

@app.route('/plantillas')
def vista_plantillas():
    plantillas = Plantilla.query.all()
    return render_template('plantillas.html', plantillas=plantillas)

@app.route('/plantillas/editar/<int:id>', methods=['POST'])
def editar_plantilla(id):
    plantilla = Plantilla.query.get_or_404(id)
    plantilla.mensaje = request.form['mensaje']
    db.session.commit()
    flash('¡Plantilla actualizada con éxito!', 'success')
    return redirect(url_for('vista_plantillas'))

@app.route('/whatsapp/<tipo>/<int:venta_id>')
def enviar_whatsapp(tipo, venta_id):
    venta = Venta.query.get_or_404(venta_id)
    
    nombre_plantilla = tipo
    if tipo == 'entrega':
        if venta.cuenta.tipo == 'IPTV 1':
            nombre_plantilla = 'entrega_iptv_1'
        elif venta.cuenta.tipo == 'IPTV 2':
            nombre_plantilla = 'entrega_iptv_2'
        else:
            nombre_plantilla = 'entrega_streaming'
            
    plantilla = Plantilla.query.filter_by(tipo=nombre_plantilla).first()
    
    if not plantilla:
        flash('Plantilla no encontrada.', 'error')
        return redirect(url_for('vista_ventas'))
        
    mensaje = plantilla.mensaje
    mensaje = mensaje.replace('[CLIENTE]', venta.cliente.nombre)
    mensaje = mensaje.replace('[PLATAFORMA]', venta.cuenta.plataforma)
    mensaje = mensaje.replace('[CORREO]', venta.cuenta.correo)
    mensaje = mensaje.replace('[CONTRASENA]', venta.cuenta.contrasena)
    mensaje = mensaje.replace('[PERFIL]', venta.nombre_perfil)
    mensaje = mensaje.replace('[PIN]', venta.pin if venta.pin else 'Sin PIN')
    mensaje = mensaje.replace('[VENCIMIENTO]', venta.fecha_vencimiento.strftime('%d/%m/%Y'))
    mensaje = mensaje.replace('[PRECIO]', str(venta.precio))
    mensaje = mensaje.replace('[MONEDA]', venta.moneda)
    
    mensaje_codificado = urllib.parse.quote(mensaje)
    numero = venta.cliente.telefono_limpio
    url_whatsapp = f"https://wa.me/{numero}?text={mensaje_codificado}"
    
    return redirect(url_whatsapp)

@app.route('/vendedores')
def vista_vendedores():
    # OPTIMIZACIÓN: eager-load de las ventas (evita 1 lazy-load por vendedor en la plantilla)
    vendedores = Vendedor.query.options(selectinload(Vendedor.ventas)).order_by(Vendedor.nombre.asc()).all()
    return render_template('vendedores.html', vendedores=vendedores)

@app.route('/vendedores/nuevo', methods=['POST'])
def nuevo_vendedor():
    nombre = request.form['nombre'].strip().title()
    contacto = request.form['contacto'].strip()
    
    nuevo_vendedor = Vendedor(nombre=nombre, contacto=contacto)
    db.session.add(nuevo_vendedor)
    db.session.commit()
    
    flash(f'¡Vendedor {nombre} registrado con éxito!', 'success')
    return redirect(url_for('vista_vendedores'))

@app.route('/vendedores/editar/<int:id>', methods=['POST'])
def editar_vendedor(id):
    vendedor = Vendedor.query.get_or_404(id)
    vendedor.nombre = request.form['nombre'].strip().title()
    vendedor.contacto = request.form['contacto'].strip()
    
    db.session.commit()
    flash(f'¡Datos de {vendedor.nombre} actualizados!', 'success')
    return redirect(url_for('vista_vendedores'))

@app.route('/vendedores/eliminar/<int:id>', methods=['POST'])
def eliminar_vendedor(id):
    vendedor = Vendedor.query.get_or_404(id)
    
    if vendedor.ventas:
        flash(f'¡Error! No puedes eliminar a {vendedor.nombre} porque tiene ventas asociadas en el sistema.', 'error')
    else:
        db.session.delete(vendedor)
        db.session.commit()
        flash(f'¡Vendedor {vendedor.nombre} eliminado correctamente!', 'success')
        
    return redirect(url_for('vista_vendedores'))

@app.route('/finanzas')
def vista_finanzas():
    fecha_hoy = datetime.utcnow()
    query = request.args.get('q', '').strip()
    page = request.args.get('page', 1, type=int)
    
    totales_historicos = {'USDT': 0, 'BOB': 0, 'ARS': 0, 'CLP': 0}
    totales_mes = {'USDT': 0, 'BOB': 0, 'ARS': 0, 'CLP': 0}
    
    # 1. OPTIMIZACIÓN: Las sumas se hacen en la base de datos, no en Python
    sumas_historicas = db.session.query(Venta.moneda, func.sum(Venta.precio)).group_by(Venta.moneda).all()
    for moneda, total in sumas_historicas:
        if moneda in totales_historicos and total:
            totales_historicos[moneda] = round(total, 2)
            
    # OPTIMIZACIÓN: rango de fechas (usa índice) en lugar de func.extract (scan completo)
    inicio_mes = fecha_hoy.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    proximo_mes = (inicio_mes + timedelta(days=31)).replace(day=1)
    sumas_mes = db.session.query(Venta.moneda, func.sum(Venta.precio)).filter(
        Venta.fecha_inicio >= inicio_mes,
        Venta.fecha_inicio < proximo_mes
    ).group_by(Venta.moneda).all()
    for moneda, total in sumas_mes:
        if moneda in totales_mes and total:
            totales_mes[moneda] = round(total, 2)

    # 2. OPTIMIZACIÓN: Inversión agregada en la BD (sin cargar cuentas ni lazy-loads)
    inversion = {'USDT': 0.0, 'BOB': 0.0, 'ARS': 0.0, 'CLP': 0.0}
    sumas_inversion = db.session.query(Cuenta.moneda_costo, func.sum(Cuenta.costo)).filter(
        Cuenta.estado == 'Activo'
    ).group_by(Cuenta.moneda_costo).all()
    for moneda, total in sumas_inversion:
        if moneda in inversion and total:
            inversion[moneda] = round(total, 2)

    vendedores = Vendedor.query.order_by(Vendedor.nombre.asc()).all()
    nombres_vendedores = {v.id: v.nombre for v in vendedores}
    inversion_por_vendedor = {}
    sumas_inv_vendedor = db.session.query(
        Cuenta.vendedor_id, Cuenta.moneda_costo, func.sum(Cuenta.costo)
    ).filter(
        Cuenta.estado == 'Activo',
        Cuenta.vendedor_id.isnot(None)
    ).group_by(Cuenta.vendedor_id, Cuenta.moneda_costo).all()
    for vendedor_id, moneda, total in sumas_inv_vendedor:
        nom = nombres_vendedores.get(vendedor_id)
        if not nom or not total:
            continue
        if nom not in inversion_por_vendedor:
            inversion_por_vendedor[nom] = {'USDT': 0.0, 'BOB': 0.0, 'ARS': 0.0, 'CLP': 0.0}
        if moneda in inversion_por_vendedor[nom]:
            inversion_por_vendedor[nom][moneda] = round(total, 2)

    es_sqlite = 'sqlite' in db.engine.url.drivername
    if es_sqlite:
        fmt_mes = func.strftime('%Y-%m', Venta.fecha_inicio)
    else:
        fmt_mes = func.to_char(Venta.fecha_inicio, 'YYYY-MM')
    meses_disponibles = db.session.query(fmt_mes.label('mes')).distinct().order_by(text('mes DESC')).all()
    reporte_historico = {}

    # 3. REPORTE MENSUAL POR VENDEDOR EN UNA SOLA CONSULTA (sin N+1 por venta).
    # El redondeo se hace en Python (igual que antes) para no depender de
    # round(double) que no existe en PostgreSQL.
    filas_reporte = db.session.query(
        fmt_mes.label('mes'),
        Venta.vendedor_id,
        Venta.moneda,
        Venta.precio,
        Cuenta.moneda_costo,
        Cuenta.costo,
        Cuenta.perfiles_maximos
    ).join(Cuenta, Cuenta.id == Venta.cuenta_id).filter(
        Venta.vendedor_id.isnot(None)
    ).all()

    agrupado = {}
    for mes, vendedor_id, moneda, precio, moneda_costo, costo, perfiles in filas_reporte:
        if not mes:
            continue
        grupo = agrupado.setdefault((mes, vendedor_id), {
            'ingresos': {'USDT': 0.0, 'BOB': 0.0, 'ARS': 0.0, 'CLP': 0.0},
            'costos': {'USDT': 0.0, 'BOB': 0.0, 'ARS': 0.0, 'CLP': 0.0},
            'perfiles': 0,
        })
        if moneda in grupo['ingresos']:
            grupo['ingresos'][moneda] += precio
        if moneda_costo in grupo['costos']:
            costo_unitario = round((costo or 0) / perfiles, 2) if perfiles and perfiles > 0 else 0
            grupo['costos'][moneda_costo] += costo_unitario
        grupo['perfiles'] += 1

    for mes_tuple in meses_disponibles:
        mes_str = mes_tuple[0]
        if not mes_str:
            continue
        reporte_historico[mes_str] = {}
        for v in vendedores:
            grupo = agrupado.get((mes_str, v.id))
            if grupo:
                reporte_historico[mes_str][v.nombre] = {
                    'ingresos': {c: round(grupo['ingresos'][c], 2) for c in grupo['ingresos']},
                    'costos': {c: round(grupo['costos'][c], 2) for c in grupo['costos']},
                    'perfiles': grupo['perfiles'],
                }
            else:
                reporte_historico[mes_str][v.nombre] = {
                    'ingresos': {'USDT': 0.0, 'BOB': 0.0, 'ARS': 0.0, 'CLP': 0.0},
                    'costos': {'USDT': 0.0, 'BOB': 0.0, 'ARS': 0.0, 'CLP': 0.0},
                    'perfiles': 0,
                }

    # 4. TABLA DE TRANSACCIONES PAGINADA Y CON BUSCADOR
    base_trans = Venta.query.options(
        joinedload(Venta.cliente), 
        joinedload(Venta.cuenta), 
        joinedload(Venta.vendedor)
    ).join(Cliente).join(Cuenta).join(Vendedor, Venta.vendedor_id == Vendedor.id)
    if query:
        termino = f"%{query}%"
        base_trans = base_trans.filter(or_(Cliente.nombre.ilike(termino), Cuenta.plataforma.ilike(termino), Vendedor.nombre.ilike(termino)))
        
    transacciones_paginadas = base_trans.order_by(Venta.fecha_inicio.desc()).paginate(page=page, per_page=30, error_out=False)
            
    return render_template('finanzas.html', 
                           transacciones_paginadas=transacciones_paginadas,
                           totales_historicos=totales_historicos, 
                           totales_mes=totales_mes,
                           inversion=inversion,
                           inversion_por_vendedor=inversion_por_vendedor,
                           reporte_historico=reporte_historico,
                           query=query)

if __name__ == '__main__':
    app.run(debug=True)