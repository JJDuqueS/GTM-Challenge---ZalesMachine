#!/usr/bin/env python3
"""
Fuente adicional (pedida por Nicolas en el correo original, nunca incorporada
hasta ahora): scrapea dos paginas de fintechmexico.org para generar una lista
de empresas candidatas, en el MISMO formato de columnas que challenge_2026_raw.csv,
para que corra por el pipeline existente (normalize -> verify -> classify -> export)
sin tocar ni una linea de esos scripts.

Fuentes:
  1. https://www.fintechmexico.org/proveedores
     36 proveedores. La pagina de listado NO tiene el sitio externo de cada uno
     (solo un link interno a /proveedores/<slug> + un PDF de oferta comercial),
     asi que este script visita cada una de las 36 paginas de perfil y saca de
     ahi el primer link externo que no sea red social / PDF / fintechmexico.org.

  2. https://www.fintechmexico.org/membresia
     Tiene 3 tabs (confirmado a mano con el navegador, no por scraping):
       - "Instituciones de Tecnologia Financiera" (pane 0) -> SE USA
       - "Miembros"                                 (pane 1) -> SE USA
       - "Integrantes"                               (pane 2) -> NO se usa
     Nicolas/Juan solo quieren las primeras dos (empresas fintech de verdad;
     "Integrantes" son proveedores no-fintech tipo despachos de abogados, que
     ya cubre la fuente 1 y no aportan al ICP). Los 3 tabs viven en el MISMO
     html (Webflow los intercambia con CSS/JS, no con requests separados), así
     que una sola descarga de /membresia alcanza para las 3 - simplemente no
     leemos el pane 2.

IMPORTANTE - este script no se pudo probar contra la red real desde este
sandbox (mismo bloqueo de red de cuenta/org que ya conoces de verify_alive.py -
ver SKILL.md "Donde correr esto"). La estructura de cada pagina la inspeccione
a mano con un navegador real y estan documentados los selectores exactos que
encontre (los ids w-tabs-0-data-w-pane-0/1/2 de Webflow, los hrefs /proveedores/
<slug>), pero el parsing tiene fallbacks defensivos por si el layout cambia
un poco. CORRELO Y MIRA LAS PRIMERAS FILAS DEL CSV DE SALIDA A MANO antes de
fusionarlo con el CSV principal (scripts/merge_sources.py hace esa fusion).

Uso:
    pip install beautifulsoup4  # si no lo tenes ya (requests ya esta en requirements.txt)
    python3 scripts/scrape_fintechmexico.py --output output/fintechmexico_scraped.csv

    # Para debug rapido, sin pegarle a las 36 paginas de perfil de proveedores:
    python3 scripts/scrape_fintechmexico.py --output output/fintechmexico_scraped.csv --skip-proveedor-profiles
"""
import argparse
import csv
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent))
from normalize_domains import get_root_domain  # misma logica de dominio raiz que el resto del pipeline

BASE = "https://www.fintechmexico.org"
PROVEEDORES_URL = f"{BASE}/proveedores"
MEMBRESIA_URL = f"{BASE}/membresia"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
}
TIMEOUT = 15

# Dominios que NO cuentan como "el sitio propio de la empresa" cuando buscamos
# el link externo en una pagina de perfil de proveedor.
#
# condusef.gob.mx, fintechmexicofestival.com, ftmxweek.org: bugs reales que
# encontramos probando contra el sitio en vivo - TODAS las paginas (listado y
# cada perfil) tienen links fijos de navegacion a estos 3 sitios (el boton
# "Verifica aqui" de CONDUSEF, y los links a los eventos "FinTech Mexico
# Festival" y "FTMX Week"). Sin excluirlos, el scraper podia agarrar cualquiera
# de estos como "Website" de una empresa (el primer link externo del DOM, sin
# importar cual sea la empresa) en vez del sitio real - confirmado en vivo con
# Auronix (agarraba fintechmexicofestival.com) y 4iDIGITAL (agarraba el de
# CONDUSEF) antes de este fix.
EXCLUDE_HOST_SUBSTRINGS = [
    "fintechmexico.org", "website-files.com", "webflow.io",
    "facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com",
    "youtube.com", "wa.me", "whatsapp.com", "tiktok.com",
    "condusef.gob.mx", "gob.mx",
    "fintechmexicofestival.com", "ftmxweek.org",
]

# Los 3 tabs de /membresia, en el orden real de la pagina (confirmado a mano
# con el navegador Y con un dump del HTML estatico real). Solo usamos los
# primeros 2 - ver docstring arriba.
#
# IMPORTANTE: el HTML que trae requests.get() (sin JS) NO tiene los ids
# "w-tabs-0-data-w-pane-N" que se ven en el navegador - Webflow los asigna
# recien en tiempo de ejecucion. Confirmado con un dump real: los 3 panes NO
# tienen id (id=None), pero SI tienen la clase Webflow estandar "w-tab-pane",
# como hijos directos de un div "tabs-content w-tab-content", en el mismo
# orden que los tabs visibles (29 links / 165 links / 29 links - coincide
# exacto con Instituciones / Miembros / Integrantes). Por eso identificamos
# los panes por POSICION dentro de ese contenedor, no por id.
MEMBRESIA_PANE_INDEXES = [
    (0, "fintechmexico_membresia_instituciones"),
    (1, "fintechmexico_membresia_miembros"),
    # (2, "fintechmexico_membresia_integrantes"),  # excluido a proposito
]

