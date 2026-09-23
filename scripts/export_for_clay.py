#!/usr/bin/env python3
"""
Paso 4: Export final - UN SOLO CSV, listo para Clay (o para que un proceso
automatico lo levante y lo mande por webhook).

Toma el output clasificado del paso 3, se queda SOLO con las filas QUALIFIED
(las NEEDS_REVIEW y DISQUALIFIED no salen de acá - "filtrar antes de
enriquecer" significa que ni siquiera llegan a este archivo hasta que una
persona las mueva a QUALIFIED a mano), y mapea las columnas al esquema que
define config/icp_config.json (clay_export.fields) - no las 40+ columnas
crudas del CSV original, solo lo que Clay/Fase 2 necesita.

Uso:
    python3 export_for_clay.py --input output/step3_classified.csv --output output/clay_export.csv --config config/icp_config.json
"""
import argparse
import csv
import json
import sys


def main():
    ap = argparse.ArgumentParser(description="Arma el CSV final de export para Clay.")
    ap.add_argument("--input", required=True, help="output/step3_classified.csv (o audit_all_companies.csv)")
    ap.add_argument("--output", required=True)
    ap.add_argument("--config", required=True)
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)

    with open(args.input, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        available_cols = set(reader.fieldnames or [])

    field_map = cfg["clay_export"]["fields"]

    missing = [fm["source"] for fm in field_map if fm["source"] not in available_cols]
    if missing:
        sys.exit(f"ERROR: estas columnas de clay_export.fields no existen en {args.input}: {missing}")

    qualified = [row for row in rows if row.get("icp_final_status") == "QUALIFIED"]

    target_fields = [fm["target"] for fm in field_map]

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=target_fields)
        writer.writeheader()
        for row in qualified:
            out_row = {fm["target"]: row.get(fm["source"], "") for fm in field_map}
            writer.writerow(out_row)

    # Chequeo de filas incompletas: ninguna fila que califica deberia faltarle un
    # campo que el paso siguiente (Clay / people-finding) necesita para funcionar.
    required_for_next_step = ["domain", "company_name"]
    incomplete = []
    for row in qualified:
        out_row = {fm["target"]: row.get(fm["source"], "") for fm in field_map}
        empty = [f for f in required_for_next_step if not out_row.get(f, "").strip()]
        if empty:
            incomplete.append((out_row.get("row_id", "?"), empty))

    print(f"[export_for_clay] Filas QUALIFIED exportadas: {len(qualified)}")
    print(f"[export_for_clay] Columnas: {target_fields}")
    if incomplete:
        print(f"[export_for_clay] ADVERTENCIA: {len(incomplete)} filas QUALIFIED con campos criticos vacios:")
        for row_id, empty in incomplete[:10]:
            print(f"  {row_id}: faltan {empty}")
    else:
        print("[export_for_clay] Ninguna fila QUALIFIED tiene campos criticos vacios.")
    print(f"[export_for_clay] Escribi: {args.output}")


if __name__ == "__main__":
    main()
