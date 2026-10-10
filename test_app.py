# -*- coding: utf-8 -*-
r"""Smoke test integral del sistema AdmNachtShop.
Usa una DB temporal (instance/test_panel_ventas.db) - NO toca datos reales.
Uso: venv\Scripts\python test_app.py
"""
import os
import re
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
TEST_DB = os.path.join(BASE, "instance", "test_panel_ventas.db")

os.environ["DATABASE_URL"] = "sqlite:///test_panel_ventas.db"
if os.path.exists(TEST_DB):
    os.remove(TEST_DB)

sys.path.insert(0, BASE)
from datetime import datetime, timedelta
from app import app, db
from app import Vendedor, Cliente, Cuenta, Venta, Plantilla

PASS = 0
FAIL = 0


def check(nombre, condicion, detalle=""):
    global PASS, FAIL
    if condicion:
        PASS += 1
        print(f"  OK   {nombre}")
    else:
        FAIL += 1
        print(f"  FAIL {nombre}  {detalle}")


def fresh(cls, obj_id):
    db.session.expire_all()
    return cls.query.get(obj_id)


client = app.test_client()
now = datetime.utcnow()
hoy = now.strftime("%Y-%m-%d")

ctx = app.app_context()
ctx.push()

print("\n=== 1. PAGINAS PRINCIPALES (GET) ===")
paginas = [
    ("/", b"AdmNachtShop"),
    ("/ventas", b"Gesti"),
    ("/cuentas", b"Cuentas"),
    ("/clientes", b"Clientes"),
    ("/vendedores", b"Vendedores"),
    ("/finanzas", b"Finanzas"),
    ("/plantillas", b"Plantillas"),
]
for url, marcador in paginas:
    r = client.get(url, follow_redirects=True)
    check(f"GET {url}", r.status_code == 200 and marcador in r.data, f"status={r.status_code}")

r = client.get("/api/buscar_clientes?q=z")
check("GET /api/buscar_clientes", r.status_code == 200 and "resultados" in r.get_data(as_text=True))

print("\n=== 2. VENDEDORES ===")
client.post("/vendedores/nuevo", data={"nombre": "juan perez", "contacto": "600111222"}, follow_redirects=True)
v = Vendedor.query.filter_by(nombre="Juan Perez").first()
check("Crear vendedor", v is not None, str(v))
v_id = v.id
client.post(f"/vendedores/editar/{v_id}", data={"nombre": "juan carlos", "contacto": "600333444"}, follow_redirects=True)
v = fresh(Vendedor, v_id)
check("Editar vendedor", v.nombre == "Juan Carlos", v.nombre)
client.post(f"/vendedores/eliminar/{v_id}", follow_redirects=True)
check("Eliminar vendedor sin ventas", Vendedor.query.get(v_id) is None)
client.post("/vendedores/nuevo", data={"nombre": "test ventas", "contacto": "600555666"}, follow_redirects=True)
v_con_ventas = Vendedor.query.filter_by(nombre="Test Ventas").first()
v_con_ventas_id = v_con_ventas.id

print("\n=== 3. CUENTAS ===")
def crear_cuenta(correo, perfiles, plataforma="Netflix"):
    client.post("/cuentas/nueva", data={
        "tipo": "Streaming", "plataforma": plataforma, "correo_o_usuario": correo,
        "contrasena": "pass1", "perfiles_maximos": str(perfiles), "vendedor_id": str(v_con_ventas_id),
        "dias_duracion": "30", "costo": "10.5", "moneda_costo": "USDT",
    }, follow_redirects=True)
    db.session.expire_all()
    return Cuenta.query.filter_by(correo=correo).first()

c1 = crear_cuenta("a@test.com", 3)
c2 = crear_cuenta("b@test.com", 2)
c3 = crear_cuenta("c@test.com", 1)
check("Crear 3 cuentas", c1 and c2 and c3 and c1.perfiles_ocupados == 0, "ok" if c1 else "FALTA")

