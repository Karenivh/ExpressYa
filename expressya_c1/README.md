# ExpressYa — Avance C1 (Python + PostgreSQL)
Este avance corresponde a la primera etapa funcional de la solución de inventario y reservas para ExpressYa. El objetivo principal de C1 es establecer la lógica base para gestionar la disponibilidad de productos en múltiples Dark Stores, controlar reservas temporales y validar la vigencia de las mismas antes de confirmar una operación comercial.

## Qué incluye

- Cinco Dark Stores ficticios para demostración.
- Carga inicial de dos productos por tienda.
- Consulta de disponibilidad por tienda y SKU.
- Gestión de reservas de 300 segundos.
- Validación de reserva vigente mediante la función       validar_reserva_vigente().
- Liberación y vencimiento de reservas.
- Auditoría de movimientos para reconstruir el último saldo disponible.
- Pruebas de integración con PostgreSQL, incluyendo casos de idempotencia, concurrencia y validación de stock

## Funcionalidades principales
- registrar_inventario: permite registrar stock inicial en un Dark Store.
- disponibilidad: consulta el saldo disponible en inventario.
- iniciar_checkout: crea un pedido con reservas temporales asociadas.
- liberar_reserva: libera una reserva activa sin duplicar efectos.
- vencer_reservas: identifica y procesa reservas vencidas.
- validar_reserva_vigente: comprueba si una reserva sigue vigente.
- reconstruir_disponible_auditoria: reconstruye el último estado de disponibilidad desde la auditoría.

## Reglas de negocio implementadas
- Cada reserva tiene una duración fija de 300 segundos.
- El inventario se bloquea transaccionalmente durante el checkout.
- La clave de idempotencia evita reintentos con datos distintos.
- La auditoría registra los cambios de disponibilidad y las operaciones relevantes.
- La validación de vigencia evita reutilizar reservas vencidas o liberadas.

## Instalación en Windows (Anaconda/VS Code)

1. Descomprimir ZIP en la carpeta del proyecto; activar entorno `conda activate expressYa`.
2. `python -m pip install -r requirements.txt`
3. Desde la terminal CMD de VS Code, ejecutar:

   ```cmd
   python ejecutar.py preparar_db.py
   python ejecutar.py demo_c1.py
   ```

   El programa pide la contraseña sin mostrarla ni guardarla en archivos. Si la BD usa otro usuario/puerto, configura `PGUSER`, `PGPORT` o `PGHOST` en tu sesión de CMD antes de ejecutar.

**Importante:** si ya creaste tablas manualmente con otros nombres o columnas, compara el esquema antes de ejecutar. `IF NOT EXISTS` no modifica estructuras incompatibles.

## Pruebas automatizadas (base separada)

Crear en pgAdmin una base `expressya_test` (con propietario `postgres` para pruebas locales). Después:

```cmd
set PGDATABASE=expressya_test
python ejecutar.py preparar_db.py
python ejecutar.py -m unittest discover -s tests -v
```

Los tests insertan datos propios en `expressya_test`; no ejecutar contra datos reales. **No afirmar que pasaron hasta ejecutarlos**. La prueba verifica que la diferencia entre `fecha_expiracion` y `fecha_creacion` sea exactamente 300 segundos. Para no esperar cinco minutos, adelanta la fecha de expiración únicamente en la BD de pruebas y ejecuta `vencer_reservas()`.



## Integración para compañeros que implementen C2

- `iniciar_checkout(id_cliente, id_dark_store, items, clave_idempotencia)` devuelve `idPedido`, `reservas` (una por línea), `tiempoExpiracion` y total. No supone una sola reserva para carritos de varios productos.
- Tablas relevantes: `pedido`, `linea_pedido`, `reserva`, `inventario`, `auditoria_movimiento`.
- **C2** debe confirmar pago y pedido en **una transacción**, bloquear filas de inventario/reservas, comprobar `estado_reserva='CREADA'` y `fecha_expiracion > clock_timestamp()`, pasar reservas a `CONFIRMADA`, descontar una sola vez de `cantidad_registrada` y `cantidad_reservada`, y registrar `VENTA` en auditoría. Un pago rechazado debe liberar reservas y no confirmar pedido.
- Un pago tardío **no revive** una reserva vencida; su compensación/reembolso debe diseñarse y probarse en C2.
- La clave de idempotencia se aplica al checkout. Este avance **propone** rechazar la misma clave con datos distintos; el SDD dejaba esa decisión pendiente.
- `vencer_reservas()` requiere ejecución periódica; aquí se ofrece la función, **no un planificador automático**. C2 debe coordinar vencimientos y pagos para evitar carreras.
- Los estados finales de `pedido` tras liberar/vencer quedan pendientes de integrar con C2.

## Decisiones técnicas a validar

El SDD enumera ocho entidades y atributos, pero deja tipos, índices y restricciones finales pendientes. Este esquema los **propone**. `huella_solicitud` es un campo técnico adicional para comparar reintentos. `ALTA_INICIAL`, `LIBERADA` y `VENCIDA` se usan en auditoría para demostrar cambios de saldo disponible; revisar nomenclatura con el equipo. No se atribuyen estas decisiones al cliente.

**Estado del avance:** Código entregado para revisión; ejecución de pruebas y compatibilidad con C2 pendientes de comprobar en el equipo del usuario.
