import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), 'desmotadora.db')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def migrate_db():
    conn = get_db()
    for sql in (
        'ALTER TABLE prestadores ADD COLUMN formulario_931_path TEXT',
        'ALTER TABLE prestadores ADD COLUMN nomina_path TEXT',
    ):
        try:
            conn.execute(sql)
            conn.commit()
        except Exception:
            pass
    conn.close()


def init_db():
    conn = get_db()
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS visitas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            apellido TEXT NOT NULL,
            dni TEXT UNIQUE NOT NULL,
            es_conductor INTEGER NOT NULL DEFAULT 0,
            patente TEXT,
            seguro_vehiculo_path TEXT,
            carnet_conducir_path TEXT,
            carnet_vencimiento TEXT,
            seguro_vencimiento TEXT,
            fecha_registro TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS prestadores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            razon_social TEXT NOT NULL,
            cuit TEXT UNIQUE NOT NULL,
            categoria_tributaria TEXT NOT NULL,
            gmail TEXT,
            resp_nombre TEXT,
            resp_dni TEXT,
            resp_maneja INTEGER NOT NULL DEFAULT 0,
            resp_carnet_path TEXT,
            resp_carnet_vencimiento TEXT,
            formulario_931_path TEXT,
            fecha_registro TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS empleados (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prestador_id INTEGER NOT NULL,
            nombre TEXT NOT NULL,
            dni TEXT NOT NULL,
            formulario_931_path TEXT,
            carnet_conducir_path TEXT,
            carnet_vencimiento TEXT,
            FOREIGN KEY (prestador_id) REFERENCES prestadores(id)
        );

        CREATE TABLE IF NOT EXISTS vehiculos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prestador_id INTEGER NOT NULL,
            patente TEXT NOT NULL,
            seguro_path TEXT,
            seguro_vencimiento TEXT,
            FOREIGN KEY (prestador_id) REFERENCES prestadores(id)
        );

        CREATE TABLE IF NOT EXISTS ingresos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dni TEXT NOT NULL,
            nombre TEXT NOT NULL,
            tipo TEXT NOT NULL,
            empresa TEXT,
            fecha_ingreso TEXT NOT NULL
        );
    ''')
    conn.commit()
    conn.close()
