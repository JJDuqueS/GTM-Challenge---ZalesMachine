# ICP Qualifier

Filtro gratis antes de enriquecer caro. Toma un CSV crudo de empresas, normaliza sus dominios, verifica de verdad que cada sitio esté vivo (no solo HTTP 200), clasifica cada fila contra un ICP configurable con el motivo explícito, y devuelve un CSV único listo para mandar a Clay por webhook.

Construido para la Fase 1 del challenge de GTM Analyst de [ZalesMachine](https://zales-machine.com/) — ver [`SKILL.md`](./SKILL.md) para el razonamiento completo detrás de cada decisión.

## Por qué existe

> Filtrar antes de enriquecer. Todo paso que cuesta plata va después de todo paso que descarta gratis.

Este pipeline es ese paso que descarta gratis. Corriendo sobre 642 empresas reales, dejó pasar 290 (45%) a la fase de enriquecimiento con Clay — el resto se descartó o se marcó para revisión **antes** de gastar un solo crédito en ellas.

Es genérico a propósito: todo el ICP (industrias, tamaño, países, firmas de parking, columnas de export) vive en [`config/icp_config.json`](./config/icp_config.json). Un cliente nuevo es un archivo de config nuevo — cero cambios de código.

## Cómo funciona

```
CSV crudo (642 filas)
        │
        ▼
┌───────────────────────┐
│ 1. normalize_domains   │  saca protocolo, www, paths, tracking params
│    .py                 │  detecta links a redes sociales/forms
└───────────┬───────────┘
            ▼
┌───────────────────────┐
│ 2. verify_alive.py     │  ALIVE / DEAD (alta confianza)
│    (necesita internet  │  PARKED_CONFIRMED (alta confianza)
│     real, sin proxy    │  BLOCKED_BOT_PROTECTION / PARKED_LIKELY /
│     restrictivo)       │  SUSPICIOUS_RECYCLED / REDIRECTED_OFFSITE
└───────────┬───────────┘  (confianza media → nunca descartan solas)
            ▼
┌───────────────────────┐
│ 3. classify_icp.py     │  4 ejes: liveness, ubicación, tamaño, industria
│                        │  → QUALIFIED / NEEDS_REVIEW / DISQUALIFIED
└───────────┬───────────┘  con motivo explícito por fila
            ▼
┌───────────────────────┐
│ 4. export_for_clay.py  │  solo QUALIFIED, solo columnas mapeadas
└───────────┬───────────┘
            ▼
   output/clay_export.csv  ← entregable
            │
            ▼
┌───────────────────────┐
│ 5. send_to_clay.py     │  POST fila por fila al webhook de Clay
│    (Fase 2)            │  --dry-run por default si no hay CLAY_WEBHOOK_URL
└───────────────────────┘
```

## Quickstart

Requiere Python 3.9+ y conexión a internet sin proxy restrictivo (ver [Dónde correr esto](#dónde-correr-esto)).

```bash
pip install -r requirements.txt

# Pipeline completo: normaliza, verifica, clasifica, y arma el CSV para Clay
python3 scripts/run_pipeline.py

# Prueba rápida con pocas filas antes de correr todo
python3 scripts/run_pipeline.py --limit 25

# Fase 2: mandar el resultado a Clay (en seco por default, sin CLAY_WEBHOOK_URL en .env)
python3 scripts/send_to_clay.py --dry-run
```

`--input` y `--config` ya tienen default (`challenge_2026_raw.csv` y `config/icp_config.json`) — solo hace falta pasarlos para correr con otro dataset o cliente.

## Fuente adicional: fintechmexico.org

El challenge original también pedía scrapear dos páginas de la Asociación FinTech México además de usar el CSV — algo que quedó pendiente hasta que se agregó acá. Es opcional y se corre aparte, sin tocar el pipeline principal:

```bash
# 1. Scrapea /proveedores (36 empresas) y /membresia (tabs "Instituciones de
#    Tecnología Financiera" y "Miembros" — el tab "Integrantes", que son
#    proveedores no-fintech, se excluye a propósito)
python3 scripts/scrape_fintechmexico.py --output output/fintechmexico_scraped.csv

# 2. Fusiona con el CSV base, sacando duplicados por dominio (las filas del
#    base quedan primero e intactas, así conservan su row_id ya mandado a Clay)
python3 scripts/merge_sources.py --base challenge_2026_raw.csv \
    --scraped output/fintechmexico_scraped.csv --output output/combined_input.csv

# 3. Corre el pipeline normal sobre el combinado
python3 scripts/run_pipeline.py --input output/combined_input.csv

# 4. Antes de mandar a Clay: quedate SOLO con las filas nuevas (evita re-mandar
#    las 291 que ya mandaste). --min-row-id es "filas del CSV base + 1"
#    (merge_sources.py te lo imprime en su resumen).
python3 scripts/filter_new_rows.py --input output/clay_export.csv --min-row-id 643 \
    --output output/clay_export_new_only.csv
python3 scripts/send_to_clay.py --input output/clay_export_new_only.csv --dry-run
```

`scrape_fintechmexico.py` no se pudo probar contra la red real desde este entorno (mismo bloqueo de red que `verify_alive.py`, ver [Dónde correr esto](#dónde-correr-esto)) — la estructura de las páginas se inspeccionó a mano con un navegador real y la lógica de parsing se validó offline contra fixtures, pero revisá las primeras filas de `output/fintechmexico_scraped.csv` antes de fusionarlas.

## Estructura del proyecto

```
.
├── README.md                    # este archivo
├── SKILL.md                     # skill de Claude Code — el razonamiento detrás de cada decisión
├── requirements.txt
├── challenge_2026_raw.csv       # input de ejemplo (el CSV real del challenge)
├── config/
│   └── icp_config.json          # TODO el ICP: industrias, tamaño, países, firmas, columnas de export
└── scripts/
    ├── run_pipeline.py          # orquestador — corre todo en orden
    ├── normalize_domains.py     # paso 1
    ├── verify_alive.py          # paso 2
    ├── classify_icp.py          # paso 3
    ├── export_for_clay.py       # paso 4
    └── send_to_clay.py          # paso 5 (Fase 2 del challenge)
```

## Output

| Archivo | Contenido |
| --- | --- |
| `output/audit_all_companies.csv` | Todas las filas, con motivo de cada decisión. Nunca se borra nada. |
| `output/needs_review.csv` | Zona gris — las revisa una persona. |
| `output/qualified_companies.csv` | Calificadas, todas las columnas (debug interno). |
| **`output/clay_export.csv`** | **El entregable.** Una fila por empresa calificada, solo las columnas que Clay necesita. |
| `output/clay_send_log.csv` | Resultado del envío por webhook, fila por fila — la base de la verificación de Fase 3. |

## Configuración

Todo lo que cambia entre clientes o entre corridas vive en `config/icp_config.json`, no en el código:

- `company_size`, `locations`, `industry` — la definición del ICP.
- `liveness_check` — timeouts, concurrencia, firmas de parking/spam.
- `clay_export.fields` — qué columnas salen en el CSV final y cómo se llaman.

**La URL del webhook de Clay no está acá.** Es un secreto, no una preferencia de
negocio — este archivo se versiona en git, un secreto no. Ver [Secrets](#secrets--variables-de-entorno).

## Secrets / variables de entorno

| Variable | Dónde vive | Qué es |
| --- | --- | --- |
| `CLAY_WEBHOOK_URL` | `.env` (nunca se commitea — está en `.gitignore`) | URL del webhook de la tabla de Clay. Sin esto, `send_to_clay.py` corre siempre en `--dry-run`, nunca manda nada por accidente. |

```bash
cp .env.example .env
# Editar .env y pegar la URL real de CLAY_WEBHOOK_URL
```

`send_to_clay.py` la carga sola al arrancar (via `python-dotenv`, ya en `requirements.txt`). También se puede pasar por línea de comando con `--webhook-url`, sin tocar `.env`, para no dejar ni un rastro del secreto en un archivo.

## Hallazgos reales (dataset del challenge, 642 empresas)

Documentados en detalle en `SKILL.md`, resumidos acá porque son la evidencia de por qué el pipeline distingue señales de "alta confianza" (descalifican solas) de señales de "confianza media" (fuerzan revisión humana, nunca descarte automático):

| Hallazgo | Señal que lo detectó | Resultado |
| --- | --- | --- |
| `Cervecería Modelo` tageada como *"computer software company"* por la fuente | Cruce del nombre de la empresa contra palabras negativas | `NEEDS_REVIEW`, no pasó colada |
| `Inmuebles24` (portal real, top de México) devolvía HTTP 403 | Distinguir bloqueo de bot (WAF) de sitio muerto | `NEEDS_REVIEW`, no se perdió el lead |
| `Dextra Technologies` → `deloitte.com` (adquisición real) | Redirect a raíz de dominio distinta | `NEEDS_REVIEW`, no `DISQUALIFIED` |
| `pagofacil.net` reciclado 100% a contenido de casino | Firma de spam en el body | `DISQUALIFIED` — confirmado con WebFetch |
| `zafirosoft.com` (ERP real) con "casino" en un banner de cookies de terceros | Misma firma de spam — **falso positivo confirmado** | `NEEDS_REVIEW`, no `DISQUALIFIED` |
| `novutek.com` redirige a `forsale.godaddy.com` | Contenido inicial <200 bytes | `PARKED_LIKELY` → confirmado como dominio en venta real |

19 de 85 sitios inicialmente marcados "muertos" resultaron ser bloqueo de bot, no caídos — el 22% de esa categoría.

## Dónde correr esto

El paso 2 (`verify_alive.py`) necesita salir a internet sin restricciones — cientos de requests HTTP reales. Si se corre desde un entorno con egress limitado a una allowlist (un sandbox corporativo, un CI con red restringida, un proxy gestionado por política de cuenta), el paso va a fallar o marcar todo como bloqueado, no porque los sitios estén caídos sino por la red del entorno. Correr desde una terminal con internet normal.

## Fase 2 — Automatizar el envío (n8n / API)

`send_to_clay.py` ya hace lo que pide la Fase 2 del challenge: manda el CSV limpio al webhook de Clay, fila por fila, desde Claude Code. Para que se dispare solo (sin correrlo a mano cada vez), sin tocar la lógica:

- Un *Schedule Trigger* o *Watch Folder* de n8n llama al script vía *Execute Command*.
- O un flujo nativo de n8n (*Read Binary File* → *Split in Batches* → *HTTP Request*) reimplementa el mismo POST fila por fila, leyendo directamente `output/clay_export.csv`.

En ambos casos, `clay_export.csv` es el contrato: mientras la cabecera de columnas no cambie, no importa qué dispare el envío.

## Notas

- Prioriza claridad sobre "código de producción" — es intencional (ver el pedido del challenge). Cada decisión no obvia está comentada in situ y documentada en `SKILL.md`.
- Sin dependencias más allá de `requests`. Sin frameworks, sin abstracciones que no ganan nada con 5 scripts de este tamaño.
- Autor: Juan — challenge GTM Analyst, ZalesMachine, septiembre 2026.
