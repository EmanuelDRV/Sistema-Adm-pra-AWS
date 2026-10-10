#!/bin/bash
set -e

echo "========================================="
echo " Iniciando despliegue de AdmNachtShop "
echo "========================================="

# Verificar si Docker está instalado
if ! command -v docker &> /dev/null; then
    echo "Instalando Docker y Docker Compose..."
    sudo apt update && sudo apt upgrade -y
    sudo apt install -y docker.io docker-compose git
    sudo systemctl enable --now docker
    sudo usermod -aG docker ubuntu
    echo "--------------------------------------------------------"
    echo "✅ Docker instalado exitosamente."
    echo "⚠️  IMPORTANTE: Cierra esta consola, vuelve a entrar por SSH y ejecuta este script nuevamente."
    echo "--------------------------------------------------------"
    exit 1
fi

# Comprobar si existe el archivo .env
if [ ! -f .env ]; then
    echo "Falta el archivo .env. Copiando .env.example a .env..."
    cp .env.example .env
    echo "--------------------------------------------------------"
    echo "⚠️  Por favor, edita el archivo .env con tus datos reales:"
    echo "    nano .env"
    echo "    (Cambia el DOMAIN por tu dominio real, y las contraseñas)"
    echo "Luego, vuelve a ejecutar este script."
    echo "--------------------------------------------------------"
    exit 1
fi

echo "Levantando contenedores con Docker Compose..."
sudo docker-compose up -d --build

echo "Esperando 10 segundos a que la base de datos inicie..."
sleep 10

echo "========================================="
echo "✅ Contenedores en línea."
echo "Para restaurar tu base de datos desde el backup, ejecuta el siguiente comando:"
echo "sudo docker exec -i \$(sudo docker-compose ps -q db) psql -U admnacht_user -d admnacht_db < \"backup db real/backup_plano.sql\""
echo "========================================="
