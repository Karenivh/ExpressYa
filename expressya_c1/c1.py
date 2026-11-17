"""ExpressYa C1: inventario, checkout con reserva de 300 s, vencimiento y concurrencia.

Integración C2: consultar PEDIDO/LINEA_PEDIDO/RESERVA; implementar pago en otro módulo.
No registra pagos ni confirma ventas: responsabilidad C2.
"""
import hashlib
import json
import os
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row

DURACION_RESERVA_SEGUNDOS = 300


class ErrorNegocio(Exception):
    """Error de validación o regla de negocio."""


def conectar():
    """Credenciales mediante variables de entorno, nunca dentro del código."""
    return psycopg.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "expressya_db"),
        user=os.getenv("PGUSER", "postgres"),
        password=os.environ["PGPASSWORD"],
        row_factory=dict_row,
    )


def disponibilidad(id_dark_store, sku):
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("""SELECT cantidad_registrada, cantidad_reservada, disponible
                       FROM inventario WHERE id_dark_store=%s AND sku=%s""", (id_dark_store, sku))
        fila = cur.fetchone()
        if fila is None:
            raise ErrorNegocio("INVENTARIO_NO_ENCONTRADO")
        return fila


def reconstruir_disponible_auditoria(id_inventario):
    """Devuelve el último saldo disponible registrado en la bitácora.

    Los movimientos de C1 guardan cantidad_anterior/cantidad_nueva como saldo
    disponible (registrado menos reservado), no como existencia física total.
    """
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT cantidad_nueva
               FROM auditoria_movimiento
               WHERE id_inventario=%s
               ORDER BY id_movimiento DESC
               LIMIT 1""",
            (id_inventario,),
        )
        fila = cur.fetchone()
        if fila is None:
            raise ErrorNegocio("AUDITORIA_SIN_MOVIMIENTOS")
        return fila["cantidad_nueva"]


def validar_reserva_vigente(id_reserva):
    """Comprueba en PostgreSQL que la reserva exista, siga creada y no haya vencido."""
    with conectar() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT estado_reserva, fecha_expiracion > clock_timestamp() AS vigente
               FROM reserva WHERE id_reserva=%s""",
            (id_reserva,),
        )
        fila = cur.fetchone()
        if fila is None:
            raise ErrorNegocio("RESERVA_NO_ENCONTRADA")
        return fila["estado_reserva"] == "CREADA" and fila["vigente"]


