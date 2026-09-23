#!/usr/bin/env python3
"""
Paso 3: Clasificacion contra el ICP.

Para cada empresa evalua 4 ejes: industria, tamano, ubicacion, y liveness
(que ya viene resuelto del paso 2). Cada eje queda documentado con su propio
motivo, y el resultado final es uno de:

    QUALIFIED       -> pasa los 4 ejes limpio
    NEEDS_REVIEW     -> zona gris en industria o tamano (ver reglas abajo), la decide una persona
    DISQUALIFIED     -> no pasa un eje duro (pais fuera de ICP, sitio muerto/parkeado, etc.)

Nunca descarta en silencio: la columna icp_reason siempre explica por que.

Uso:
    python3 classify_icp.py --input output/step2_verified.csv --output output/step3_classified.csv --config config/icp_config.json
"""
import argparse
import csv
import json
import re


def normalize_country(raw_country: str, cfg: dict) -> str:
    if not raw_country:
        return ""
    raw = raw_country.strip().lower()
    for canonical, aliases in cfg["locations"]["country_normalization"].items():
        if raw in [a.lower() for a in aliases]:
            return canonical
    return raw  # deja el valor crudo si no lo reconoce; no lo inventamos


def check_location(row: dict, cfg: dict) -> dict:
    country_norm = normalize_country(row.get("Company Country", ""), cfg)
    allowed = cfg["locations"]["allowed_countries"]
    optional = cfg["locations"]["optional_countries"]

    if country_norm in allowed:
        return {"pass": True, "status": "IN_ICP", "country_normalized": country_norm,
                "reason": f"pais '{country_norm}' esta en la lista del ICP"}
    if country_norm in optional:
        return {"pass": True, "status": "OPTIONAL", "country_normalized": country_norm,
                "reason": f"pais '{country_norm}' es opcional segun el ICP (USA)"}
    return {"pass": False, "status": "OUT_OF_ICP", "country_normalized": country_norm,
            "reason": f"pais '{row.get('Company Country', '(vacio)')}' no esta en la lista del ICP"}


def parse_size_range(size_str: str):
    """'11-50' -> (11, 50). '10001+' -> (10001, None). Devuelve None si no se puede parsear."""
    if not size_str:
        return None
    size_str = size_str.strip()
    m = re.match(r"^(\d+)-(\d+)$", size_str)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.match(r"^(\d+)\+$", size_str)
    if m:
        return int(m.group(1)), None
    return None


def check_size(row: dict, cfg: dict) -> dict:
    field = cfg["company_size"]["employee_size_field"]
    raw = row.get(field, "")
    parsed = parse_size_range(raw)

    if parsed is None:
        return {"pass": None, "status": "UNKNOWN", "reason": f"'{field}'='{raw}' no se pudo interpretar como rango"}

    lo, hi = parsed
    icp_min = cfg["company_size"]["min_employees"]
    icp_max = cfg["company_size"]["max_employees"]

    # Si el rango de la empresa se solapa aunque sea parcialmente con el rango del ICP, pasa.
    range_hi = hi if hi is not None else float("inf")
    overlaps = lo <= icp_max and range_hi >= icp_min

    if overlaps:
        return {"pass": True, "status": "IN_RANGE", "reason": f"'{raw}' se solapa con el rango ICP {icp_min}-{icp_max}"}
    return {"pass": False, "status": "OUT_OF_RANGE", "reason": f"'{raw}' esta fuera del rango ICP {icp_min}-{icp_max}"}


def check_industry(row: dict, cfg: dict) -> dict:
    industry = (row.get("Industry", "") or "").strip().lower()
    tags = (row.get("Industry Tags", "") or "").lower()
    desc = (row.get("Description", "") or "").lower()
    product = (row.get("Product and Services", "") or "").lower()
    name = (row.get("Company Name", "") or "").lower()
    # El nombre de la empresa entra al chequeo de señales negativas (no al de positivas:
    # un nombre no prueba que la empresa sea SaaS, pero "Cerveceria X" si prueba que NO lo es,
    # incluso cuando el Industry/Description del CSV crudo viene mal tageado por la fuente).
    combined_context = f"{tags} {desc} {product} {name}"

    core = cfg["industry"]["core_industries"]
    gray = cfg["industry"]["gray_zone_industries"]
    positive_signals = cfg["industry"]["keyword_signals"]["positive_signals"]
    negative_signals = cfg["industry"]["keyword_signals"]["negative_signals"]

    matched_positive = [s for s in positive_signals if s in combined_context]
    matched_negative = [s for s in negative_signals if s in combined_context]

    if any(c in industry for c in core):
        # Aun en el core, si hay CUALQUIER señal negativa (nombre de empresa o texto),
        # lo mandamos a revision - incluso si tambien hay señales positivas en conflicto.
        # Caso real encontrado en este dataset: "Cerveceria Modelo" viene tageada por la
        # fuente como 'software development' y su Description dice literalmente "is a
        # computer software company" (enriquecimiento mal hecho de origen). Esa Description
        # tiene la palabra "software" (señal positiva) Y el nombre tiene "cerveceria" (señal
        # negativa) al mismo tiempo. Cuando las señales se contradicen, el dato de origen no
        # es confiable y la decision es de una persona, no del keyword matching.
        if matched_negative:
            return {
                "pass": None, "status": "NEEDS_REVIEW",
                "reason": (f"Industry='{row.get('Industry')}' matchea el ICP, pero encontre señales negativas "
                           f"({', '.join(matched_negative)}) que contradicen el dato - posible mal tageo de origen, revisar a mano"),
            }
        return {
            "pass": True, "status": "INDUSTRY_MATCH",
            "reason": f"Industry='{row.get('Industry')}' matchea directo con el ICP",
        }

    if any(g in industry for g in gray):
        if matched_positive:
            return {
                "pass": None, "status": "NEEDS_REVIEW",
                "reason": (f"Industry='{row.get('Industry')}' es zona gris, pero Industry Tags/Description "
                           f"sugieren que SI vive en el ICP ({', '.join(matched_positive)}) - candidato fuerte para aprobar a mano"),
            }
        return {
            "pass": None, "status": "NEEDS_REVIEW",
            "reason": f"Industry='{row.get('Industry')}' es zona gris y no encontre señales claras en Tags/Description - revisar a mano",
        }

    # Fuera del core y fuera de la lista gris: ultima chance via señales fuertes en el texto libre.
    if matched_positive:
        return {
            "pass": None, "status": "NEEDS_REVIEW",
            "reason": (f"Industry='{row.get('Industry')}' no esta en la lista del ICP, pero Industry Tags/Description "
                       f"tienen señales de SaaS/fintech/software ({', '.join(matched_positive)}) - revisar a mano"),
        }

    return {
        "pass": False, "status": "OUT_OF_ICP",
        "reason": f"Industry='{row.get('Industry')}' no matchea el ICP y no hay señales de SaaS/fintech/software en el texto",
    }



