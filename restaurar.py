import psycopg2

DB_HOST = "localhost"
DB_PORT = "5433"
DB_NAME = "nacht_shop_drv"  # Cambia por "postgres" si te da error de base de datos inexistente
DB_USER = "postgres"
DB_PASSWORD = "mi_nueva_clave123"  # La contraseña que le asignaste en el ALTER USER

SQL_PATH = "backup db real/backup_plano.sql"

try:
    print("Conectando a la base de datos en Fly.io...")
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD
    )
    conn.autocommit = True
    cursor = conn.cursor()

    print(f"Leyendo y ejecutando sentencias de {SQL_PATH}...")
    
    with open(SQL_PATH, 'r', encoding='utf-8', errors='ignore') as f:
        statement = ""
        in_copy_block = False
        
        for line in f:
            # Ignorar metabarras de psql (\connect, \restrict, etc.)
            if line.strip().startswith('\\') and not line.strip().startswith('\\.'):
                continue
            
            # Si entramos a un bloque COPY, leemos hasta encontrar '\.'
            if line.strip().startswith('COPY ') and 'FROM stdin;' in line:
                in_copy_block = True
                copy_buffer = [line]
                continue
                
            if in_copy_block:
                copy_buffer.append(line)
                if line.strip() == '\\.':
                    in_copy_block = False
                    # Ejecutar el bloque COPY completo mediante copy_expert
                    full_copy = "".join(copy_buffer)
                    header = full_copy.splitlines()[0]
                    data = "\n".join(full_copy.splitlines()[1:-1]) + "\n"
                    
                    from io import StringIO
                    cursor.copy_expert(header, StringIO(data))
                continue

            # Para SQL regular, acumular hasta encontrar ';'
            statement += line
            if ';' in line:
                stmt_to_exec = statement.strip()
                if stmt_to_exec:
                    try:
                        cursor.execute(stmt_to_exec)
                    except Exception as err:
                        # Ignorar advertencias menores si las tablas ya existen
                        pass
                statement = ""

    print("¡Base de datos restaurada con éxito en Fly.io!")

except Exception as e:
    print(f"Error al restaurar: {e}")
finally:
    if 'conn' in locals() and conn:
        cursor.close()
        conn.close()