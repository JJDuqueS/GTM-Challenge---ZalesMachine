#!/usr/bin/env python3
"""
Paso 2: Verificacion de que el sitio este REALMENTE vivo.

Un HTTP 200 no alcanza. Chequea, en orden:
  1. Resuelve DNS y conecta (si no, DEAD - alta confianza).
  2. Sigue redirects y mira a donde termina de verdad.
  3. Si el status final es 403/429, BLOCKED_BOT_PROTECTION (NO es DEAD: es un WAF
     bloqueando el request automatizado, no evidencia de que el sitio este caido).
  4. Si el status final es otro codigo de error, DEAD (alta confianza).
  5. Busca firmas EXPLICITAS de parking (GoDaddy/Sedo/Dan.com/HugeDomains/
     "domain for sale"/etc.) en <title> o body -> PARKED_CONFIRMED (alta confianza:
     es una frase inequivoca).
  6. Si el contenido es sospechosamente chico pero SIN firma explicita -> PARKED_LIKELY
     (confianza media: puede ser parking servido por JS, pero tambien puede ser una
     app moderna tipo SPA con poco HTML inicial - no se asume, se manda a revision).
  7. Busca firmas de contenido reciclado por spam (casino, farmacia online, etc.)
     -> SUSPICIOUS_RECYCLED (confianza media: verificado en este dataset que puede
     ser un falso positivo por boilerplate de terceros - banners de cookies/ads que
     listan categorias IAB completas sin que el sitio tenga nada que ver. Igual vale
     la pena marcarlo, pero no descartar solo).
  8. Si el dominio final (post-redirect) cambio de raiz -> REDIRECTED_OFFSITE
     (confianza baja para descartar: en este dataset la mayoria fueron migraciones
     o adquisiciones legitimas - ej. una empresa comprada por Deloitte redirigiendo
     a deloitte.com. Nunca se descarta solo).

Los primeros 2 statuses (DEAD, PARKED_CONFIRMED) son de alta confianza y el paso 3
(classify_icp.py) los trata como descalificacion dura. Los otros 3
(BLOCKED_BOT_PROTECTION, PARKED_LIKELY, SUSPICIOUS_RECYCLED, REDIRECTED_OFFSITE) son
señales blandas: classify_icp.py los manda a NEEDS_REVIEW, nunca a DISQUALIFIED
en silencio.

Corre con concurrencia (ThreadPoolExecutor) porque son cientos de sitios.

Uso:
    python3 verify_alive.py --input output/step1_normalized.csv --output output/step2_verified.csv --config config/icp_config.json
"""
import argparse
import csv
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from normalize_domains import get_root_domain


def build_session(user_agent: str) -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    })
    retries = Retry(total=1, backoff_factor=0.5, status_forcelist=[502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))
    session.mount("http://", HTTPAdapter(max_retries=retries))
    return session


