#!/usr/bin/env python3
"""
Orquestador. Corre los 4 pasos en orden:

  output/audit_all_companies.csv   -> TODAS las filas, con el motivo de cada una (nunca se borra nada en silencio)
  output/needs_review.csv          -> Las NEEDS_REVIEW, para que una persona decida (zona gris de industria/tamano)
  output/qualified_companies.csv   -> Las QUALIFIED, con TODAS las columnas (para auditoria/debug interno)
  output/clay_export.csv           -> EL ENTREGABLE: un solo CSV, solo las columnas que Clay necesita,
                                       listo para mandar por webhook (a mano ahora, por n8n/API despues)

Uso (desde la raiz del proyecto - --input y --config ya tienen default):
    python3 scripts/run_pipeline.py

    # Para probar rapido con pocas filas antes de correr las 642:
    python3 scripts/run_pipeline.py --limit 30

    # Con otro CSV o config (ej. un cliente nuevo):
    python3 scripts/run_pipeline.py --input otro.csv --config config/otro_cliente.json
"""
import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path


def run_step(cmd: list):
    print(f"\n{'='*70}\n$ {' '.join(cmd)}\n{'='*70}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        sys.exit(f"[run_pipeline] El paso fallo (exit {result.returncode}): {' '.join(cmd)}")


def split_final_outputs(classified_csv: str, cfg: dict):
    with open(classified_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames

    qualified = [r for r in rows if r["icp_final_status"] == "QUALIFIED"]
    needs_review = [r for r in rows if r["icp_final_status"] == "NEEDS_REVIEW"]
    disqualified = [r for r in rows if r["icp_final_status"] == "DISQUALIFIED"]

    audit_path = cfg["output"]["audit_csv"]
    qualified_path = cfg["output"]["qualified_csv"]
    review_path = cfg["output"]["needs_review_csv"]

    for path in (audit_path, qualified_path, review_path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    with open(audit_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)

    with open(qualified_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(qualified)

    with open(review_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(needs_review)

    return {
        "total": len(rows),
        "qualified": len(qualified),
        "needs_review": len(needs_review),
        "disqualified": len(disqualified),
        "audit_path": audit_path,
        "qualified_path": qualified_path,
        "review_path": review_path,
    }


def main():
    ap = argparse.ArgumentParser(description="Corre el pipeline completo de calificacion ICP.")
    ap.add_argument("--input", default="challenge_2026_raw.csv", help="CSV crudo de entrada (default: challenge_2026_raw.csv)")
    ap.add_argument("--config", default="config/icp_config.json", help="Config JSON del ICP (default: config/icp_config.json)")
    ap.add_argument("--website-col", default="Website")
    ap.add_argument("--limit", type=int, default=None, help="Solo procesar las primeras N filas (para pruebas rapidas)")
    ap.add_argument("--skip-liveness", action="store_true",
                     help="Salta el chequeo de sitios vivos (solo para debug de la clasificacion, "
                          "NUNCA para el entregable final: el challenge pide verificar liveness de verdad)")
    args = ap.parse_args()

    script_dir = Path(__file__).parent
    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)

    Path("output").mkdir(exist_ok=True)

    t0 = time.time()

    # Paso 1: normalizar
    step1_out = "output/step1_normalized.csv"
    cmd1 = [sys.executable, str(script_dir / "normalize_domains.py"),
            "--input", args.input, "--output", step1_out, "--website-col", args.website_col]
    run_step(cmd1)

    input_for_step2 = step1_out
    if args.limit:
        # Recorta el CSV a --limit filas antes del paso caro (red).
        with open(step1_out, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)[:args.limit]
            fieldnames = reader.fieldnames
        input_for_step2 = "output/step1_normalized_limited.csv"
        with open(input_for_step2, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)

    # Paso 2: verificar liveness
    step2_out = "output/step2_verified.csv"
    if args.skip_liveness:
        print("\n[run_pipeline] --skip-liveness activo: copiando sin chequear (SOLO DEBUG).")
        with open(input_for_step2, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            fieldnames = reader.fieldnames
        new_fields = fieldnames + ["domain_checked", "http_status", "final_url", "final_domain",
                                    "liveness_status", "liveness_reason", "content_length"]
        with open(step2_out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=new_fields)
            w.writeheader()
            for row in rows:
                row.update({"domain_checked": "", "http_status": "", "final_url": "", "final_domain": "",
                            "liveness_status": "SKIPPED", "liveness_reason": "--skip-liveness", "content_length": ""})
                w.writerow(row)
    else:
        cmd2 = [sys.executable, str(script_dir / "verify_alive.py"),
                "--input", input_for_step2, "--output", step2_out, "--config", args.config]
        run_step(cmd2)

    # Paso 3: clasificar contra ICP
    step3_out = "output/step3_classified.csv"
    cmd3 = [sys.executable, str(script_dir / "classify_icp.py"),
            "--input", step2_out, "--output", step3_out, "--config", args.config]
    run_step(cmd3)

    # Separar en los 3 CSV internos (audit / qualified / needs_review)
    summary = split_final_outputs(step3_out, cfg)

    # Paso 4: armar el UNICO CSV de export para Clay (solo columnas mapeadas, solo QUALIFIED)
    clay_export_path = cfg["output"]["clay_export_csv"]
    cmd4 = [sys.executable, str(script_dir / "export_for_clay.py"),
            "--input", step3_out, "--output", clay_export_path, "--config", args.config]
    run_step(cmd4)

    elapsed = time.time() - t0

    print(f"\n{'='*70}")
    print("[run_pipeline] RESUMEN FINAL")
    print(f"{'='*70}")
    print(f"Total de filas procesadas:  {summary['total']}")
    print(f"  QUALIFIED (van a Clay):    {summary['qualified']}")
    print(f"  NEEDS_REVIEW (revisar):    {summary['needs_review']}")
    print(f"  DISQUALIFIED (descartadas):{summary['disqualified']}")
    print(f"Tiempo total: {elapsed:.1f}s")
    print()
    print(f"Auditoria completa (todas las filas + motivo): {summary['audit_path']}")
    print(f"Para revisar a mano (zona gris): {summary['review_path']}")
    print(f"Calificadas, todas las columnas (debug interno): {summary['qualified_path']}")
    print(f">>> ENTREGABLE PARA CLAY (un solo CSV, listo para el webhook): {clay_export_path}")
    print()
    print("Para mandarlo al webhook de Clay (Fase 2):")
    print("  python3 scripts/send_to_clay.py --dry-run")
    print("  (sacá --dry-run cuando tengas CLAY_WEBHOOK_URL en tu .env - ver .env.example)")


if __name__ == "__main__":
    main()