client.post("/cuentas/nueva", data={
    "tipo": "Streaming", "plataforma": "Netflix", "correo_o_usuario": "b@test.com",
    "contrasena": "x", "perfiles_maximos": "2", "vendedor_id": str(v_con_ventas_id),
    "dias_duracion": "30", "costo": "5", "moneda_costo": "USDT",
}, follow_redirects=True)
check("Duplicado plataforma+correo (rechazado)", Cuenta.query.filter_by(correo="b@test.com").count() == 1)

client.post(f"/cuentas/editar/{c1.id}", data={
    "correo_o_usuario": "a@test.com", "contrasena": "passx", "perfiles_maximos": "3",
    "vendedor_id": str(v_con_ventas_id), "dias_duracion": "45", "costo": "12",
    "moneda_costo": "USDT", "estado": "Inactivo",
}, follow_redirects=True)
c1 = fresh(Cuenta, c1.id)
check("Editar cuenta (correo+estado)", c1.correo == "a@test.com" and c1.estado == "Inactivo" and c1.perfiles_maximos == 3, c1.correo)

client.post(f"/cuentas/renovar/{c1.id}", data={
    "dias_duracion": "30", "ultimo_costo_renovacion": "9.99",
    "ultima_moneda_renovacion": "USDT", "vendedor_renovacion_id": str(v_con_ventas_id),
}, follow_redirects=True)
c1 = fresh(Cuenta, c1.id)
check("Renovar cuenta (reactiva+extiende)", c1.estado == "Activo" and c1.ultimo_costo_renovacion == 9.99 and c1.fecha_vencimiento_cuenta > now, c1.estado)

client.post(f"/cuentas/cambiar_estado/{c1.id}", data={}, follow_redirects=True)
c1 = fresh(Cuenta, c1.id)
check("Switch estado -> Inactivo (sin checkbox)", c1.estado == "Inactivo", c1.estado)
client.post(f"/cuentas/cambiar_estado/{c1.id}", data={"estado": "1"}, follow_redirects=True)
c1 = fresh(Cuenta, c1.id)
check("Switch estado -> Activo (con checkbox)", c1.estado == "Activo", c1.estado)

print("\n=== 3.5 AUTO-DESACTIVACION DE CUENTA VENCIDA ===")
client.post("/cuentas/nueva", data={
    "tipo": "Streaming", "plataforma": "Netflix", "correo_o_usuario": "vencida@test.com",
    "contrasena": "pass", "perfiles_maximos": "1", "vendedor_id": str(v_con_ventas_id),
    "dias_duracion": "-1", "costo": "5", "moneda_costo": "USDT",
})
cv = Cuenta.query.filter_by(correo="vencida@test.com").first()
check("Cuenta vencida creada (Activo en DB)", cv is not None and cv.estado == "Activo", str(cv and cv.estado))
cv_id = cv.id

client.get("/cuentas", follow_redirects=True)
cv = fresh(Cuenta, cv_id)
check("Auto-desactivacion: vencida -> Inactivo", cv.estado == "Inactivo", cv.estado)

r = client.get("/ventas", follow_redirects=True)
check("Cuenta vencida NO aparece en registro de ventas", b"vencida@test.com" not in r.data, "aparece en dropdown")

ventas_antes = Venta.query.filter_by(cuenta_id=cv_id).count()
r = client.post("/ventas/nueva", data={
    "tipo_cliente": "nuevo", "nombre_nuevo": "cliente vencida", "telefono_nuevo": "600999111",
    "vendedor_id": str(v_con_ventas_id), "fecha_inicio": hoy,
    "cuenta_id[]": [str(cv_id)], "nombre_perfil[]": ["P"], "pin[]": [""],
    "dias[]": ["30"], "precio[]": ["5"], "moneda[]": ["BOB"],
}, follow_redirects=True)
cv = fresh(Cuenta, cv_id)
check("Venta sobre cuenta vencida (rechazada)", cv.perfiles_ocupados == 0 and Venta.query.filter_by(cuenta_id=cv_id).count() == ventas_antes,
      f"slots={cv.perfiles_ocupados}")

