# svc — Microservicio batch de ISRCs

Servicio FastAPI loopback (127.0.0.1:8600) que procesa batches de
Excel/CSV con ISRCs contra la API de Soundcharts como **jobs de fondo**.

El job-store vive en SQLite (`svc/data/jobs.db`) y sobrevive a reinicios
del propio servicio (los jobs interrumpidos se marcan como `error` al
arrancar, para no dejarlos como zombis).

## Instalación

```bash
cd ~/musicadders-buscador
python -m venv .venv-svc
source .venv-svc/bin/activate
pip install -r svc/requirements.txt
```

## Configuración

```bash
cp svc/.env.example svc/.env
# Editar svc/.env con las credenciales reales
```

Variables obligatorias:
- `SOUNDCHARTS_APP_ID` — app ID de Soundcharts
- `SOUNDCHARTS_API_KEY` — API key de Soundcharts
- `INTERNAL_TOKEN` — shared secret entre Next.js y este servicio

## Arrancar

```bash
# Cargar variables de entorno
export $(cat svc/.env | grep -v '^#' | xargs)

# Arrancar el servicio (SIEMPRE loopback, NUNCA 0.0.0.0)
uvicorn svc.main:app --host 127.0.0.1 --port 8600
```

O con reload en desarrollo:
```bash
uvicorn svc.main:app --host 127.0.0.1 --port 8600 --reload
```

## Endpoints

### GET /health
Liveness probe. Sin token.

```bash
curl http://127.0.0.1:8600/health
# {"status":"ok","service":"svc-buscador","version":"0.1.0"}
```

---

### POST /batch
Crea y arranca un job de procesado batch. Responde 202 inmediatamente.

```bash
curl -s -X POST http://127.0.0.1:8600/batch \
  -H "X-Internal-Token: $INTERNAL_TOKEN" \
  -F "file=@isrcs.xlsx" \
  -F "scope=importantes"
# {"job_id":"uuid4-aqui","total":42}
```

`scope` admite: `importantes` (4 DSPs), `todas` (9 DSPs), o un nombre
de plataforma concreto (`spotify`, `apple-music`, etc.).

---

### GET /batch/{job_id}/status
Estado del job en tiempo real (polling).

```bash
curl -s http://127.0.0.1:8600/batch/<job_id>/status \
  -H "X-Internal-Token: $INTERNAL_TOKEN"
# {"id":"...","estado":"running","total":42,"hechos":15,"calls_used":45,"not_found_count":2,...}
```

`estado` puede ser: `pending` | `running` | `done` | `cancelled` | `error`

---

### GET /batch/{job_id}/result.json
Resumen JSON del resultado (disponible cuando estado=done|cancelled).

```bash
curl -s http://127.0.0.1:8600/batch/<job_id>/result.json \
  -H "X-Internal-Token: $INTERNAL_TOKEN" \
  -o resultado.json
```

Contiene: `meta_count`, `total_playlists`, `not_found`, `meta` (por ISRC).

---

### GET /batch/{job_id}/result.csv
Tabla de playlists en CSV.

```bash
curl -s http://127.0.0.1:8600/batch/<job_id>/result.csv \
  -H "X-Internal-Token: $INTERNAL_TOKEN" \
  -o playlists.csv
```

---

### GET /batch/{job_id}/result.xlsx
Tabla de playlists en Excel.

```bash
curl -s http://127.0.0.1:8600/batch/<job_id>/result.xlsx \
  -H "X-Internal-Token: $INTERNAL_TOKEN" \
  -o playlists.xlsx
```

---

### POST /batch/{job_id}/cancel
Cancela un job en curso.

```bash
curl -s -X POST http://127.0.0.1:8600/batch/<job_id>/cancel \
  -H "X-Internal-Token: $INTERNAL_TOKEN"
# {"ok":true,"job_id":"..."}
```

## Estructura de datos

- `svc/data/jobs.db` — SQLite con el estado de todos los jobs
- `svc/data/results/<job_id>.jsonl` — resultados parciales por ISRC
- `svc/data/results/<job_id>.json` — resumen final
- `svc/data/results/<job_id>.csv` — playlists en CSV
- `svc/data/results/<job_id>.xlsx` — playlists en Excel

## Seguridad

- Solo escucha en `127.0.0.1`. Nunca usar `--host 0.0.0.0`.
- Todos los endpoints (salvo `/health`) requieren `X-Internal-Token`.
- Fail-closed: si `INTERNAL_TOKEN` no está configurado → 503.
