---
name: icp-qualifier
description: Toma un CSV crudo de empresas (de Apollo, Clay, un scrape, lo que sea), normaliza sus dominios, verifica de verdad que cada sitio esté vivo (no solo HTTP 200 — detecta dominios parkeados, en venta, o reciclados por spam), y clasifica cada fila contra un ICP configurable, dejando escrito el motivo empresa por empresa. Devuelve un CSV limpio listo para enriquecer (Clay, Apollo, lo que sea) y nunca descarta nada en silencio. Usar cuando llega una lista cruda de empresas y hay que filtrarla antes de gastar créditos de enriquecimiento en ella.
---

# ICP Qualifier

## Por qué existe

Filtrar antes de enriquecer. Todo paso que cuesta plata (Clay, Apollo, una
llamada a un LLM) tiene que correr **después** de todo paso que descarta
gratis. Este skill es el paso que descarta gratis.

Es genérico a propósito: el ICP completo — industrias, tamaño, países, roles,
firmas de parking — vive en `config/icp_config.json`. Para un cliente nuevo
se edita ese archivo. El código no cambia.

## Cuándo usarlo

- Llegó un CSV crudo de empresas y hay que decidir cuáles entran a una
  campaña antes de gastar créditos enriqueciéndolas.
- Hay que demostrar, fila por fila, por qué una empresa calificó o no
  (auditoría, no solo un output).
- Los dominios vienen sucios: con `http://`, `www`, paths, parámetros de
  tracking, o apuntando directamente a una red social en vez del sitio
  propio.

## Qué NO hace

- No busca personas ni contactos dentro de las empresas (eso es Clay/Apollo,
  Fase 2 del proceso — ver `PIPELINE.md` de referencia en el challenge de
  ZalesMachine).
- No escribe copy ni manda mensajes.
- No decide solo los casos de zona gris: los deja marcados en
  `NEEDS_REVIEW` con el motivo exacto, para que los revise una persona. Un
  script que "resuelve" solo los casos ambiguos es exactamente lo que este
  rol no quiere.

## Cómo correrlo

Requiere Python 3.9+ y la librería `requests` (`pip install requests`).

```bash
# 1. Correlo completo contra el CSV real (esto SI necesita internet real,
#    sin proxy restrictivo — correr en tu terminal local, no en un sandbox
#    con egress limitado):
python3 scripts/run_pipeline.py --input mi_csv_crudo.csv --config config/icp_config.json

# 2. Para probar rápido con pocas filas antes de correr las 600+:
python3 scripts/run_pipeline.py --input mi_csv_crudo.csv --config config/icp_config.json --limit 25
```

Esto corre los 4 pasos en orden y deja todo en `output/`:

| Archivo | Qué es |
| --- | --- |
| `output/step1_normalized.csv` | Paso 1 solo, para debug |
| `output/step2_verified.csv` | Paso 1+2, para debug |
| `output/audit_all_companies.csv` | **Todas** las filas originales, con columnas de normalización + liveness + clasificación + motivo. Nunca se borra nada de acá. |
| `output/needs_review.csv` | Las `NEEDS_REVIEW`: zona gris de industria/tamaño, o señal de liveness de confianza media. Las revisa una persona antes de decidir si entran. |
| `output/qualified_companies.csv` | Las `QUALIFIED`, con **todas** las columnas originales — para auditoría/debug interno, no para mandar afuera. |
| **`output/clay_export.csv`** | **El entregable.** Un solo CSV, solo las `QUALIFIED`, solo las columnas que define `clay_export.fields` en el config. Esto es lo que se manda al webhook de Clay en Fase 2. |

También se puede correr cada paso suelto (`normalize_domains.py`,
`verify_alive.py`, `classify_icp.py`, `export_for_clay.py`) si hace falta
debuggear uno en particular — cada uno tiene su propio `--help`.

## Los 4 pasos, y las decisiones detrás de cada uno

### Paso 1 — Normalización de dominios (`normalize_domains.py`)

Saca protocolo, `www`/`www2`, paths, query params y puerto. Respeta ccSLDs
de LATAM (`empresa.com.mx` no se corta mal en `com.mx`). Además detecta
cuando el campo "Website" en realidad apunta a Facebook, un form de Google,
etc. — eso no es el sitio propio de la empresa, así que se flagea aparte
(`flag_social_or_form_link`) en vez de tratarlo como un dominio normal.