print("\n=== 4. CLIENTES (creados via venta) ===")
r = client.post("/ventas/nueva", data={
    "tipo_cliente": "nuevo", "nombre_nuevo": "ana maria", "telefono_nuevo": "+591 700-1234",
    "vendedor_id": str(v_con_ventas_id), "fecha_inicio": hoy,
    "cuenta_id[]": [str(c1.id)], "nombre_perfil[]": [""], "pin[]": ["1234"],
    "dias[]": ["30"], "precio[]": ["25.5"], "moneda[]": ["BOB"],
}, follow_redirects=True)
db.session.expire_all()
cl1 = Cliente.query.filter_by(nombre="Ana Maria").first()
check("Venta crea cliente nuevo", cl1 is not None, str(cl1 and cl1.id))
check("Telefono normalizado", cl1.telefono_limpio == "5917001234", cl1.telefono_limpio)
v1 = Venta.query.filter_by(cliente_id=cl1.id).first()
c1 = fresh(Cuenta, c1.id)
check("Venta creada + slot ocupado", v1 is not None and c1.perfiles_ocupados == 1, f"slots={c1.perfiles_ocupados}")

client.post(f"/clientes/editar/{cl1.id}", data={"nombre": "ana maria gomez", "telefono_original": "5917005678"}, follow_redirects=True)
cl1 = fresh(Cliente, cl1.id)
check("Editar cliente", cl1.nombre == "Ana Maria Gomez" and cl1.telefono_limpio == "5917005678", cl1.nombre)

client.post(f"/clientes/eliminar/{cl1.id}", follow_redirects=True)
check("Eliminar cliente con ventas (denegado)", Cliente.query.get(cl1.id) is not None)

print("\n=== 5. VENTAS: MULTI, VALIDACIONES, MUDANZA ===")
client.post("/ventas/nueva", data={
    "tipo_cliente": "existente", "cliente_id": str(cl1.id),
    "vendedor_id": str(v_con_ventas_id), "fecha_inicio": hoy,
    "cuenta_id[]": [str(c1.id), str(c2.id)],
    "nombre_perfil[]": ["PerfilA", "PerfilB"],
    "pin[]": ["", "4321"],
    "dias[]": ["30", "60"],
    "precio[]": ["10", "20"],
    "moneda[]": ["USDT", "USDT"],
}, follow_redirects=True)
c1 = fresh(Cuenta, c1.id)
c2 = fresh(Cuenta, c2.id)
check("Venta multi (2 plataformas)", c1.perfiles_ocupados == 2 and c2.perfiles_ocupados == 1, f"A={c1.perfiles_ocupados} B={c2.perfiles_ocupados}")

r = client.post("/ventas/nueva", data={
    "tipo_cliente": "existente", "cliente_id": str(cl1.id),
    "vendedor_id": str(v_con_ventas_id), "fecha_inicio": hoy,
    "cuenta_id[]": [str(c1.id)], "nombre_perfil[]": ["SinPrecio"],
    "pin[]": [""], "dias[]": ["30"], "precio[]": [""], "moneda[]": ["BOB"],
}, follow_redirects=True)
c1 = fresh(Cuenta, c1.id)
check("Venta con precio vacio (fila omitida, sin 500)",
      r.status_code == 200 and c1.perfiles_ocupados == 2 and b"Datos inv" in r.data,
      f"status={r.status_code} slots={c1.perfiles_ocupados}")

client.post("/ventas/nueva", data={
    "tipo_cliente": "existente", "cliente_id": str(cl1.id),
    "vendedor_id": str(v_con_ventas_id), "fecha_inicio": hoy,
    "cuenta_id[]": [str(c2.id)], "nombre_perfil[]": ["LlenaB"],
    "pin[]": [""], "dias[]": ["30"], "precio[]": ["5"], "moneda[]": ["BOB"],
}, follow_redirects=True)
c2 = fresh(Cuenta, c2.id)
check("Venta 2 de 2 slots (llena cuenta B)", c2.perfiles_ocupados == 2, f"slots={c2.perfiles_ocupados}")