# Columnas del CSV de salida: el subconjunto de challenge_2026_raw.csv que el
# pipeline realmente lee/escribe, mas "Source" (propia, para trazabilidad -
# no rompe nada porque normalize/classify solo leen columnas por nombre).
OUTPUT_FIELDS = [
    "Company Name", "Website", "Industry", "Industry Tags", "Description",
    "Product and Services", "Employee Size", "Company Country", "LinkedIn",
    "Source",
]


def get_soup(url: str) -> BeautifulSoup:
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def domain_of(url: str) -> str:
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def is_excluded_host(url: str) -> bool:
    # Bug real encontrado y corregido: un "bad in host" por substring hacia que
    # "x.com" (pensado para excluir Twitter/X) matcheara CUALQUIER dominio que
    # termine en esas letras - "auronix.com" contiene literalmente "x.com" como
    # substring, asi que el sitio real de Auronix se descartaba por error.
    # Ahora comparamos por dominio exacto o subdominio real (host == bad, o
    # host termina en ".bad"), nunca por substring suelto.
    host = domain_of(url).split(":")[0]
    host = re.sub(r"^www\d*\.", "", host)
    return any(host == bad or host.endswith("." + bad) for bad in EXCLUDE_HOST_SUBSTRINGS)


def name_from_domain(url: str) -> str:
    """Fallback cuando no encontramos un nombre visible: 'www.dynamicore.io' -> 'Dynamicore'.

    Bug real encontrado y corregido: esto solo sacaba el prefijo 'www.' antes de
    tomar el primer segmento del host, asi que un subdominio real como
    'web.didiglobal.com' daba "Web" en vez de reflejar DiDi, 'mx.cobre.co' daba
    "Mx" en vez de Cobre, etc. Ahora reducimos el host a su dominio raiz
    (get_root_domain, la misma logica que ya usa normalize_domains.py /
    merge_sources.py para deduplicar) antes de tomar el primer segmento, asi
    que un subdominio ya no "roba" el nombre.
    """
    host = domain_of(url).split(":")[0]
    host = re.sub(r"^www\d*\.", "", host)
    root = get_root_domain(host) if host else ""
    label = root.split(".")[0] if root else (host.split(".")[0] if host else url)
    label = re.sub(r"[-_]+", " ", label)
    return label.strip().title() or url


def scrape_proveedores(skip_profiles: bool) -> list:
    print(f"[scrape] GET {PROVEEDORES_URL}")
    soup = get_soup(PROVEEDORES_URL)

    cards = []
    seen_slugs = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not re.match(r"^/proveedores/[a-z0-9\-]+/?$", href):
            continue
        slug = href.strip("/").split("/")[-1]
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)

        # El nombre suele estar en un heading (h1-h6) dentro de la misma card
        # que envuelve este link. Buscamos hacia arriba unos niveles.
        name = None
        container = a
        for _ in range(4):
            if container is None:
                break
            heading = container.find(["h1", "h2", "h3", "h4", "h5", "h6"])
            if heading and heading.get_text(strip=True):
                name = heading.get_text(strip=True)
                break
            container = container.parent
        if not name:
            name = slug.replace("-", " ").title()

        cards.append({"slug": slug, "name": name, "profile_url": urljoin(BASE, href)})

    print(f"[scrape] Proveedores encontrados en el listado: {len(cards)}")

    rows = []
    for i, card in enumerate(cards, start=1):
        website = ""
        if not skip_profiles:
            try:
                print(f"[scrape]   ({i}/{len(cards)}) perfil de {card['name']} -> {card['profile_url']}")
                psoup = get_soup(card["profile_url"])
                candidates = [a for a in psoup.find_all("a", href=True)
                              if a["href"].startswith("http") and not is_excluded_host(a["href"])]

                # Patron real confirmado a mano en el sitio: el link al sitio de la
                # empresa tiene como TEXTO VISIBLE su propia URL (ej. un heading que
                # dice literalmente "https://4i.digital/"). Preferimos ese matcheo
                # exacto por sobre "el primer link no excluido", que es mas fragil
                # (fue justamente lo que causo el bug de CONDUSEF - un link fijo del
                # header que aparece antes que el de la empresa en el DOM).
                website = ""
                for a in candidates:
                    text = a.get_text(strip=True)
                    if text.lower().startswith("http"):
                        website = a["href"]
                        break
                if not website and candidates:
                    website = candidates[0]["href"]
                time.sleep(0.3)  # no golpear el sitio de una
            except requests.exceptions.RequestException as e:
                print(f"[scrape]   ADVERTENCIA: no pude leer el perfil de {card['name']}: {e}")

        if not website:
            print(f"[scrape]   sin sitio externo detectado para '{card['name']}' "
                  f"(perfil: {card['profile_url']}) - queda con Website vacio, se va a marcar solo en el pipeline")

        rows.append({
            "Company Name": card["name"],
            "Website": website,
            "Industry": "Financial Technology",
            "Industry Tags": "fintech proveedor pool de proveedores",
            "Description": f"Proveedor del Pool de Proveedores de FinTech Mexico. Perfil: {card['profile_url']}",
            "Product and Services": "",
            "Employee Size": "",
            "Company Country": "Mexico",
            "LinkedIn": "",
            "Source": "fintechmexico_proveedores",
        })

    return rows


