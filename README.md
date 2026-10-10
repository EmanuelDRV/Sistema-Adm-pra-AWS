# AdmNachtShop - Panel de Ventas y Streaming

Sistema integral para la gestión y venta de cuentas y perfiles de streaming, IPTV y servicios digitales con control automático de slots, suscripciones y finanzas multi-divisa.

---

## 🚀 Arquitectura y Tecnologías
- **Backend:** Python 3 + Flask 3.1 + SQLAlchemy 2.0
- **Base de Datos:** PostgreSQL 16 (Producción) / SQLite (Desarrollo local)
- **Frontend:** HTML5 + Bootstrap 5.3 (Dark Theme) + Jinja2 + Tom Select
- **Servidor WSGI & Proxy:** Gunicorn + Caddy (SSL automático Let's Encrypt)
- **Contenedores:** Docker + Docker Compose

---

## 🛠️ Estructura del Proyecto
```text
├── app.py                  # Aplicación principal Flask (modelos, rutas y lógica)
├── templates/              # Vistas HTML con Jinja2
│   ├── base.html           # Plantilla base y estilos
│   ├── dashboard.html      # Panel principal y KPIs
│   ├── ventas.html         # Control de ventas y perfiles
│   ├── cuentas.html        # Inventario de cuentas y slots
│   ├── clientes.html       # Directorio e historial de clientes
│   ├── vendedores.html     # Gestión de vendedores
│   ├── finanzas.html       # Balances y rentabilidad
│   └── plantillas.html     # Plantillas dinámicas de WhatsApp
├── backup db real/         # Backups de base de datos SQL para restauración
│   └── backup_plano.sql    # Volcado completo para restauración en PostgreSQL
├── Dockerfile              # Construcción del contenedor de la aplicación
├── docker-compose.yml      # Orquestación de App + PostgreSQL + Caddy SSL
├── Caddyfile               # Configuración de proxy inverso y certificados SSL
├── .env.example            # Plantilla de variables de entorno
├── deploy_aws.sh           # Script automatizado de despliegue en servidor AWS
├── test_app.py             # Suite de 59 pruebas automatizadas
└── requirements.txt        # Dependencias de Python
```

---

## 💻 Ejecución en Entorno Local

1. **Crear y activar entorno virtual:**
   ```powershell
   python -m venv venv
   .\venv\Scripts\activate
   ```

2. **Instalar dependencias:**
   ```powershell
   pip install -r requirements.txt
   ```

3. **Ejecutar pruebas unitarias:**
   ```powershell
   python test_app.py
   ```

4. **Iniciar servidor local:**
   ```powershell
   python app.py
   ```
   Abre tu navegador en `http://localhost:5000`.

---

## ☁️ Despliegue en AWS (Docker + SSL)

1. Crea una instancia en **AWS Lightsail** (Ubuntu 24.04 LTS / $5 USD/mes) y asígnale una **Static IP**.
2. Apunta tu dominio a la IP estática mediante un registro DNS tipo `A`.
3. Clona este repositorio en el servidor.
4. Copia y edita tus variables de entorno:
   ```bash
   cp .env.example .env
   nano .env
   ```
5. Inicia los servicios con el script de despliegue:
   ```bash
   chmod +x deploy_aws.sh
   ./deploy_aws.sh
   ```
6. Restaura la base de datos:
   ```bash
   sudo docker exec -i $(sudo docker-compose ps -q db) psql -U admnacht_user -d admnacht_db < "backup db real/backup_plano.sql"
   ```