client.post("/ventas/nueva", data={
    "tipo_cliente": "existente", "cliente_id": str(cl1.id),
    "vendedor_id": str(v_con_ventas_id), "fecha_inicio": hoy,
    "cuenta_id[]": [str(c2.id)], "nombre_perfil[]": ["SobrepasaB"],
    "pin[]": [""], "dias[]": ["30"], "precio[]": ["5"], "moneda[]": ["BOB"],
}, follow_redirects=True)
c2 = fresh(Cuenta, c2.id)
check("Venta en cuenta llena (rechazada)", c2.perfiles_ocupados == 2, f"slots={c2.perfiles_ocupados}")

client.post("/ventas/nueva", data={
    "tipo_cliente": "existente", "cliente_id": str(cl1.id),
    "vendedor_id": str(v_con_ventas_id), "fecha_inicio": hoy,
    "cuenta_id[]": [str(c1.id)], "nombre_perfil[]": ["TercerSlot"],
    "pin[]": [""], "dias[]": ["30"], "precio[]": ["5"], "moneda[]": ["BOB"],
}, follow_redirects=True)
c1 = fresh(Cuenta, c1.id)
check("Venta en slot libre (3 de 3)", c1.perfiles_ocupados == 3, f"slots={c1.perfiles_ocupados}")

v2 = Venta.query.filter_by(cuenta_id=c2.id).first()
client.post(f"/ventas/renovar/{v2.id}", data={"dias": "30", "precio": "22", "moneda": "BOB", "hacer_mudanza": "no"}, follow_redirects=True)
v2 = fresh(Venta, v2.id)
check("Renovar venta (extiende)", v2.fecha_vencimiento > now + timedelta(days=45) and v2.precio == 22, str(v2.fecha_vencimiento))

client.post(f"/ventas/editar/{v2.id}", data={
    "nombre_perfil": "PerfilNuevo", "pin": "9999", "precio": "33.33", "moneda": "ARS",
    "fecha_vencimiento": (now + timedelta(days=10)).strftime("%Y-%m-%d"),
}, follow_redirects=True)
v2 = fresh(Venta, v2.id)
check("Editar venta", v2.nombre_perfil == "PerfilNuevo" and v2.moneda == "ARS" and v2.precio == 33.33, v2.nombre_perfil)

client.post(f"/ventas/mudanza/{v2.id}", data={"nueva_cuenta_id": str(c1.id)}, follow_redirects=True)
v2 = fresh(Venta, v2.id)
check("Mudanza a cuenta llena (rechazada)", v2.cuenta_id == c2.id, f"cuenta={v2.cuenta_id}")

client.post(f"/ventas/mudanza/{v2.id}", data={"nueva_cuenta_id": str(c3.id)}, follow_redirects=True)
v2 = fresh(Venta, v2.id)
c2 = fresh(Cuenta, c2.id)
c3 = fresh(Cuenta, c3.id)
check("Mudanza exitosa (cambia cuenta + slots)", v2.cuenta_id == c3.id and c2.perfiles_ocupados == 1 and c3.perfiles_ocupados == 1,
      f"venta->c{v2.cuenta_id} B={c2.perfiles_ocupados} C={c3.perfiles_ocupados}")

print("\n=== 6. WHATSAPP ===")
r = client.get(f"/whatsapp/entrega/{v1.id}")
check("Link WhatsApp entrega", r.status_code == 302 and "wa.me" in r.headers.get("Location", ""), r.headers.get("Location", "")[:60])
r = client.get(f"/whatsapp/recordatorio/{v1.id}")
check("Link WhatsApp recordatorio", r.status_code == 302 and "wa.me" in r.headers.get("Location", ""))
r = client.get(f"/whatsapp/entrega/{v2.id}")
check("WhatsApp con pin en mensaje", r.status_code == 302)

print("\n=== 7. AUTO-LIMPIEZA Y REACTIVACION ===")
c4 = crear_cuenta("d@test.com", 1)
v_exp = Venta(cliente_id=cl1.id, cuenta_id=c4.id, vendedor_id=v_con_ventas_id,
              nombre_perfil="Vencida", dias_suscripcion=1,
              fecha_inicio=now - timedelta(days=40), fecha_vencimiento=now - timedelta(days=10),
              precio=5, moneda="BOB")
