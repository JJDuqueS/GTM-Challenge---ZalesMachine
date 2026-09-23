#!/usr/bin/env python3
"""
Paso 1: Normalizacion de dominios.

Saca protocolo, www, paths y parametros de tracking. Devuelve un dominio
raiz limpio y comparable (ej. https://www.t1.com?utm_source=li -> t1.com).

Uso:
    python3 normalize_domains.py --input input_raw.csv --output output/step1_normalized.csv
"""
import argparse
import csv
import re
import sys
from urllib.parse import urlparse

# ccSLDs (second-level ccTLDs) mas comunes en LATAM + genericos.
# Sin esto, "empresa.com.mx" se corta mal en "com.mx" en vez de "empresa.com.mx".
KNOWN_SECOND_LEVEL_SUFFIXES = {
    "com.mx", "com.ar", "com.co", "com.pe", "com.uy", "com.cl", "com.br",
    "gob.mx", "org.mx", "net.mx", "edu.mx",
    "com.do", "com.pa", "co.uk", "com.au", "co.nz",
}


def strip_url_junk(raw: str) -> str:
    """Saca protocolo, www, paths, query params y fragments."""
    if not raw or not raw.strip():
        return ""

    raw = raw.strip().lower()

    # Si no tiene protocolo, se lo agregamos para que urlparse lo lea bien.
    if not re.match(r"^https?://", raw):
        raw = "http://" + raw

    parsed = urlparse(raw)
    host = parsed.netloc or parsed.path.split("/")[0]

    # Sacar puerto si vino (ej. empresa.com:8080)
    host = host.split(":")[0]

    # Sacar www. (y variantes como www2.)
    host = re.sub(r"^www\d*\.", "", host)

    return host.strip(".")


def get_root_domain(host: str) -> str:
    """
    Reduce un host a su dominio raiz comparable.
    subdominio.empresa.com -> empresa.com
    empresa.com.mx -> empresa.com.mx (respeta ccSLD)
    """
    if not host:
        return ""

    parts = host.split(".")
    if len(parts) <= 2:
        return host

    last_two = ".".join(parts[-2:])
    last_three = ".".join(parts[-3:])

    if last_two in KNOWN_SECOND_LEVEL_SUFFIXES and len(parts) >= 3:
        return last_three
    return last_two


def is_non_company_domain(host: str) -> bool:
    """Detecta cuando el 'Website' en realidad apunta a una red social o form, no al sitio propio."""
    if not host:
        return False
    social_hosts = [
        "facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com",
        "forms.gle", "docs.google.com", "wa.me", "whatsapp.com", "tiktok.com",
        "youtube.com",
    ]
    root = get_root_domain(host)
    return root in social_hosts


def normalize_row(website_raw: str) -> dict:
    host = strip_url_junk(website_raw)
    root = get_root_domain(host)
    flag_social = is_non_company_domain(host)

    return {
        "domain_raw": website_raw.strip() if website_raw else "",
        "domain_normalized": root,
        "domain_has_value": bool(root) and not flag_social,
        "flag_social_or_form_link": flag_social,
    }


def main():
    ap = argparse.ArgumentParser(description="Normaliza dominios de un CSV crudo de empresas.")
    ap.add_argument("--input", required=True, help="CSV crudo de entrada")
    ap.add_argument("--output", required=True, help="CSV de salida con dominios normalizados")
    ap.add_argument("--website-col", default="Website", help="Nombre de la columna con el sitio web")
    args = ap.parse_args()

    with open(args.input, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames

    if args.website_col not in fieldnames:
        sys.exit(f"ERROR: no encontre la columna '{args.website_col}'. Columnas disponibles: {fieldnames}")

    # row_id estable, asignado UNA sola vez, acá, al principio de todo el pipeline.
    # Viaja sin cambiar por normalize -> verify -> classify -> export -> webhook, y es
    # lo que permite reconciliar "cuantas filas salieron de Claude Code" contra
    # "cuantas filas llegaron a Clay" en la Fase 3 (verificacion) sin ambiguedad -
    # nombres de empresa se repiten o tienen variantes, un id secuencial no.
    out_fields = ["row_id"] + list(fieldnames) + [
        "domain_raw", "domain_normalized", "domain_has_value", "flag_social_or_form_link"
    ]

    stats = {"total": 0, "no_website": 0, "social_or_form": 0, "normalized_ok": 0}

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields)
        writer.writeheader()
        for i, row in enumerate(rows, start=1):
            stats["total"] += 1
            row["row_id"] = f"icp-{i:05d}"
            norm = normalize_row(row.get(args.website_col, ""))
            row.update(norm)

            if not norm["domain_normalized"]:
                stats["no_website"] += 1
            elif norm["flag_social_or_form_link"]:
                stats["social_or_form"] += 1
            else:
                stats["normalized_ok"] += 1

            writer.writerow(row)

    print(f"[normalize_domains] Total filas: {stats['total']}")
    print(f"[normalize_domains] Sin website utilizable: {stats['no_website']}")
    print(f"[normalize_domains] Apuntan a red social/form (no sitio propio): {stats['social_or_form']}")
    print(f"[normalize_domains] Normalizados OK: {stats['normalized_ok']}")
    print(f"[normalize_domains] Escribi: {args.output}")


if __name__ == "__main__":
    main()