Sin dominio utilizable → la fila no entra al paso 2 (no hay nada que
verificar), y queda marcada en la auditoría final. No se intenta adivinar
un dominio a partir del nombre de la empresa: es una fuente de falsos
positivos más cara de limpiar que el tiempo que ahorra.

### Paso 2 — Verificación de que esté vivo de verdad (`verify_alive.py`)

Esta es la parte que el challenge pide leer dos veces. Un HTTP 200 no
prueba nada. Pero la primera versión de este chequeo tenía el problema
opuesto también: trataba **toda** señal de "algo no cierra" como si fuera
la misma certeza, y descalificaba duro casos que en la corrida real contra
las 642 empresas del challenge resultaron ser, en su mayoría, falsos
positivos. Quedó documentado abajo porque es el ejemplo más concreto de
"cómo verificaste" de todo este proceso.

El chequeo separa dos niveles de confianza:

**Alta confianza (descalifica duro):**

1. **¿Conecta?** Si no resuelve DNS o la conexión se rechaza de verdad →
   `DEAD`.
2. **¿Hay una firma de parking EXPLÍCITA?** Frases inequívocas como
   *"domain is for sale"*, *"buy this domain"*, o nombres de
   registradores/marketplaces (GoDaddy, Sedo, Dan.com, HugeDomains,
   Afternic, Bodis) en el `<title>` o el body → `PARKED_CONFIRMED`.

**Confianza media (nunca descalifica sola — fuerza `NEEDS_REVIEW`):**

3. **¿El status final es 403 o 429?** → `BLOCKED_BOT_PROTECTION`. **Esto no
   estaba en la primera versión y fue el hallazgo más caro de no tenerlo:**
   contra el dataset real, 19 de 85 sitios marcados como "muertos" en
   realidad devolvían 403/429 — un WAF bloqueando un request sin headers de
   navegador, no un sitio caído. Entre ellos, **Inmuebles24** (uno de los
   portales inmobiliarios más grandes de México) y un software de punto de
   venta real (SICAR). Tratarlos como `DEAD` los hubiera descartado para
   siempre sin que nadie lo notara.
4. **¿El contenido es sospechosamente chico, pero sin firma explícita?** →
   `PARKED_LIKELY`. Puede ser un dominio parkeado que carga el aviso por JS
   (confirmado en este dataset: un caso redirigía a
   `forsale.godaddy.com` y el body igual pesaba <200 bytes), pero el mismo
   patrón lo produce una app moderna (React/Vue/Angular) que sirve un HTML
   casi vacío y arma la página en el navegador. No se puede distinguir sin
   ejecutar JS, así que no se asume.
5. **¿Hay una firma de contenido reciclado por spam?** Casino, apuestas,
   farmacia online → `SUSPICIOUS_RECYCLED`. **Verificado en este dataset
   que esto tiene falsos positivos reales:** de 7 dominios marcados, uno
   (`pagofacil.net`) resultó ser un reciclado genuino a contenido 100% de
   casino — pero otro (`zafirosoft.com`, una ERP mexicana real) tenía la
   palabra "casino" en algún banner de cookies/ads de terceros que lista
   categorías IAB completas sin que tenga nada que ver con el sitio. Con
   una muestra de 2 ya salió 1 falso positivo — suficiente para no
   descartar en base a esto solo.
6. **¿El dominio final cambió de raíz?** → `REDIRECTED_OFFSITE`. De 35
   casos en la corrida real, la enorme mayoría fueron migraciones o
   adquisiciones legítimas (`Dextra Technologies` → `deloitte.com` — una
   adquisición real; `Apli` → `icims.com` — otra adquisición real; varios
   cambios simples de TLD como `.com.mx` → `.mx`). Ninguno se descarta
   solo.

Todas las firmas y los códigos HTTP que cuentan como bloqueo de bot viven
en `config/icp_config.json` (`liveness_check`), así que agregar un
registrador nuevo o un patrón de spam nuevo es editar una lista, no tocar
código.

### Paso 3 — Clasificación contra el ICP (`classify_icp.py`)

Evalúa 4 ejes independientes, cada uno con su propio motivo:

