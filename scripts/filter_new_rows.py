#!/usr/bin/env python3
"""
Se queda solo con las filas de clay_export.csv cuyo row_id es NUEVO (viene
del CSV scrapeado, no del CSV base que ya mandaste a Clay). Corre esto DESPUES
de run_pipeline.py sobre el combinado, y ANTES de send_to_clay.py - asi no
re-mandas las mismas 291 filas dos veces.

--min-row-id es la cantidad de filas que tenia tu CSV base ANTES de fusionar
(merge_sources.py te la imprime como "Filas del CSV base: N" - usa N+1 aca).

Uso:
    python3 scripts/filter_new_rows.py --input output/clay_export.csv \\
        --min-row-id 643 --output output/clay_export_new_only.csv

    python3 scripts/send_to_clay.py --input output/clay_export_new_only.csv --dry-run
"""
import argparse
import csv
import re


def row_id_num(row_id: str):
    m = re.match(r"^icp-(\d+)$", row_id or "")
    return int(m.group(1)) if m else None


def main():
    ap = argparse.ArgumentParser(description="Filtra clay_export.csv a solo las filas nuevas (row_id >= --min-row-id).")
    ap.add_argument("--input", default="output/clay_export.csv")
    ap.add_argument("--output", default="output/clay_export_new_only.csv")
    ap.add_argument("--min-row-id", type=int, required=True,
                     help="Primer row_id numerico que cuenta como 'nuevo' (= filas del CSV base + 1)")
    args = ap.parse_args()

    with open(args.input, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames

    new_rows = [r for r in rows if (row_id_num(r.get("row_id")) or 0) >= args.min_row_id]

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(new_rows)

    print(f"[filter_new_rows] Filas totales en {args.input}: {len(rows)}")
    print(f"[filter_new_rows] Filas nuevas (row_id >= icp-{args.min_row_id:05d}): {len(new_rows)}")
    print(f"[filter_new_rows] Escribi: {args.output}")
    print(f"[filter_new_rows] Ahora: python3 scripts/send_to_clay.py --input {args.output} --dry-run")


if __name__ == "__main__":
    main()
