# AGENTS.md - Guía Exhaustiva para Desarrolladores y Agentes de IA

Este documento describe con precisión la arquitectura, modelos, flujos, estándares y directrices operativas del proyecto **AdmNachtShop** (Sistema de Gestión y Venta de Cuentas de Streaming e IPTV).

---

## 1. Arquitectura General y Stack Tecnológico

El proyecto es un **monolito en Flask**, optimizado para alta velocidad, bajo consumo de recursos y facilidad de despliegue.

### Stack Tecnológico:
- **Backend**: Python (>=3.11 / 3.14-slim en producción).
- **Framework Web**: [Flask](https://flask.palletsprojects.com/) (3.1.3) + [Werkzeug](https://werkzeug.palletsprojects.com/) (3.1.8) + [Jinja2](https://jinja.palletsprojects.com/) (3.1.6).
- **ORM & Base de Datos**: [Flask-SQLAlchemy](https://flask-sqlalchemy.palletsprojects.com/) (3.1.1) sobre [SQLAlchemy](https://www.sqlalchemy.org/) (2.0.51).
  - **Local**: SQLite (`sqlite:///panel_ventas.db` en el directorio `instance/`).
  - **Producción**: PostgreSQL conectado mediante `psycopg2-binary` (2.9.12) vía la variable de entorno `DATABASE_URL`.
- **Frontend**:
  - HTML5 renderizado del lado del servidor con Jinja2.
  - CSS: [Bootstrap 5.3.0](https://getbootstrap.com/) con tema Dark Custom (paleta Discord / Slate: `#0f1115`, `#14171c`, `#35373C`, acentos `#5865F2`, `#23A559`, `#DA373C`, `#00A8FC`).
  - Iconos: [Bootstrap Icons](https://icons.getbootstrap.com/) (1.10.5).
  - Selectores dinámicos / Búsqueda AJAX: [Tom Select](https://tom-select.js.org/) (2.2.2).
- **Servidor WSGI & Despliegue**:
  - [Gunicorn](https://gunicorn.org/) (26.0.0) configurado con `--workers 1 --threads 4 --timeout 120`.
  - Plataforma Cloud: [Fly.io](https://fly.io/) (configurado en `fly.toml`, puerto interno `8080`, región `lax`).

---

## 2. Estructura de Carpetas y Responsabilidades

```text
├── app.py                  # Monolito principal: modelos, rutas, migraciones, queries y startup
├── test_app.py             # Suite de pruebas automatizadas y smoke tests funcionales
├── restaurar.py            # Script auxiliar para restaurar backups SQL en PostgreSQL
├── requirements.txt        # Dependencias de Python fijadas
├── Dockerfile              # Definición del contenedor de producción (Python 3.14-slim)
├── fly.toml                # Configuración de despliegue en Fly.io
├── Procfile                # Comando de arranque WSGI para plataformas PaaS
├── .dockerignore           # Exclusiones del build de Docker (ej. venv/, instance/)
├── AGENTS.md               # Este archivo de referencia técnica
├── instance/               # Almacenamiento local de bases de datos SQLite
│   ├── panel_ventas.db     # Base de datos de desarrollo local
│   └── test_panel_ventas.db# Base de datos temporal creada por test_app.py
├── backup db real/         # Volcados y backups SQL históricos
└── templates/              # Vistas Jinja2 (Bootstrap 5 Dark Mode)
    ├── base.html           # Layout maestro, navbar responsive móvil/desktop, estilos CSS base
    ├── dashboard.html      # Métricas del negocio, ingresos del mes y avisos de vencimientos
    ├── ventas.html         # Panel de ventas activas/inactivas, modales y creación multi-servicio
    ├── cuentas.html        # Inventario de cuentas, asignación de slots, costos y renovaciones
    ├── clientes.html       # Directorio de clientes con historial de compras cronológico
    ├── vendedores.html     # Administración de vendedores del equipo
    ├── finanzas.html       # Reportes de ingresos multidivisa, costos y rentabilidad por vendedor
    └── plantillas.html     # Editor de mensajes WhatsApp para entregas y recordatorios
```

---

## 3. Convenciones de Código, Nombres y Patrones de Diseño

### Nomenclatura:
- **Modelos y Tablas**: Nombres en singular y en español (`Vendedor`, `Cliente`, `Cuenta`, `Venta`, `Plantilla`).
- **Campos de Base de Datos**: `snake_case` en español (`fecha_vencimiento`, `perfiles_ocupados`, `telefono_limpio`, `moneda_costo`).
- **Rutas URL**: Minúsculas en español (`/ventas/nueva`, `/cuentas/renovar/<id>`, `/api/buscar_clientes`).
- **Nombres de Métodos de Ruta**: Verbo o descripción clara (`vista_ventas`, `nueva_venta`, `editar_cliente`).

### Patrones de Diseño Clave:

1. **Self-Healing & Auto-Reconciliación**:
   - `reconciliar_slots()`: Recalcula en una única sentencia SQL los `perfiles_ocupados` de cada cuenta basados en las ventas `Activas` reales.
   - `reconciliar_cuentas_vencidas()`: Desactiva automáticamente cuentas cuya vigencia de compra haya expirado (`fecha_vencimiento_cuenta < now`).
   - **Auto-limpieza de ventas**: En `GET /ventas`, las ventas vencidas pasan a estado `Inactiva` y liberan sus slots.

2. **Optimización de Consultas (Anti-N+1)**:
   - Uso intensivo de `joinedload` y `selectinload` en SQLAlchemy para relaciones foráneas (`Venta.cliente`, `Venta.cuenta`, `Venta.vendedor`, `Cuenta.comprador`, `Cuenta.renovador`).
   - Agregaciones de métricas financieras e ingresos ejecutadas directamente en SQL (`func.sum`, `func.strftime`, `func.to_char`).

3. **Multi-Divisa & Normalización**:
   - Monedas soportadas: `USDT`, `BOB`, `ARS`, `CLP`.
   - Filtro de plantilla `@app.template_filter('dinero')` para formateo uniforme de decimales y miles (`1,250.50`).
   - Normalización de números en `Cliente.telefono_limpio` (solo caracteres numéricos) para construir enlaces `https://wa.me/<numero>?text=...`.

4. **Regla Crítica de UI en Tablas HTML (Bootstrap Modals)**:
   - **NUNCA colocar `<div class="modal">` directamente como hijo de `<tbody>` o `<tr>`**. Los navegadores aplican *foster-parenting* extrayendo el modal fuera de la tabla, lo que congela la interfaz con un fondo oscuro estático.
   - Los modales deben residir dentro del último `<td>` de la fila correspondiente o colocarse fuera de la tabla `<table>`.

---

## 4. Flujos de Datos, Modelos y Rutas

### Modelos de Datos

```mermaid
erDiagram
    Vendedor ||--o{ Venta : "realiza"
    Vendedor ||--o{ Cuenta : "compra / renueva"
    Cliente ||--o{ Venta : "adquiere"
    Cuenta ||--o{ Venta : "aloja perfiles"

    Vendedor {
        int id PK
        string nombre
        string contacto
    }

    Cliente {
        int id PK
        string nombre
        string telefono_original
        string telefono_limpio
        datetime fecha_registro
    }

    Cuenta {
        int id PK
        string tipo
        string plataforma
        string correo
        string contrasena
        int perfiles_maximos
        int perfiles_ocupados
        string estado
        float costo
        string moneda_costo
        int vendedor_id FK
        datetime fecha_compra
        int dias_duracion
        datetime fecha_vencimiento_cuenta
        int vendedor_renovacion_id FK
        datetime ultima_renovacion_fecha
        float ultimo_costo_renovacion
        string ultima_moneda_renovacion
    }

    Venta {
        int id PK
        int cliente_id FK
        int cuenta_id FK
        int vendedor_id FK
        string nombre_perfil
        string pin
        datetime fecha_inicio
        int dias_suscripcion
        datetime fecha_vencimiento
        float precio
        string moneda
        string estado_pago
        string estado
    }

    Plantilla {
        int id PK
        string tipo
        text mensaje
    }
```

### Rutas y Endpoints

| Módulo | Método | Endpoint | Propósito |
| :--- | :--- | :--- | :--- |
| **Dashboard** | `GET` | `/` | Vista principal con KPI, ingresos mensuales, slots libres y alertas |
| **Cuentas** | `GET` | `/cuentas` | Catálogo de cuentas con filtros y estado de ocupación |
| | `POST` | `/cuentas/nueva` | Registra una cuenta (valida unicidad `plataforma + correo`) |
| | `POST` | `/cuentas/editar/<id>` | Actualiza credenciales, slots o costo |
| | `POST` | `/cuentas/renovar/<id>` | Extiende fecha de vencimiento y registra costo de renovación |
| | `POST` | `/cuentas/cambiar_estado/<id>` | Conmuta estado entre `Activo` e `Inactivo` |
| | `POST` | `/cuentas/eliminar/<id>` | Elimina cuenta (bloqueado si tiene ventas históricas asociadas) |
| **API AJAX** | `GET` | `/api/buscar_clientes?q=...` | Búsqueda predictiva rápida en formato JSON |
| **Clientes** | `GET` | `/clientes` | Listado paginado con modal de historial de compras |
| | `POST` | `/clientes/editar/<id>` | Modifica nombre y teléfonos (actualiza `telefono_limpio`) |
| | `POST` | `/clientes/eliminar/<id>` | Elimina cliente (bloqueado si tiene ventas registradas) |
| **Ventas** | `GET` | `/ventas` | Gestión de ventas activas/inactivas con paginación y búsqueda |
| | `POST` | `/ventas/nueva` | Creación de ventas simples o múltiples (`cuenta_id[]`, `precio[]`) |
| | `POST` | `/ventas/renovar/<id>` | Extiende fecha de vencimiento o ejecuta mudanza de cuenta |
| | `POST` | `/ventas/mudanza/<id>` | Mueve un perfil vendido a otra cuenta base con slots disponibles |
| | `POST` | `/ventas/editar/<id>` | Corrige perfil, pin, precio o fecha de vencimiento |
| | `POST` | `/ventas/liberar/<id>` | Pasa venta a `Inactiva` y descuenta slot |
| | `POST` | `/ventas/eliminar/<id>` | Elimina físicamente la venta y libera el slot permanentemente |
| **WhatsApp** | `GET` | `/whatsapp/<tipo>/<id>` | Genera texto con placeholders y redirige a `wa.me` |
| **Vendedores**| `GET` | `/vendedores` | Directorio de vendedores del panel |
| | `POST` | `/vendedores/nuevo` | Registra un nuevo vendedor |
| | `POST` | `/vendedores/editar/<id>` | Edita nombre o contacto |
| | `POST` | `/vendedores/eliminar/<id>` | Elimina vendedor (bloqueado si tiene ventas asociadas) |
| **Finanzas** | `GET` | `/finanzas` | Reportes consolidados, rentabilidad mensual y tabla transaccional |
| **Plantillas**| `GET` | `/plantillas` | Editor de textos para mensajes automatizados |
| | `POST` | `/plantillas/editar/<id>` | Guarda modificaciones en plantillas de entrega/recordatorio |

---

## 5. Comandos Comunes y Flujo de Trabajo

### Entorno de Desarrollo Local

```powershell
# 1. Crear entorno virtual (si no existe)
python -m venv venv

# 2. Activar entorno virtual
.\venv\Scripts\activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Ejecutar aplicación en modo debug
python app.py
```

### Ejecución de Pruebas Automatizadas

```powershell
# Ejecuta la suite de pruebas completa en SQLite aislado
python test_app.py
```

### Sistema de Migraciones de Base de Datos

El proyecto cuenta con un sistema de migraciones **idempotente y automático** al arrancar la aplicación (`app.py:123-176`).

**Para agregar una nueva columna a la base de datos**:
1. Añadir el campo a la clase del modelo en `app.py` (ej. `campo = db.Column(db.String(50))`).
2. Añadir una entrada al array `MIGRACIONES` en `app.py`:
   ```python
   MIGRACIONES = [
       # ... migraciones previas ...
       ("tabla.campo", lambda: agregar_columna('tabla', 'campo', "VARCHAR(50) DEFAULT 'valor'")),
   ]
   ```
3. Ejecutar `python test_app.py` para validar que la migración se aplica correctamente tanto en SQLite como en PostgreSQL.

### Despliegue en Producción (Fly.io)

```bash
# Iniciar sesión en Fly
flyctl auth login

# Desplegar cambios
flyctl deploy

# Configurar variables de entorno y secretos
flyctl secrets set DATABASE_URL="postgresql://usuario:pass@host:5432/dbname"
flyctl secrets set SECRET_KEY="clave_secreta_super_segura"

# Ver logs en tiempo real
flyctl logs
```

---

## 6. Reglas Estrictas y Lineamientos para Agentes de IA

Cualquier agente de IA o desarrollador que opere en este repositorio debe cumplir rigurosamente las siguientes directrices:

1. **Preservar la Arquitectura Monolítica**: No fragmentar `app.py` en múltiples submódulos o carpetas a menos que el usuario lo solicite explícitamente.
2. **Consistencia de Esquema y Migraciones**: Todo cambio de modelo debe ir acompañado de su migración en la lista `MIGRACIONES` y de un test correspondiente en `test_app.py`.
3. **Control de Slots (`perfiles_ocupados`)**:
   - `perfiles_ocupados` jamás debe superar `perfiles_maximos`.
   - Cualquier operación que cree, traslade, elimine o renueve una venta debe sincronizar los slots o apoyarse en `reconciliar_slots()`.
4. **Modales de Bootstrap**: Colocar siempre los modales dentro del `<td>` de la fila o después del cierre de `</table>`. Nunca como hijos directos de `<tbody>` o `<tr>`.
5. **Normalización Telefónica**: Mantener la separación entre `telefono_original` (presentación al usuario) y `telefono_limpio` (solo dígitos para la API de WhatsApp).
6. **No Alterar Archivos de Producción en Local**: No escribir datos reales ni credenciales en `instance/panel_ventas.db` ni en `fly.toml`.
7. **Verificación Obligatoria**: Antes de dar por concluida cualquier modificación funcional, ejecutar mentalmente o validar los casos de prueba de `test_app.py`.
