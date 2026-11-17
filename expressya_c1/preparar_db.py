"""Crea tablas a partir de schema.sql en la base indicada por PGDATABASE."""
from pathlib import Path
from c1 import conectar

with conectar() as conexion:
    with conexion.cursor() as cursor:
        cursor.execute((Path(__file__).parent / 'schema.sql').read_text(encoding='utf-8'))
print('Esquema instalado correctamente.')
