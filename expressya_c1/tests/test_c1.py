"""Pruebas de integración reales contra PostgreSQL; base de pruebas separada."""
import os
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import psycopg
from psycopg.rows import dict_row
from c1 import (conectar, disponibilidad, registrar_inventario, iniciar_checkout,
                liberar_reserva, vencer_reservas, ErrorNegocio,
                validar_reserva_vigente, reconstruir_disponible_auditoria)


@unittest.skipUnless(os.getenv('PGDATABASE', '').endswith('_test'),
                     'Por seguridad: PGDATABASE debe terminar en _test')
class C1Integracion(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:12]
        self.sku = 'TEST-' + self.tag
        with conectar() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO dark_store(nombre_tienda) VALUES(%s) RETURNING id_dark_store", ('Prueba ' + self.tag,))
            self.tienda = cur.fetchone()['id_dark_store']
            cur.execute("INSERT INTO producto(sku,nombre,precio_unitario) VALUES(%s,%s,10)", (self.sku, 'Producto prueba'))
        self.inv = registrar_inventario(self.tienda, self.sku, 1)

    def compra(self, clave=None, cantidad=1):
        return iniciar_checkout('cliente-test', self.tienda,
                               [{'sku': self.sku, 'cantidad': cantidad}], clave or uuid.uuid4().hex)

    def test_01_disponibilidad_inicial(self):
        self.assertEqual(disponibilidad(self.tienda, self.sku)['disponible'], 1)

    def test_02_reserva_300_segundos(self):
        r = self.compra()
        ident = r['reservas'][0]['idReserva']
        with conectar() as conn, conn.cursor() as cur:
            cur.execute("""SELECT EXTRACT(EPOCH FROM (fecha_expiracion-fecha_creacion)) AS duracion
                           FROM reserva WHERE id_reserva=%s""", (ident,))
            self.assertEqual(cur.fetchone()['duracion'], 300)
        self.assertTrue(validar_reserva_vigente(ident))
        self.assertEqual(disponibilidad(self.tienda, self.sku)['disponible'], 0)

    def test_03_stock_insuficiente(self):
        with self.assertRaisesRegex(ErrorNegocio, 'STOCK_INSUFICIENTE'):
            self.compra(cantidad=2)
        self.assertEqual(disponibilidad(self.tienda, self.sku)['cantidad_reservada'], 0)

    def test_04_idempotencia_misma_clave(self):
        clave = uuid.uuid4().hex
        a, b = self.compra(clave), self.compra(clave)
        self.assertEqual(a['idPedido'], b['idPedido'])
        self.assertTrue(b['reintento'])
        self.assertEqual(disponibilidad(self.tienda, self.sku)['cantidad_reservada'], 1)

    def test_05_idempotencia_datos_distintos(self):
        clave = uuid.uuid4().hex
        self.compra(clave)
        with self.assertRaisesRegex(ErrorNegocio, 'CLAVE_IDEMPOTENCIA_REUTILIZADA'):
            self.compra(clave, cantidad=2)

    def test_06_liberacion_una_vez(self):
        r = self.compra()
        ident = r['reservas'][0]['idReserva']
        self.assertTrue(liberar_reserva(ident))
        self.assertFalse(liberar_reserva(ident))
        self.assertEqual(disponibilidad(self.tienda, self.sku)['disponible'], 1)

    def test_07_vencimiento(self):
        r = self.compra()
        ident = r['reservas'][0]['idReserva']
        # Primero verifica el plazo exacto; luego adelanta el reloj lógico sólo
        # en la BD de pruebas para probar el proceso sin esperar cinco minutos.
        with conectar() as conn, conn.cursor() as cur:
            cur.execute("""SELECT EXTRACT(EPOCH FROM (fecha_expiracion-fecha_creacion)) AS duracion
                           FROM reserva WHERE id_reserva=%s""", (ident,))
            self.assertEqual(cur.fetchone()['duracion'], 300)
            cur.execute("""UPDATE reserva
                           SET fecha_expiracion=clock_timestamp()-interval '1 second'
                           WHERE id_reserva=%s""", (ident,))
        self.assertFalse(validar_reserva_vigente(ident))
        self.assertGreaterEqual(vencer_reservas(), 1)
        self.assertEqual(disponibilidad(self.tienda, self.sku)['disponible'], 1)
        with conectar() as conn, conn.cursor() as cur:
            cur.execute("SELECT estado_reserva FROM reserva WHERE id_reserva=%s", (ident,))
            self.assertEqual(cur.fetchone()['estado_reserva'], 'VENCIDA')

    def test_08_concurrencia_ultima_unidad(self):
        barrera = threading.Barrier(2)
        def comprar(i):
            barrera.wait(timeout=10)
            try:
                self.compra(str(i) + uuid.uuid4().hex)
                return 'OK'
            except ErrorNegocio as exc:
                return str(exc)
        with ThreadPoolExecutor(max_workers=2) as pool:
            resultados = list(pool.map(comprar, (1, 2)))
        self.assertEqual(resultados.count('OK'), 1, resultados)
        self.assertEqual(resultados.count('STOCK_INSUFICIENTE'), 1, resultados)
        self.assertEqual(disponibilidad(self.tienda, self.sku)['disponible'], 0)

    def test_09_reconstruccion_saldo_desde_auditoria(self):
        self.assertEqual(reconstruir_disponible_auditoria(self.inv), 1)
        reserva = self.compra()
        self.assertEqual(reconstruir_disponible_auditoria(self.inv), 0)
        liberar_reserva(reserva['reservas'][0]['idReserva'])
        self.assertEqual(reconstruir_disponible_auditoria(self.inv), 1)
        self.assertEqual(
            reconstruir_disponible_auditoria(self.inv),
            disponibilidad(self.tienda, self.sku)['disponible'],
        )

    def test_10_reserva_no_vigente_no_se_puede_usar(self):
        reserva = self.compra()['reservas'][0]['idReserva']
        with conectar() as conn, conn.cursor() as cur:
            cur.execute("""UPDATE reserva
                           SET fecha_expiracion=clock_timestamp()-interval '1 second'
                           WHERE id_reserva=%s""", (reserva,))
        self.assertFalse(validar_reserva_vigente(reserva))

    def test_11_auditoria_no_modificable(self):
        with conectar() as conn, conn.cursor() as cur:
            cur.execute('SELECT id_movimiento FROM auditoria_movimiento WHERE id_inventario=%s LIMIT 1', (self.inv,))
            ident = cur.fetchone()['id_movimiento']
        with self.assertRaises(psycopg.Error):
            with conectar() as conn, conn.cursor() as cur:
                cur.execute('DELETE FROM auditoria_movimiento WHERE id_movimiento=%s', (ident,))

    def test_12_tienda_inactiva(self):
        with conectar() as conn, conn.cursor() as cur:
            cur.execute('UPDATE dark_store SET estatus_activo=false WHERE id_dark_store=%s', (self.tienda,))
        with self.assertRaisesRegex(ErrorNegocio, 'DARK_STORE_INACTIVA'):
            self.compra()

    def test_13_no_stock_negativo(self):
        with self.assertRaises(psycopg.Error):
            with conectar() as conn, conn.cursor() as cur:
                cur.execute('UPDATE inventario SET cantidad_reservada=2 WHERE id_inventario=%s', (self.inv,))


if __name__ == '__main__':
    unittest.main()