c4.perfiles_ocupados += 1
db.session.add(v_exp)
db.session.commit()
c4 = fresh(Cuenta, c4.id)
check("Venta vencida creada (slot=1)", c4.perfiles_ocupados == 1, f"slots={c4.perfiles_ocupados}")

client.get("/ventas", follow_redirects=True)
v_exp = fresh(Venta, v_exp.id)
c4 = fresh(Cuenta, c4.id)
check("Auto-limpieza: Inactiva + libera slot", v_exp.estado == "Inactiva" and c4.perfiles_ocupados == 0,
      f"estado={v_exp.estado} slots={c4.perfiles_ocupados}")

client.post(f"/ventas/renovar/{v_exp.id}", data={"dias": "30", "precio": "15", "moneda": "BOB", "hacer_mudanza": "no"}, follow_redirects=True)
v_exp = fresh(Venta, v_exp.id)
c4 = fresh(Cuenta, c4.id)
check("Reactivar venta inactiva", v_exp.estado == "Activa" and c4.perfiles_ocupados == 1, f"slots={c4.perfiles_ocupados}")

c5 = crear_cuenta("e@test.com", 1)
client.post("/ventas/nueva", data={
    "tipo_cliente": "existente", "cliente_id": str(cl1.id),
    "vendedor_id": str(v_con_ventas_id), "fecha_inicio": hoy,
    "cuenta_id[]": [str(c5.id)], "nombre_perfil[]": ["V6"], "pin[]": [""],
    "dias[]": ["30"], "precio[]": ["10"], "moneda[]": ["BOB"],
}, follow_redirects=True)
v6 = Venta.query.filter_by(cuenta_id=c5.id).first()
v6.estado = "Inactiva"
c5.perfiles_ocupados -= 1
db.session.commit()
client.post("/ventas/nueva", data={
    "tipo_cliente": "existente", "cliente_id": str(cl1.id),
    "vendedor_id": str(v_con_ventas_id), "fecha_inicio": hoy,
    "cuenta_id[]": [str(c5.id)], "nombre_perfil[]": ["V7"], "pin[]": [""],
    "dias[]": ["30"], "precio[]": ["10"], "moneda[]": ["BOB"],
}, follow_redirects=True)
r = client.post(f"/ventas/renovar/{v6.id}", data={"dias": "30", "precio": "10", "moneda": "BOB", "hacer_mudanza": "no"}, follow_redirects=True)
v6 = fresh(Venta, v6.id)
check("Reactivar sin cupo (exige mudanza)", v6.estado == "Inactiva" and b"mudanza obligatoria" in r.data.lower(),
      f"estado={v6.estado}")

print("\n=== 8. ELIMINACION DE VENTA, CLIENTE, VENDEDOR, CUENTA ===")
client.post(f"/ventas/eliminar/{v2.id}", follow_redirects=True)
c3 = fresh(Cuenta, c3.id)
check("Eliminar venta (libera slot)", Venta.query.get(v2.id) is None and c3.perfiles_ocupados == 0,
      f"slots={c3.perfiles_ocupados}")

client.post(f"/clientes/eliminar/{cl1.id}", follow_redirects=True)
check("Eliminar cliente con historial (denegado)", Cliente.query.get(cl1.id) is not None)

client.post(f"/vendedores/eliminar/{v_con_ventas_id}", follow_redirects=True)
check("Eliminar vendedor con ventas (denegado)", Vendedor.query.get(v_con_ventas_id) is not None)

client.post(f"/cuentas/eliminar/{c2.id}", follow_redirects=True)
check("Eliminar cuenta con ventas (denegado)", Cuenta.query.get(c2.id) is not None)

c6 = crear_cuenta("f@test.com", 1)
client.post(f"/cuentas/eliminar/{c6.id}", follow_redirects=True)
check("Eliminar cuenta sin ventas", Cuenta.query.get(c6.id) is None)

