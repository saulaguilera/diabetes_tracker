"""
Migración SQLite → Postgres (FASE MOTOR: copia fiel, misma semántica de
timestamps — el pase a UTC es una fase posterior sobre base estable).

Corre EN el contenedor web de Railway (tiene /data/diabetes.db y red interna
al Postgres):

    python scripts/migrar_a_postgres.py --destino "$MIGRATE_TO"            # migra
    python scripts/migrar_a_postgres.py --destino "$MIGRATE_TO" --verificar  # solo compara

Diseño:
- El esquema lo crea models.py (create_all) — idéntico al de la app.
- Copia por lotes en orden de FKs (metadata.sorted_tables), con los TIPOS de
  las columnas del modelo (bool 0/1 → boolean, datetimes naive tal cual).
- setval de cada secuencia al máximo id (o el primer INSERT explotaría).
- Verificación = compuerta: conteos por tabla + muestreo de filas exactas.
- La SQLite NO se toca jamás: es la foto de rollback.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import create_engine, text, select, func

LOTE = 2000


def _engines(destino):
    data_dir = os.environ.get("DATA_DIR", "/data")
    src_path = os.path.join(data_dir, "diabetes.db")
    if not os.path.exists(src_path):
        sys.exit(f"No existe la SQLite fuente: {src_path}")
    if destino.startswith("postgres://"):
        destino = destino.replace("postgres://", "postgresql://", 1)
    if not destino.startswith("postgresql"):
        sys.exit("El destino debe ser una URL postgresql://")
    return create_engine(f"sqlite:///{src_path}"), create_engine(destino)


def _tablas():
    from models import db
    return db.metadata


def migrar(destino):
    src, dst = _engines(destino)
    meta = _tablas()

    # esquema fresco e idéntico al de models.py
    meta.create_all(dst)

    resumen = []
    with src.connect() as cs, dst.connect() as cd:
        for t in meta.sorted_tables:
            # destino DEBE estar vacío (no migrar encima de datos)
            ya = cd.execute(select(func.count()).select_from(t)).scalar()
            if ya:
                print(f"  {t.name}: destino ya tiene {ya} filas — SALTANDO (no piso datos)")
                continue
            filas = cs.execute(t.select()).mappings().all()
            n = len(filas)
            for i in range(0, n, LOTE):
                lote = [dict(r) for r in filas[i:i + LOTE]]
                if lote:
                    cd.execute(t.insert(), lote)
            cd.commit()
            resumen.append((t.name, n))
            print(f"  {t.name}: {n} filas")

        # secuencias de PK (Postgres): al máximo id copiado
        for t in meta.sorted_tables:
            pk = [c for c in t.primary_key.columns]
            if len(pk) == 1 and pk[0].autoincrement and str(pk[0].type).startswith("INTEGER"):
                col = pk[0].name
                cd.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{t.name}', '{col}'), "
                    f"COALESCE((SELECT MAX({col}) FROM {t.name}), 0) + 1, false)"))
        cd.commit()
    print(f"\nMigradas {len(resumen)} tablas, {sum(n for _, n in resumen)} filas totales.")


def verificar(destino):
    src, dst = _engines(destino)
    meta = _tablas()
    problemas = 0
    with src.connect() as cs, dst.connect() as cd:
        for t in meta.sorted_tables:
            a = cs.execute(select(func.count()).select_from(t)).scalar()
            b = cd.execute(select(func.count()).select_from(t)).scalar()
            marca = "✓" if a == b else "✗✗✗"
            if a != b:
                problemas += 1
            print(f"  {marca} {t.name}: sqlite={a} postgres={b}")

            # muestreo: primeras y últimas filas por PK, campo a campo
            pk = list(t.primary_key.columns)
            if a and a == b and len(pk) == 1:
                col = pk[0]
                for orden in (col.asc(), col.desc()):
                    fa = cs.execute(t.select().order_by(orden).limit(3)).mappings().all()
                    fb = cd.execute(t.select().order_by(orden).limit(3)).mappings().all()
                    for ra, rb in zip(fa, fb):
                        for k in ra.keys():
                            va, vb = ra[k], rb[k]
                            # SQLite bool llega como 0/1; Postgres como bool
                            if isinstance(vb, bool):
                                va = bool(va)
                            if va != vb:
                                problemas += 1
                                print(f"      ✗ {t.name}.{k} pk={ra[col.name]}: "
                                      f"{va!r} != {vb!r}")
    if problemas:
        sys.exit(f"\n✗ VERIFICACIÓN FALLÓ: {problemas} problemas — NO hacer el switch.")
    print("\n✓ Verificación completa: conteos y muestras idénticos. Listo para el switch.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--destino", required=True, help="URL postgresql:// de destino")
    ap.add_argument("--verificar", action="store_true", help="solo comparar, no migrar")
    args = ap.parse_args()
    if args.verificar:
        verificar(args.destino)
    else:
        migrar(args.destino)
        print("\nAhora corre con --verificar antes del switch.")