def scrape_membresia() -> list:
    print(f"[scrape] GET {MEMBRESIA_URL}")
    soup = get_soup(MEMBRESIA_URL)

    # Los panes no tienen id en el HTML estatico (ver comentario en
    # MEMBRESIA_PANE_INDEXES) - los identificamos por posicion dentro del
    # contenedor "tabs-content w-tab-content". Buscamos ese contenedor por
    # clase; si no aparece, caemos a buscar w-tab-pane en toda la pagina
    # (menos preciso, pero mejor que no traer nada).
    container = soup.find("div", class_=lambda c: c and "w-tab-content" in c)
    panes = container.find_all("div", class_=lambda c: c and "w-tab-pane" in c, recursive=False) \
        if container else []
    if not panes:
        print("[scrape] ADVERTENCIA: no encontre 'div.w-tab-content' - probando fallback (w-tab-pane en toda la pagina)")
        panes = soup.find_all("div", class_=lambda c: c and "w-tab-pane" in c)

    rows = []
    seen_domains_this_source = set()
    for pane_idx, source_tag in MEMBRESIA_PANE_INDEXES:
        if pane_idx >= len(panes):
            print(f"[scrape] ADVERTENCIA: esperaba encontrar al menos {pane_idx + 1} tabs en /membresia, "
                  f"encontre {len(panes)} - el layout de la pagina puede haber cambiado. Revisa /membresia "
                  f"a mano y ajusta MEMBRESIA_PANE_INDEXES en este script.")
            continue
        pane = panes[pane_idx]

        count_before = len(rows)
        for a in pane.find_all("a", href=True):
            href = a["href"]
            if not href.startswith("http"):
                continue
            if is_excluded_host(href):
                continue

            dom = domain_of(href)
            if dom in seen_domains_this_source:
                continue
            seen_domains_this_source.add(dom)

            # Nombre: alt de un img adentro del link, si existe; si no, del dominio.
            name = None
            img = a.find("img")
            if img is not None:
                alt = (img.get("alt") or img.get("title") or "").strip()
                if alt:
                    # Los alt suelen venir con sufijos tipo " - FinTech Mexico" o " FinTech México"
                    alt = re.sub(r"\s*[-|]?\s*fin\s*tech\s*m[eé]xico\s*$", "", alt, flags=re.IGNORECASE).strip()
                    if alt:
                        name = alt
            if not name:
                name = name_from_domain(href)

            rows.append({
                "Company Name": name,
                "Website": href,
                "Industry": "Financial Technology",
                "Industry Tags": "fintech membresia asociacion fintech mexico",
                "Description": "Miembro de la Asociacion FinTech Mexico.",
                "Product and Services": "",
                "Employee Size": "",
                "Company Country": "Mexico",
                "LinkedIn": "",
                "Source": source_tag,
            })

        print(f"[scrape] Pane #{pane_idx} ({source_tag}): {len(rows) - count_before} empresas")

    return rows


def main():
    ap = argparse.ArgumentParser(description="Scrapea fintechmexico.org/proveedores y /membresia (tabs 1 y 2).")
    ap.add_argument("--output", default="output/fintechmexico_scraped.csv")
    ap.add_argument("--skip-proveedor-profiles", action="store_true",
                     help="No visita las 36 paginas de perfil de /proveedores (mas rapido, pero sin Website "
                          "para esa fuente - util solo para probar que el resto del script corre bien)")
    args = ap.parse_args()

    all_rows = []
    try:
        all_rows.extend(scrape_proveedores(args.skip_proveedor_profiles))
    except requests.exceptions.RequestException as e:
        sys.exit(f"ERROR scrapeando /proveedores: {e}")

    try:
        all_rows.extend(scrape_membresia())
    except requests.exceptions.RequestException as e:
        sys.exit(f"ERROR scrapeando /membresia: {e}")

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(all_rows)

    by_source = {}
    for r in all_rows:
        by_source[r["Source"]] = by_source.get(r["Source"], 0) + 1

    print(f"\n[scrape] Total de filas: {len(all_rows)}")
    for source, count in sorted(by_source.items()):
        print(f"  {source}: {count}")
    print(f"[scrape] Escribi: {args.output}")
    print("\n[scrape] Revisa a mano las primeras filas de este CSV antes de fusionarlo -")
    print("[scrape] el parsing de 'Company Name' y 'Website' tiene fallbacks, no es 100% infalible.")


if __name__ == "__main__":
    main()