print("\n=== 8.5 ORDEN POR REGISTRO RECIENTE ===")
c_ord = crear_cuenta("ord@test.com", 2)
client.post("/ventas/nueva", data={
    "tipo_cliente": "existente", "cliente_id": str(cl1.id),
    "vendedor_id": str(v_con_ventas_id), "fecha_inicio": (now - timedelta(days=5)).strftime("%Y-%m-%d"),
    "cuenta_id[]": [str(c_ord.id)], "nombre_perfil[]": ["PerfilAntiguo"], "pin[]": [""],
    "dias[]": ["60"], "precio[]": ["10"], "moneda[]": ["BOB"],
}, follow_redirects=True)
client.post("/ventas/nueva", data={
    "tipo_cliente": "existente", "cliente_id": str(cl1.id),
    "vendedor_id": str(v_con_ventas_id), "fecha_inicio": hoy,
    "cuenta_id[]": [str(c_ord.id)], "nombre_perfil[]": ["PerfilReciente"], "pin[]": [""],
    "dias[]": ["30"], "precio[]": ["10"], "moneda[]": ["BOB"],
}, follow_redirects=True)
r = client.get("/ventas", follow_redirects=True)
idx_reciente = r.data.find(b"PerfilReciente")
idx_antiguo = r.data.find(b"PerfilAntiguo")
check("Ventas ordenadas por registro reciente (reciente primero)",
      idx_reciente != -1 and idx_antiguo != -1 and idx_reciente < idx_antiguo,
      f"reciente={idx_reciente} antiguo={idx_antiguo}")

print("\n=== 9. BUSCADORES ===")
r = client.get("/ventas?q=ana", follow_redirects=True)
check("Buscar ventas por cliente", r.status_code == 200 and b"Ana" in r.data)
check("Buscar ventas sin resultados (200)", client.get("/ventas?q=noexiste123", follow_redirects=True).status_code == 200)
r = client.get("/cuentas?q=test.com", follow_redirects=True)
check("Buscar cuentas por correo", r.status_code == 200 and b"test.com" in r.data)
check("Buscar cuentas vacio (200)", client.get("/cuentas?q=zzzznada", follow_redirects=True).status_code == 200)
check("Buscar transacciones (200)", client.get("/finanzas?q=ana", follow_redirects=True).status_code == 200)
check("Paginacion fuera de rango (200)", client.get("/ventas?page=99", follow_redirects=True).status_code == 200)

print("\n=== 10. PLANTILLAS ===")
p = Plantilla.query.filter_by(tipo="entrega_streaming").first()
client.post(f"/plantillas/editar/{p.id}", data={"mensaje": "Hola [CLIENTE] tu cuenta [PLATAFORMA] vence el [VENCIMIENTO]"}, follow_redirects=True)
check("Editar plantilla", Plantilla.query.get(p.id).mensaje.startswith("Hola [CLIENTE]"))

print("\n=== 11. FINANZAS CON DATOS ===")
r = client.get("/finanzas", follow_redirects=True)
check("Finanzas con datos (200)", r.status_code == 200)
check("Finanzas muestra totales", b"USDT" in r.data and b"BOB" in r.data)
check("Finanzas sin decimales crudos", not re.search(r"\d+\.\d{4,}", r.data.decode("utf-8")))

print("\n=== 12. CONSISTENCIA FINAL DE SLOTS ===")
inconsistente = 0
for cuenta in Cuenta.query.all():
    activas = Venta.query.filter_by(cuenta_id=cuenta.id, estado="Activa").count()
    if cuenta.perfiles_ocupados != activas:
        inconsistente += 1
        print(f"  DESFASE cuenta {cuenta.correo}: ocupados={cuenta.perfiles_ocupados} vs activas={activas}")
check("Slots = ventas activas en todas las cuentas", inconsistente == 0, f"{inconsistente} desfases")

print("\n" + "=" * 50)
print(f"RESULTADO: {PASS} OK / {FAIL} FAIL")
print("=" * 50)
ctx.pop()
sys.exit(1 if FAIL else 0)
