"""Ejecuta scripts de ExpressYa solicitando contraseña sin mostrarla."""
import getpass
import os
import subprocess
import sys

if len(sys.argv) < 2:
    print('Uso: python ejecutar.py preparar_db.py | demo_c1.py | -m unittest discover -s tests -v')
    raise SystemExit(2)
ambiente = os.environ.copy()
ambiente.setdefault('PGHOST', 'localhost')
ambiente.setdefault('PGPORT', '5432')
ambiente.setdefault('PGUSER', 'postgres')
ambiente.setdefault('PGDATABASE', 'expressya_db')
if 'PGPASSWORD' not in ambiente:
    ambiente['PGPASSWORD'] = getpass.getpass('Contraseña de PostgreSQL (no se muestra): ')
raise SystemExit(subprocess.call([sys.executable, *sys.argv[1:]], env=ambiente))