def registrar_inventario(id_dark_store, sku, cantidad, responsable="operador_prueba"):
    """Alta de stock para datos de prueba. En producción requiere autorización."""
    if not isinstance(cantidad, int) or cantidad < 0:
        raise ErrorNegocio("CANTIDAD_INVALIDA")
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO inventario(id_dark_store,sku,cantidad_registrada)
                       VALUES(%s,%s,%s) RETURNING id_inventario""", (id_dark_store, sku, cantidad))
        ident = cur.fetchone()["id_inventario"]
        cur.execute("""INSERT INTO auditoria_movimiento
                       (id_inventario,tipo_operacion,cantidad_anterior,cantidad_cambio,
                        cantidad_nueva,motivo,usuario_responsable)
                       VALUES(%s,'ALTA_INICIAL',0,%s,%s,'Carga de prueba',%s)""",
                    (ident, cantidad, cantidad, responsable))
        return ident


def _normalizar_items(items):
    """Un SKU por línea; agrupa cantidades duplicadas."""
    if not isinstance(items, list) or not items:
        raise ErrorNegocio("ITEMS_INVALIDOS")
    acumulado = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("sku"), str):
            raise ErrorNegocio("ITEMS_INVALIDOS")
        sku, cantidad = item["sku"], item.get("cantidad")
        if not sku or type(cantidad) is not int or cantidad <= 0:
            raise ErrorNegocio("CANTIDAD_INVALIDA")
        acumulado[sku] = acumulado.get(sku, 0) + cantidad
    return sorted(acumulado.items())


def iniciar_checkout(id_cliente, id_dark_store, items, clave_idempotencia):
    """Todo o nada, bloqueo de filas y clave única para reintentos/concurrencia."""
    if not str(id_cliente).strip() or not str(clave_idempotencia).strip():
        raise ErrorNegocio("DATOS_OBLIGATORIOS")
    normalizados = _normalizar_items(items)
    payload = json.dumps([str(id_cliente), id_dark_store, normalizados], separators=(",", ":"))
    huella = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    with conectar() as conn, conn.cursor() as cur:
        # Bloqueo transaccional por clave: evita carrera de dos solicitudes con la misma clave.
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (clave_idempotencia,))
        cur.execute("SELECT id_pedido,huella_solicitud,estado_pedido,total_pedido FROM pedido WHERE clave_idempotencia=%s", (clave_idempotencia,))
        existente = cur.fetchone()
        if existente:
            if existente["huella_solicitud"] != huella:
                raise ErrorNegocio("CLAVE_IDEMPOTENCIA_REUTILIZADA_CON_OTROS_DATOS")
            return _resultado_checkout(cur, existente["id_pedido"], repetido=True)

        cur.execute("SELECT estatus_activo FROM dark_store WHERE id_dark_store=%s", (id_dark_store,))
        tienda = cur.fetchone()
        if not tienda or not tienda["estatus_activo"]:
            raise ErrorNegocio("DARK_STORE_INACTIVA")

        # Orden consistente de bloqueos reduce riesgo de deadlocks entre carritos.
        cur.execute("""SELECT i.id_inventario,i.sku,i.disponible,p.precio_unitario,p.activo
                       FROM inventario i JOIN producto p ON p.sku=i.sku
                       WHERE i.id_dark_store=%s AND i.sku=ANY(%s)
                       ORDER BY i.id_inventario FOR UPDATE OF i""",
                    (id_dark_store, [sku for sku, _ in normalizados]))
        filas = {f["sku"]: f for f in cur.fetchall()}
        for sku, cantidad in normalizados:
            f = filas.get(sku)
            if f is None or not f["activo"] or f["disponible"] < cantidad:
                raise ErrorNegocio("STOCK_INSUFICIENTE")

        total = sum((filas[sku]["precio_unitario"] * cantidad for sku, cantidad in normalizados), Decimal("0"))
        cur.execute("""INSERT INTO pedido(id_cliente,id_dark_store,estado_pedido,total_pedido,clave_idempotencia,huella_solicitud)
                       VALUES(%s,%s,'PENDIENTE_DE_PAGO',%s,%s,%s) RETURNING id_pedido""",
                    (str(id_cliente), id_dark_store, total, clave_idempotencia, huella))
        id_pedido = cur.fetchone()["id_pedido"]
        # Hora de BD después de adquirir los bloqueos; no reloj del equipo cliente.
        cur.execute("SELECT clock_timestamp() AS ahora")
        ahora = cur.fetchone()["ahora"]
        from datetime import timedelta
        vence = ahora + timedelta(seconds=DURACION_RESERVA_SEGUNDOS)
        for sku, cantidad in normalizados:
            f = filas[sku]
            cur.execute("""INSERT INTO linea_pedido(id_pedido,sku,cantidad_solicitada)
                           VALUES(%s,%s,%s) RETURNING id_linea_pedido""", (id_pedido, sku, cantidad))
            linea = cur.fetchone()["id_linea_pedido"]
            cur.execute("""UPDATE inventario SET cantidad_reservada=cantidad_reservada+%s
                           WHERE id_inventario=%s RETURNING cantidad_reservada""", (cantidad, f["id_inventario"]))
            cur.execute("""INSERT INTO reserva(id_linea_pedido,id_inventario,cantidad_reservada,estado_reserva,fecha_creacion,fecha_expiracion)
                           VALUES(%s,%s,%s,'CREADA',%s,%s)""",
                        (linea, f["id_inventario"], cantidad, ahora, vence))
            # Auditoría del saldo reservado, no del saldo registrado.
            cur.execute("""INSERT INTO auditoria_movimiento
                           (id_inventario,tipo_operacion,cantidad_anterior,cantidad_cambio,cantidad_nueva,motivo,usuario_responsable)
                           VALUES(%s,'RESERVA',%s,%s,%s,%s,%s)""",
                        (f["id_inventario"], f["disponible"], -cantidad,
                         f["disponible"] - cantidad, f'Pedido {id_pedido}: disminución de disponible', str(id_cliente)))
        return _resultado_checkout(cur, id_pedido, repetido=False)


def _resultado_checkout(cur, id_pedido, repetido):
    cur.execute("""SELECT p.id_pedido,p.estado_pedido,p.total_pedido,
                          r.id_reserva,r.estado_reserva,r.fecha_expiracion
                   FROM pedido p JOIN linea_pedido l ON l.id_pedido=p.id_pedido
                   JOIN reserva r ON r.id_linea_pedido=l.id_linea_pedido
                   WHERE p.id_pedido=%s ORDER BY r.id_reserva""", (id_pedido,))
    filas = cur.fetchall()
    return {
        "idPedido": id_pedido,
        "estado": filas[0]["estado_pedido"],
        "montoTotal": str(filas[0]["total_pedido"]),
        "reservas": [{"idReserva": f["id_reserva"], "estado": f["estado_reserva"],
                      "tiempoExpiracion": f["fecha_expiracion"].isoformat()} for f in filas],
        "reintento": repetido,
    }


def _cerrar_reserva(cur, id_reserva, nuevo_estado, motivo):
    """Cierra reserva con bloqueo; no permite doble liberación."""
    cur.execute("SELECT id_inventario FROM reserva WHERE id_reserva=%s", (id_reserva,))
    encontrada = cur.fetchone()
    if not encontrada:
        raise ErrorNegocio("RESERVA_NO_ENCONTRADA")
    # Mismo orden de bloqueos que checkout: primero inventario, luego reserva.
    cur.execute("SELECT disponible FROM inventario WHERE id_inventario=%s FOR UPDATE", (encontrada["id_inventario"],))
    anterior = cur.fetchone()["disponible"]
    cur.execute("SELECT * FROM reserva WHERE id_reserva=%s FOR UPDATE", (id_reserva,))
    reserva = cur.fetchone()
    if reserva["estado_reserva"] != "CREADA":
        return False
    if nuevo_estado == "LIBERADA":
        cur.execute("SELECT clock_timestamp() AS ahora")
        if reserva["fecha_expiracion"] <= cur.fetchone()["ahora"]:
            nuevo_estado = "VENCIDA"
    cur.execute("UPDATE reserva SET estado_reserva=%s WHERE id_reserva=%s", (nuevo_estado, id_reserva))
    cur.execute("""UPDATE inventario SET cantidad_reservada=cantidad_reservada-%s
                   WHERE id_inventario=%s""", (reserva["cantidad_reservada"], reserva["id_inventario"]))
    cur.execute("""INSERT INTO auditoria_movimiento
                   (id_inventario,tipo_operacion,cantidad_anterior,cantidad_cambio,cantidad_nueva,motivo,usuario_responsable)
                   VALUES(%s,%s,%s,%s,%s,%s,'sistema')""",
                (reserva["id_inventario"], nuevo_estado, anterior, reserva["cantidad_reservada"],
                 anterior + reserva["cantidad_reservada"], motivo))
    return True


def liberar_reserva(id_reserva, motivo="Liberación solicitada"):
    with conectar() as conn, conn.cursor() as cur:
        return _cerrar_reserva(cur, id_reserva, "LIBERADA", motivo)


def vencer_reservas():
    """Ejecutar periódicamente desde proceso programado; también antes de checkout si se desea."""
    procesadas = 0
    with conectar() as conn, conn.cursor() as cur:
        cur.execute("""SELECT id_reserva FROM reserva
                       WHERE estado_reserva='CREADA' AND fecha_expiracion<=clock_timestamp()
                       ORDER BY id_inventario,id_reserva""")
        ids = [f["id_reserva"] for f in cur.fetchall()]
        for ident in ids:
            # Revalidación dentro del bloqueo para carreras con pagos.
            cur.execute("SELECT fecha_expiracion FROM reserva WHERE id_reserva=%s", (ident,))
            fecha = cur.fetchone()["fecha_expiracion"]
            cur.execute("SELECT clock_timestamp() AS ahora")
            if fecha <= cur.fetchone()["ahora"]:
                if _cerrar_reserva(cur, ident, "VENCIDA", "Vencimiento de 300 segundos"):
                    procesadas += 1
    return procesadas