- **Liveness** (heredado del paso 2): `ALIVE` pasa. `DEAD` y
  `PARKED_CONFIRMED` (señales de alta confianza) descalifican duro. Todo lo
  demás (`BLOCKED_BOT_PROTECTION`, `PARKED_LIKELY`, `SUSPICIOUS_RECYCLED`,
  `REDIRECTED_OFFSITE` — las señales de confianza media del paso 2) fuerza
  `NEEDS_REVIEW`, nunca `DISQUALIFIED` en silencio.
- **Ubicación**: normaliza el país (mayúsculas, tildes, "mexico" vs
  "méxico" vs "mx") contra una tabla de alias en el config, y lo compara
  contra la lista de países del ICP + la lista de países "opcionales".
- **Tamaño**: parsea el rango de empleados (`"11-50"`, `"10001+"`) y
  chequea si se **solapa** con el rango del ICP — no si es idéntico. Si el
  dato no se puede interpretar (`"null+"`, vacío), no se asume nada: queda
  en `NEEDS_REVIEW`, nunca se descarta ni se aprueba a ciegas.
- **Industria**: tres niveles.
  - Si el campo `Industry` matchea directo con la lista `core_industries`
    del ICP → pasa, **a menos que** haya una señal negativa (en
    `Industry Tags`, `Description`, `Product and Services`, o el propio
    nombre de la empresa) que contradiga el dato. Si hay conflicto, no se
    asume que el dato de origen tiene razón: va a `NEEDS_REVIEW`.
  - Si matchea con `gray_zone_industries` (ej. "business consulting",
    "venture capital", "human resources services") → nunca pasa solo.
    Siempre `NEEDS_REVIEW`, con una nota de si encontró o no señales de
    SaaS/fintech/software en el texto libre, para que la persona que
    revisa no tenga que leer la descripción de cero.
  - Si no matchea nada pero el texto libre tiene señales fuertes de
    SaaS/fintech/software → también `NEEDS_REVIEW`, nunca pasa solo.

**Caso real encontrado en el dataset de este challenge:** "Cervecería
Modelo" viene tageada por la fuente como `Industry: software development`,
y su `Description` dice literalmente *"is a computer software company"* —
enriquecimiento mal hecho de origen, no un caso ambiguo real. El nombre de
la empresa (que sí entra al chequeo de señales negativas) tiene "cerveceria"
y eso contradice el resto del dato → cae en `NEEDS_REVIEW` con el motivo
explícito. Es el ejemplo que mejor explica por qué el clasificador nunca
resuelve solo un conflicto entre señales: cuando el dato de origen se
contradice a sí mismo, la decisión es de una persona.

El veredicto final por fila:

- **`QUALIFIED`** — pasa los 4 ejes limpio. Va al CSV que se manda a Clay.
- **`NEEDS_REVIEW`** — zona gris de industria, o tamaño sin dato limpio.
  Nunca es un descarte automático de un caso ambiguo.
- **`DISQUALIFIED`** — falla un eje duro: país fuera del ICP, tamaño fuera
  de rango, industria claramente fuera, o sitio no verificado como vivo.

La columna `icp_reason` siempre trae los 4 motivos concatenados, así que
la auditoría se lee sin tener que volver a correr nada.

### Paso 4 — Export para Clay (`export_for_clay.py`)

Toma `step3_classified.csv`, se queda **solo** con las filas `QUALIFIED`
(las `NEEDS_REVIEW` no salen de acá hasta que una persona las mueva a mano —
"filtrar antes de enriquecer" significa que ni siquiera llegan a este
archivo), y mapea las ~40 columnas crudas a solo las que Clay necesita,
según `clay_export.fields` en el config:

```json
{"source": "domain_normalized", "target": "domain"}
```

`source` = columna en el CSV interno. `target` = como se llama del otro
lado. Agregar un campo nuevo al export (por ejemplo, `Founding Year`) es una
línea en el config, no una línea de código.