def check_one_domain(domain: str, session: requests.Session, cfg: dict) -> dict:
    """Devuelve el veredicto de vida para un dominio. No lanza excepciones hacia afuera."""
    result = {
        "domain_checked": domain,
        "http_status": None,
        "final_url": None,
        "final_domain": None,
        # ALIVE | DEAD | BLOCKED_BOT_PROTECTION | PARKED_CONFIRMED | PARKED_LIKELY
        # | SUSPICIOUS_RECYCLED | REDIRECTED_OFFSITE
        "liveness_status": "DEAD",
        "liveness_reason": "",
        "content_length": None,
    }

    if not domain:
        result["liveness_reason"] = "sin dominio para chequear"
        return result

    timeout = cfg["liveness_check"]["timeout_seconds"]
    max_redirects = cfg["liveness_check"]["max_redirects"]
    min_len = cfg["liveness_check"]["min_content_length_bytes"]

    resp = None
    last_error = None
    for scheme in ("https://", "http://"):
        try:
            resp = session.get(
                scheme + domain,
                timeout=timeout,
                allow_redirects=True,
            )
            if len(resp.history) > max_redirects:
                last_error = "demasiados redirects"
                resp = None
                continue
            break
        except requests.exceptions.SSLError as e:
            last_error = f"error SSL: {e}"
            continue
        except requests.exceptions.ConnectionError as e:
            last_error = f"error de conexion: {e}"
            continue
        except requests.exceptions.Timeout:
            last_error = "timeout"
            continue
        except requests.exceptions.RequestException as e:
            last_error = f"error: {e}"
            continue

    if resp is None:
        result["liveness_reason"] = last_error or "no se pudo conectar"
        return result

    result["http_status"] = resp.status_code
    result["final_url"] = resp.url
    result["final_domain"] = get_root_domain(
        re.sub(r"^https?://", "", resp.url).split("/")[0].split(":")[0].lower()
    )

    if resp.status_code in (403, 429):
        # No es lo mismo que DEAD: es un WAF/rate-limiter bloqueando un request
        # automatizado (sin headers de navegador real, sin cookies, etc.). Encontrado
        # en este dataset sobre dominios de empresas reales y grandes (ej. un
        # portal inmobiliario top de Mexico) - descartarlos como DEAD hubiera sido
        # un falso negativo caro. Se manda a revision, no se asume nada.
        result["liveness_reason"] = f"HTTP {resp.status_code} - posible bloqueo de bot (WAF/rate-limit), no necesariamente caido"
        result["liveness_status"] = "BLOCKED_BOT_PROTECTION"
        return result

    if resp.status_code >= 400:
        result["liveness_reason"] = f"HTTP {resp.status_code} en la URL final"
        return result

    body = resp.text or ""
    result["content_length"] = len(body)
    body_lower = body.lower()
    title_match = re.search(r"<title[^>]*>(.*?)</title>", body_lower, re.DOTALL)
    title = title_match.group(1) if title_match else ""

    # Firma EXPLICITA de parking - frase inequivoca, alta confianza
    for sig in cfg["liveness_check"]["parking_signatures"]:
        if sig.lower() in body_lower or sig.lower() in title:
            result["liveness_status"] = "PARKED_CONFIRMED"
            result["liveness_reason"] = f"firma de parking encontrada: '{sig}'"
            return result

    # Contenido sospechosamente chico SIN firma de texto - confianza media.
    # Verificado en este dataset: puede ser un dominio parkeado de verdad (redirige a
    # una pagina de reventa que carga los datos por JS, sin texto en el HTML inicial),
    # pero el mismo patron tambien lo produce una app moderna (React/Vue/Angular) que
    # sirve un HTML casi vacio y arma la pagina en el navegador. No se puede
    # distinguir sin ejecutar JS, asi que no se descarta solo.
    if result["content_length"] < min_len:
        result["liveness_status"] = "PARKED_LIKELY"
        result["liveness_reason"] = (f"contenido de solo {result['content_length']} bytes (< {min_len}) - "
                                      f"puede ser parking o una app que carga por JS, revisar a mano")
        return result

    # Firma de contenido reciclado por spam
    for sig in cfg["liveness_check"]["spam_recycled_signatures"]:
        if sig.lower() in body_lower or sig.lower() in title:
            result["liveness_status"] = "SUSPICIOUS_RECYCLED"
            result["liveness_reason"] = f"firma de contenido reciclado/spam: '{sig}'"
            return result

    # Redirect a una raiz de dominio distinta (posible dominio reciclado o migracion legitima)
    if result["final_domain"] and result["final_domain"] != domain:
        result["liveness_status"] = "REDIRECTED_OFFSITE"
        result["liveness_reason"] = f"redirige de {domain} a {result['final_domain']} - revisar manualmente"
        return result

    result["liveness_status"] = "ALIVE"
    result["liveness_reason"] = "respondio 2xx, con contenido, sin firmas de parking/spam"
    return result


def main():
    ap = argparse.ArgumentParser(description="Verifica que cada dominio este realmente vivo.")
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--limit", type=int, default=None, help="Solo procesar las primeras N filas (para pruebas)")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)

    with open(args.input, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames

    if args.limit:
        rows = rows[:args.limit]

    to_check = [(i, row) for i, row in enumerate(rows) if row.get("domain_has_value") == "True"]
    print(f"[verify_alive] {len(to_check)} dominios para chequear de {len(rows)} filas totales.")

    session = build_session(cfg["liveness_check"]["user_agent"])
    concurrency = cfg["liveness_check"]["concurrency"]

    results_by_index = {}
    start = time.time()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {
            pool.submit(check_one_domain, row["domain_normalized"], session, cfg): i
            for i, row in to_check
        }
        done = 0
        for fut in as_completed(futures):
            i = futures[fut]
            results_by_index[i] = fut.result()
            done += 1
            if done % 50 == 0:
                print(f"[verify_alive] {done}/{len(to_check)} chequeados...")

    elapsed = time.time() - start
    print(f"[verify_alive] Listo en {elapsed:.1f}s")

    liveness_fields = [
        "domain_checked", "http_status", "final_url", "final_domain",
        "liveness_status", "liveness_reason", "content_length",
    ]
    out_fields = list(fieldnames) + liveness_fields

    status_counts = {}
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields)
        writer.writeheader()
        for i, row in enumerate(rows):
            if i in results_by_index:
                row.update(results_by_index[i])
            else:
                row.update({k: "" for k in liveness_fields})
                row["liveness_status"] = "NOT_CHECKED"
                row["liveness_reason"] = "sin dominio normalizado valido"
            status_counts[row["liveness_status"]] = status_counts.get(row["liveness_status"], 0) + 1
            writer.writerow(row)

    print("[verify_alive] Resumen de status:")
    for status, count in sorted(status_counts.items(), key=lambda x: -x[1]):
        print(f"  {status}: {count}")
    print(f"[verify_alive] Escribi: {args.output}")


if __name__ == "__main__":
    main()