# Statuses de liveness de ALTA confianza: si el sitio cae en uno de estos, se
# descalifica duro. Son señales verificadas en este dataset como confiables
# (conexion rechazada de verdad, o una frase explicita de "domain for sale").
LIVENESS_HARD_FAIL = {"DEAD", "PARKED_CONFIRMED", "NOT_CHECKED", "SKIPPED"}

# Statuses de liveness BLANDOS: hay evidencia de que algo no cierra del todo, pero
# verificado a mano en este dataset que varios de estos son falsos positivos
# (migraciones de dominio legitimas, WAF bloqueando el request, boilerplate de
# terceros con una palabra suelta). Nunca descalifican solos: fuerzan NEEDS_REVIEW.
LIVENESS_SOFT_FLAG = {"BLOCKED_BOT_PROTECTION", "PARKED_LIKELY", "SUSPICIOUS_RECYCLED", "REDIRECTED_OFFSITE"}


def classify_row(row: dict, cfg: dict) -> dict:
    reasons = []

    # --- Eje 0: liveness (ya resuelto en el paso anterior) ---
    liveness_status = row.get("liveness_status", "NOT_CHECKED")
    liveness_ok = liveness_status == "ALIVE"
    liveness_hard_fail = liveness_status in LIVENESS_HARD_FAIL
    liveness_soft_flag = liveness_status in LIVENESS_SOFT_FLAG
    if not liveness_ok:
        reasons.append(f"liveness={liveness_status} ({row.get('liveness_reason', '')})")

    # --- Eje 1: ubicacion ---
    loc = check_location(row, cfg)
    reasons.append(f"ubicacion: {loc['reason']}")

    # --- Eje 2: tamano ---
    size = check_size(row, cfg)
    reasons.append(f"tamano: {size['reason']}")

    # --- Eje 3: industria ---
    industry = check_industry(row, cfg)
    reasons.append(f"industria: {industry['reason']}")

    # --- Veredicto final ---
    hard_fail = (
        liveness_hard_fail
        or loc["pass"] is False
        or size["pass"] is False
        or industry["pass"] is False
    )
    needs_review = (
        liveness_soft_flag
        or (size["pass"] is None)
        or (industry["status"] == "NEEDS_REVIEW")
    )

    if hard_fail:
        final = "DISQUALIFIED"
    elif needs_review:
        final = "NEEDS_REVIEW"
    else:
        final = "QUALIFIED"

    return {
        "icp_final_status": final,
        "icp_location_status": loc["status"],
        "icp_country_normalized": loc["country_normalized"],
        "icp_size_status": size["status"],
        "icp_industry_status": industry["status"],
        "icp_reason": " | ".join(reasons),
    }


def main():
    ap = argparse.ArgumentParser(description="Clasifica cada empresa contra el ICP.")
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--config", required=True)
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)

    with open(args.input, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames

    new_fields = [
        "icp_final_status", "icp_location_status", "icp_country_normalized",
        "icp_size_status", "icp_industry_status", "icp_reason",
    ]
    out_fields = list(fieldnames) + new_fields

    counts = {}
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields)
        writer.writeheader()
        for row in rows:
            verdict = classify_row(row, cfg)
            row.update(verdict)
            counts[verdict["icp_final_status"]] = counts.get(verdict["icp_final_status"], 0) + 1
            writer.writerow(row)

    print("[classify_icp] Resumen:")
    for status, count in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {status}: {count}")
    print(f"[classify_icp] Escribi: {args.output}")


if __name__ == "__main__":
    main()
