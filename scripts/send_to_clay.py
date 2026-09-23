#!/usr/bin/env python3
"""
Paso 5 (Fase 2 del challenge): manda el CSV de Clay, fila por fila, al webhook
de la tabla de Clay ("Pull in data from a Webhook").

Pensado para correr de 3 formas distintas sin cambiar una linea:
  1. A mano, una vez, desde la terminal (lo que vas a hacer ahora).
  2. Como un paso mas de un cron/scheduled task que corre todo el pipeline.
  3. Como el comando que un nodo "Execute Command" de n8n dispara, o como
     referencia directa de la logica de POST para armar el mismo flujo con
     un nodo "HTTP Request" + "Split in Batches" nativo de n8n.

Sin URL de webhook (ni en env, ni en --webhook-url) corre en --dry-run
SIEMPRE: nunca manda nada a ningun lado por accidente. Esto es a proposito -
conectar esto a un webhook real es una decision que toma una persona, no un
default.

LA URL DEL WEBHOOK ES UN SECRETO, NO CONFIGURACION: cualquiera que la tenga
puede mandarte filas falsas a tu tabla de Clay o gastarte creditos. Por eso
NO vive en config/icp_config.json (ese archivo se versiona en git) - vive en
la variable de entorno CLAY_WEBHOOK_URL, leida desde un archivo .env que
NUNCA se commitea (.gitignore ya lo excluye). Ver .env.example.

Orden de prioridad para la URL: --webhook-url (CLI) > variable de entorno
CLAY_WEBHOOK_URL > nada (dry-run).

Escribe output/clay_send_log.csv con el resultado de cada fila (row_id,
http_status, ok/error) - es la base de la verificacion de Fase 3: contás
cuantas filas mandaste con éxito acá, y lo comparás contra cuantas aparecen
en la tabla de Clay.

Uso (desde la raiz del proyecto - --input y --config ya tienen default):
    # 1. Copiá .env.example a .env y pegá tu URL real ahi adentro.
    # 2. Prueba en seco (no manda nada, solo simula):
    python3 scripts/send_to_clay.py --dry-run

    # 3. Envio real (toma la URL de la variable de entorno CLAY_WEBHOOK_URL,
    #    cargada automaticamente desde .env):
    python3 scripts/send_to_clay.py

    # O pasando la URL directo por linea de comando, sin .env:
    python3 scripts/send_to_clay.py --webhook-url https://api.clay.com/webhook/xxxxx
"""
import argparse
import csv
import json
import os
import sys
import time

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()  # lee .env si existe, no rompe nada si no existe
except ImportError:
    pass  # python-dotenv es opcional: si no esta instalado, igual funciona con --webhook-url o un env var seteado a mano


def send_row(webhook_url: str, row: dict, timeout: int = 15) -> dict:
    try:
        resp = requests.post(webhook_url, json=row, timeout=timeout)
        return {
            "row_id": row.get("row_id", ""),
            "http_status": resp.status_code,
            "ok": 200 <= resp.status_code < 300,
            "error": "" if resp.ok else resp.text[:200],
        }
    except requests.exceptions.RequestException as e:
        return {"row_id": row.get("row_id", ""), "http_status": None, "ok": False, "error": str(e)[:200]}


def main():
    ap = argparse.ArgumentParser(description="Manda el CSV de Clay al webhook, fila por fila.")
    ap.add_argument("--input", default="output/clay_export.csv", help="default: output/clay_export.csv")
    ap.add_argument("--config", default="config/icp_config.json", help="default: config/icp_config.json")
    ap.add_argument("--webhook-url", default=None, help="Override de la URL del webhook (si no, usa la del config)")
    ap.add_argument("--dry-run", action="store_true", help="No manda nada, solo simula y muestra que mandaria")
    ap.add_argument("--delay-ms", type=int, default=150, help="Pausa entre requests (ms) para no saturar el webhook")
    ap.add_argument("--log-output", default=None, help="Donde escribir el log de envio (default: config.output.send_log_csv)")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)

    # Prioridad: CLI > variable de entorno (.env) > nada. OJO: ya no se lee del
    # config.json a proposito - ese archivo se versiona en git, un secreto no va ahi.
    webhook_url = args.webhook_url or os.environ.get("CLAY_WEBHOOK_URL", "")
    dry_run = args.dry_run or not webhook_url

    if dry_run and not args.dry_run:
        print("[send_to_clay] No hay webhook URL (ni en --webhook-url ni en la variable de entorno CLAY_WEBHOOK_URL).")
        print("[send_to_clay] Corriendo en --dry-run automaticamente. Nada se manda a ningun lado.")
        print("[send_to_clay] Para el envio real: copiá .env.example a .env y pegá tu URL ahi.")

    with open(args.input, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"[send_to_clay] {len(rows)} filas para mandar. dry_run={dry_run}")

    log_path = args.log_output or cfg["output"]["send_log_csv"]
    results = []

    for i, row in enumerate(rows, start=1):
        if dry_run:
            results.append({"row_id": row.get("row_id", ""), "http_status": "DRY_RUN", "ok": True, "error": ""})
        else:
            results.append(send_row(webhook_url, row))
            time.sleep(args.delay_ms / 1000)

        if i % 50 == 0:
            print(f"[send_to_clay] {i}/{len(rows)} procesadas...")

    ok_count = sum(1 for r in results if r["ok"])
    fail_count = len(results) - ok_count

    with open(log_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["row_id", "http_status", "ok", "error"])
        writer.writeheader()
        writer.writerows(results)

    print(f"\n[send_to_clay] Resumen:")
    print(f"  Filas en el CSV de entrada: {len(rows)}")
    print(f"  Mandadas OK: {ok_count}")
    print(f"  Fallidas: {fail_count}")
    print(f"  Log completo: {log_path}")
    print(f"\n[send_to_clay] Paso de verificacion (Fase 3): comparar '{ok_count}' contra el numero de")
    print(f"  filas que efectivamente aparecen en la tabla de Clay. Si no cierran, revisar {log_path}")
    print(f"  para ver que row_id fallaron y por que.")

    if fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
