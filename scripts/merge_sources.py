#!/usr/bin/env python3
"""
Combina el CSV base del challenge con el CSV scrapeado de fintechmexico.org,
sacando duplicados (mismo dominio raiz ya presente en el CSV base) ANTES de
correr el pipeline - asi no le hacemos verificar/clasificar dos veces la
misma empresa, ni (mas importante) terminamos re-mandando a Clay compañias
que Juan ya mando en la corrida anterior.

Las filas del CSV base van PRIMERO, en el mismo orden, sin tocar - eso
preserva sus row_id (icp-00001 .. icp-00642) exactamente como ya estan en
Clay. Las filas scrapeadas nuevas van despues, asi que normalize_domains.py
les va a asignar row_id a partir de icp-00643 en adelante: nunca chocan con
las que ya mandaste.

Uso:
    python3 scripts/merge_sources.py \\
        --base challenge_2026_raw.csv \\
        --scraped output/fintechmexico_scraped.csv \\
        --output output/combined_input.csv

Despues corres el pipeline normal sobre el combinado:
    python3 scripts/run_pipeline.py --input output/combined_input.csv

Y ANTES de mandar a Clay, te quedas solo con las filas nuevas (ver
scripts/filter_new_rows.py) para no re-mandar las 291 que ya mandaste.
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from normalize_domains import strip_url_junk, get_root_domain  # reusa la misma logica de dominio


def domains_in(csv_path: str, website_col: str) -> set:
    domains = set()
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            root = get_root_domain(strip_url_junk(row.get(website_col, "")))
            if root:
                domains.add(root)
    return domains


def main():
    ap = argparse.ArgumentParser(description="Combina el CSV base + el scrapeado, sacando duplicados por dominio.")
    ap.add_argument("--base", default="challenge_2026_raw.csv")
    ap.add_argument("--scraped", default="output/fintechmexico_scraped.csv")
    ap.add_argument("--output", default="output/combined_input.csv")
    ap.add_argument("--base-website-col", default="Website")
    ap.add_argument("--scraped-website-col", default="Website")
    args = ap.parse_args()

    with open(args.base, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        base_rows = list(reader)
        base_fields = reader.fieldnames

    with open(args.scraped, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        scraped_rows = list(reader)
        scraped_fields = reader.fieldnames

    base_domains = domains_in(args.base, args.base_website_col)

    # Union de columnas: todo lo que trae el base + lo que trae el scrapeado que
    # el base no tenga (ej. "Source"). Las filas del base quedan con "Source" vacio.
    out_fields = list(base_fields) + [c for c in scraped_fields if c not in base_fields]

    kept, skipped_dupe, skipped_no_domain = [], [], []
    for row in scraped_rows:
        root = get_root_domain(strip_url_junk(row.get(args.scraped_website_col, "")))
        if not root:
            skipped_no_domain.append(row)
            kept.append(row)  # no se descarta en silencio - el pipeline ya sabe marcar "sin website"
            continue
        if root in base_domains:
            skipped_dupe.append((row.get("Company Name", "?"), root))
            continue
        kept.append(row)

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields)
        writer.writeheader()
        for row in base_rows:
            writer.writerow(row)
        for row in kept:
            writer.writerow(row)

    print(f"[merge_sources] Filas del CSV base: {len(base_rows)} (van primero, row_id icp-00001..icp-{len(base_rows):05d})")
    print(f"[merge_sources] Filas scrapeadas: {len(scraped_rows)}")
    print(f"[merge_sources]   Duplicadas (mismo dominio ya en el base, se excluyen): {len(skipped_dupe)}")
    for name, dom in skipped_dupe[:15]:
        print(f"    - {name} ({dom}) ya estaba en el CSV base")
    if len(skipped_dupe) > 15:
        print(f"    ... y {len(skipped_dupe) - 15} mas")
    print(f"[merge_sources]   Sin dominio parseable (quedan igual, el pipeline las va a marcar): {len(skipped_no_domain)}")
    print(f"[merge_sources]   Nuevas, se agregan: {len(kept) - len(skipped_no_domain)}")
    print(f"[merge_sources] Total filas en {args.output}: {len(base_rows) + len(kept)}")
    print(f"[merge_sources] Las filas nuevas van a quedar con row_id >= icp-{len(base_rows) + 1:05d} "
          f"cuando corras run_pipeline.py sobre este archivo - usa eso despues con filter_new_rows.py "
          f"para mandar a Clay SOLO lo nuevo.")


if __name__ == "__main__":
    main()
