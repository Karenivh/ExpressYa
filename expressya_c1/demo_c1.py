"""Demostración C1 con cinco Dark Stores y productos de prueba ficticios."""
from uuid import uuid4
from c1 import (
    conectar,
    registrar_inventario,
    disponibilidad,
    iniciar_checkout,
    liberar_reserva,
    validar_reserva_vigente,
    reconstruir_disponible_auditoria,
)

# Datos exclusivamente demostrativos; no representan inventario real de ExpressYa.
productos = [
    (f"DEMO-{uuid4().hex[:8]}", "Producto de prueba A", 25.00),
    (f"DEMO-{uuid4().hex[:8]}", "Producto de prueba B", 40.00),
]

with conectar() as conn, conn.cursor() as cur:
    cur.executemany(
        "INSERT INTO producto(sku,nombre,precio_unitario) VALUES(%s,%s,%s)",
        productos,
    )
    tiendas = []
    for numero in range(1, 6):
        cur.execute(
            """INSERT INTO dark_store(nombre_tienda,direccion)
               VALUES(%s,%s) RETURNING id_dark_store""",
            (f"Dark Store DEMO {numero}", f"Dirección ficticia {numero}"),
        )
        tiendas.append(cur.fetchone()["id_dark_store"])

# Se carga cada producto en cada una de las cinco tiendas.
inventarios = {}
for id_tienda in tiendas:
    for sku, _, _ in productos:
        inventarios[(id_tienda, sku)] = registrar_inventario(id_tienda, sku, 3)

print("\n=== DISPONIBILIDAD INICIAL POR DARK STORE ===")
for numero, id_tienda in enumerate(tiendas, start=1):
    print(f"Dark Store {numero}:")
    for sku, nombre, _ in productos:
        print(f"  {nombre} ({sku}): {disponibilidad(id_tienda, sku)['disponible']} unidades")

# Se demuestra checkout, vigencia, liberación y reconstrucción de saldo.
id_tienda = tiendas[0]
sku_demo = productos[0][0]
id_inventario = inventarios[(id_tienda, sku_demo)]
print("\n=== PRUEBA DE RESERVA ===")
resultado = iniciar_checkout(
    "cliente-demo",
    id_tienda,
    [{"sku": sku_demo, "cantidad": 1}],
    str(uuid4()),
)
reserva = resultado["reservas"][0]["idReserva"]
print("CHECKOUT:", resultado)
print("RESERVA VIGENTE:", validar_reserva_vigente(reserva))
print("DISPONIBLE DURANTE LA RESERVA:", disponibilidad(id_tienda, sku_demo)["disponible"])
print("LIBERACIÓN:", liberar_reserva(reserva))
print("DISPONIBLE DESPUÉS DE LIBERAR:", disponibilidad(id_tienda, sku_demo)["disponible"])
print("SALDO RECONSTRUIDO DESDE AUDITORÍA:", reconstruir_disponible_auditoria(id_inventario))