Cada fila lleva un `row_id` estable (`icp-00001`, `icp-00002`, ...)
asignado una sola vez en el Paso 1 y que viaja sin cambiar por todo el
pipeline — es lo que permite la verificación de Fase 3 ("¿cierran los
números?") sin ambigüedad: nombres de empresa se repiten o tienen variantes
de escritura, un id secuencial no.

También chequea que ninguna fila `QUALIFIED` le falte `domain` o
`company_name` (los dos campos que el paso siguiente — encontrar personas
en Clay — necesita para funcionar) y lo dice explícito si encuentra alguna.

### Paso 5 (Fase 2 del challenge) — Mandarlo a Clay (`send_to_clay.py`)

Toma `clay_export.csv` y hace un `POST` fila por fila al webhook de la
tabla de Clay (source **"Pull in data from a Webhook"**).

```bash
# Prueba en seco — no manda nada, simula y deja el log igual:
python3 scripts/send_to_clay.py --input output/clay_export.csv --config config/icp_config.json --dry-run

# Envío real — con la URL del webhook (de la tabla de Clay ya creada):
python3 scripts/send_to_clay.py --input output/clay_export.csv --config config/icp_config.json --webhook-url https://api.clay.com/webhook/xxxxx
```

Sin URL de webhook (ni en `--webhook-url` ni en la variable de entorno
`CLAY_WEBHOOK_URL`) **corre en `--dry-run` automáticamente** — nunca manda
nada a ningún lado por accidente. Conectar esto a un webhook real es una
decisión que toma una persona, copiando `.env.example` a `.env` y pegando
la URL ahí — **nunca en `config/icp_config.json`**, porque ese archivo se
versiona en git y la URL del webhook es un secreto (cualquiera que la
tenga puede mandar filas falsas a la tabla de Clay o gastar créditos).

Deja `output/clay_send_log.csv` con el resultado de cada fila (`row_id`,
`http_status`, `ok`, `error`) — es la base de la verificación de Fase 3:
contás cuántas filas mandaste con éxito acá, y lo comparás contra cuántas
aparecen de verdad en la tabla de Clay. Si no cierran, este log dice
exactamente qué `row_id` falló y por qué.

#### Automatizarlo (n8n / API), sin tocar la lógica

El pedido de "que esto se mande solo" tiene dos caminos, sin reescribir
nada de lo de arriba:

- **n8n dispara el script tal cual.** Un nodo *Execute Command* (o *SSH*,
  si Claude Code corre en un servidor) llama
  `python3 scripts/run_pipeline.py --input ... --config ...` seguido de
  `python3 scripts/send_to_clay.py --input output/clay_export.csv --config config/icp_config.json`,
  disparado por un *Schedule Trigger* o por un nodo que detecta que llegó
  un CSV nuevo a una carpeta (*Local File Trigger* / *Watch Folder*).
- **n8n reimplementa el POST con sus propios nodos.** Un nodo que lee
  `output/clay_export.csv` (*Read Binary File* → *Spreadsheet File*) +
  *Split in Batches* + *HTTP Request* (POST, un item por fila) hace
  exactamente lo mismo que `send_to_clay.py`, nativo en n8n — útil si
  ZalesMachine prefiere que el envío viva ahí y no en un script.

En ambos casos, `clay_export.csv` es el contrato: mientras esa cabecera de
columnas no cambie, no importa qué dispare el envío.

## Adaptar esto a un cliente nuevo

Todo lo que cambia entre clientes vive en `config/icp_config.json`:

- `company_size` — rango de empleados.
- `locations` — países del ICP, países opcionales, y sus alias de
  normalización.
- `industry.core_industries` / `gray_zone_industries` / `keyword_signals` —
  qué industrias pasan directo, cuáles son zona gris, y qué palabras
  sueltas en el texto libre cuentan como señal a favor o en contra.
- `roles.target_titles` — no lo usa este skill (es a nivel empresa, no
  contacto), pero queda ahí listo para pasarlo a Clay en la fase de
  búsqueda de personas.
- `liveness_check` — timeouts, concurrencia, y las listas de firmas de
  parking/spam.
- `clay_export.fields` — qué columnas salen en `clay_export.csv` y cómo se
  llaman del otro lado.

Nada de esto requiere tocar los `.py`. La URL del webhook de Clay **no**
vive en este archivo — va en `CLAY_WEBHOOK_URL` dentro de `.env` (ver
`.env.example`), porque es un secreto y este config se versiona en git.

## Nota sobre dónde correr esto

El paso 2 (`verify_alive.py`) necesita salir a internet sin restricciones —
cientos de requests HTTP reales a dominios externos. Si se corre desde un
entorno con egress limitado a una allowlist (un sandbox corporativo, un CI
con red restringida), el paso va a fallar o marcar todo como `DEAD` por
bloqueo de red, no por los sitios estar realmente caídos. Correr desde una
terminal con internet normal (la laptop, un servidor propio).
