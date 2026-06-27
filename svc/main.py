"""
svc/main.py — Microservicio FastAPI para el procesado batch de ISRCs.

Expone el batch de Excel→Soundcharts como jobs de fondo (no síncrono),
con job-store en SQLite que sobrevive a reinicios del propio servicio.

Endpoints:
  GET  /health                         — liveness probe (sin token)
  POST /batch                          — crea y arranca un job (multipart)
  GET  /batch/{job_id}/status          — estado del job
  GET  /batch/{job_id}/result.json     — resumen JSON (meta + playlists count + not_found)
  GET  /batch/{job_id}/result.csv      — fichero CSV de playlists
  GET  /batch/{job_id}/result.xlsx     — fichero Excel de playlists
  POST /batch/{job_id}/cancel          — cancela el job

Control de acceso:
  Header X-Internal-Token (variable INTERNAL_TOKEN).
  Fail-closed: 503 si no está configurado, 401 si no coincide.
  /health no requiere token.

CRÍTICO — NUNCA usar --host 0.0.0.0:
  El servicio es loopback-only (127.0.0.1). Exponer en 0.0.0.0 permitiría
  a cualquier proceso de la red solicitar jobs con credenciales de Soundcharts.

Arrancar:
  uvicorn svc.main:app --host 127.0.0.1 --port 8600
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import Annotated

# Patrón de job_id válido: UUID4 canónico (8-4-4-4-12 hex, separados por guiones)
_JOB_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# Patrón ISRC: 2 letras de país + 3 alfanuméricos + 7 dígitos (RFC 3901)
_ISRC_RE = re.compile(r"^[A-Za-z]{2}[A-Za-z0-9]{3}\d{7}$")


# ── Contador diario de llamadas Soundcharts (paridad con app.py Streamlit) ────
#
# Contador en proceso, thread-safe.  Reseteado automáticamente al cambiar el
# día (comparando la fecha ISO).  Expuesto en /health; usado en /search para
# devolver 429 propio si se supera SOUNDCHARTS_MAX_PER_DAY.

_daily_lock = threading.Lock()
_daily_state: dict[str, object] = {"date": None, "calls": 0}


def _today_iso() -> str:
    return date.today().isoformat()


def _accum_calls(n: int) -> int:
    """Acumula n llamadas al contador diario. Resetea si cambia el día. Devuelve el total."""
    today = _today_iso()
    with _daily_lock:
        if _daily_state["date"] != today:
            _daily_state["date"] = today
            _daily_state["calls"] = 0
        _daily_state["calls"] = int(_daily_state["calls"]) + n  # type: ignore[arg-type]
        return int(_daily_state["calls"])


def _daily_snapshot() -> tuple[int, str]:
    """Devuelve (total_hoy, fecha_iso) sin modificar el contador."""
    with _daily_lock:
        return int(_daily_state["calls"]), str(_daily_state["date"] or _today_iso())


def _validate_job_id(job_id: str) -> None:
    """Valida el formato del job_id antes de tocar DB o FS.

    Defensa en profundidad: evita path traversal y consultas innecesarias.
    """
    if not _JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=400, detail="job_id con formato inválido.")

logger = logging.getLogger(__name__)

# ── Plataformas soportadas ─────────────────────────────────────────────────────

_PLATFORMS_DEFAULT = ["spotify", "apple-music", "amazon", "deezer"]
_PLATFORMS_ALL = _PLATFORMS_DEFAULT + ["youtube", "soundcloud", "tidal", "audiomack", "pandora"]

_SCOPE_MAP = {
    "importantes": _PLATFORMS_DEFAULT,
    "todas": _PLATFORMS_ALL,
}


def _platforms_for_scope(scope: str) -> list[str]:
    """Convierte el scope de texto en lista de plataformas."""
    s = (scope or "importantes").lower().strip()
    if s in _SCOPE_MAP:
        return _SCOPE_MAP[s]
    # Aceptar también un nombre de plataforma concreto
    if s in _PLATFORMS_ALL:
        return [s]
    return _PLATFORMS_DEFAULT


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Startup / shutdown del servicio.

    Al apagar, espera a que el pool de workers termine los jobs en curso
    (shutdown graceful). Timeout implícito del sistema operativo.
    """
    # Startup: limpiar jobs FUGA antiguos para evitar crecimiento ilimitado de svc/data/fuga_results/
    from svc import fuga_jobs as _fuga_jobs_startup
    try:
        n = _fuga_jobs_startup.cleanup_old_jobs(max_age_days=30)
        if n:
            logger.info("svc: cleanup eliminó %d jobs FUGA antiguos al arrancar.", n)
    except Exception as exc:
        logger.warning("svc: error en cleanup de jobs FUGA al arrancar: %s", exc)
    # Startup: limpiar jobs Spotify antiguos (retención 7 días)
    from svc import spotify_jobs as _sp_jobs_startup
    try:
        n = _sp_jobs_startup.cleanup_old_jobs(max_age_days=7)
        if n:
            logger.info("svc: cleanup eliminó %d jobs Spotify antiguos al arrancar.", n)
    except Exception as exc:
        logger.warning("svc: error en cleanup de jobs Spotify al arrancar: %s", exc)
    yield
    # Shutdown: cancel_futures=True, wait=False (no espera llamadas HTTP largas)
    from svc import jobs
    from svc import fuga_jobs
    from svc import spotify_jobs
    try:
        jobs.shutdown_pool()
        logger.info("svc: pool de workers batch cerrado correctamente.")
    except Exception as exc:
        logger.warning("svc: error al cerrar el pool de workers batch: %s", exc)
    try:
        fuga_jobs.shutdown_pool()
        logger.info("svc: pool de workers FUGA cerrado correctamente.")
    except Exception as exc:
        logger.warning("svc: error al cerrar el pool de workers FUGA: %s", exc)
    try:
        spotify_jobs.shutdown_pool()
        logger.info("svc: pool de workers Spotify cerrado correctamente.")
    except Exception as exc:
        logger.warning("svc: error al cerrar el pool de workers Spotify: %s", exc)


# ── App ────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Musicadders Buscador — svc",
    description="Microservicio Python para el procesado batch de ISRCs via Soundcharts.",
    version="0.1.0",
    lifespan=_lifespan,
)

# CORS: solo el servidor Next.js (mismo host en producción).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ── Auth ───────────────────────────────────────────────────────────────────────

def _configured_token() -> str | None:
    """Devuelve el token configurado o None si no está en el entorno."""
    return os.environ.get("INTERNAL_TOKEN", "").strip() or None


def _check_token(x_internal_token: str | None) -> None:
    """Valida el header X-Internal-Token.

    - Token no configurado → 503 (fail-closed).
    - Header ausente o no coincide → 401.
    """
    configured = _configured_token()
    if configured is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "svc no tiene INTERNAL_TOKEN configurado. "
                "Define la variable de entorno antes de arrancar el servicio."
            ),
        )
    if not x_internal_token or x_internal_token != configured:
        raise HTTPException(status_code=401, detail="X-Internal-Token inválido o ausente.")


# ── Endpoints ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """Liveness probe — sin autenticación. Incluye contador de llamadas Soundcharts del día."""
    calls_today, calls_date = _daily_snapshot()
    max_per_day = int(os.environ.get("SOUNDCHARTS_MAX_PER_DAY", "5000"))
    return {
        "status": "ok",
        "service": "svc-buscador",
        "version": "0.1.0",
        "calls_today": calls_today,
        "calls_date": calls_date,
        "calls_limit": max_per_day,
    }


@app.post("/batch", status_code=202)
async def crear_batch(
    file: UploadFile = File(..., description="Fichero Excel (.xlsx) o CSV con columna ISRC"),
    scope: str = Form(default="importantes", description="'importantes', 'todas' o nombre de plataforma"),
    x_internal_token: str | None = Header(default=None),
):
    """Crea un job de procesado batch y lo arranca en background.

    Recibe un fichero Excel/CSV con columna ISRC (multipart/form-data).
    Devuelve 202 con {job_id, total} para que el cliente haga polling en /status.

    El job se encola en el ThreadPoolExecutor: la respuesta es inmediata,
    el procesado ocurre en background y persiste en SQLite.
    """
    _check_token(x_internal_token)

    from svc.soundcharts import parse_isrcs_from_excel
    from svc import jobs

    # Leer fichero en memoria (bytes)
    try:
        file_bytes = await file.read()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Error al leer el fichero: {exc}")

    if not file_bytes:
        raise HTTPException(status_code=400, detail="El fichero está vacío.")

    # Parsear ISRCs
    try:
        isrcs = parse_isrcs_from_excel(file_bytes, filename=file.filename or "")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"No se pudo leer el fichero: {exc}")

    if not isrcs:
        raise HTTPException(
            status_code=422,
            detail="No se encontraron ISRCs válidos en el fichero.",
        )

    platforms = _platforms_for_scope(scope)

    # Crear y arrancar el job
    job_id = jobs.create_job(isrcs=isrcs, platforms=platforms, scope=scope)
    jobs.start_job(job_id)

    logger.info(
        "svc: batch creado job_id=%s, total=%d ISRCs, scope=%s.",
        job_id, len(isrcs), scope,
    )
    return {"job_id": job_id, "total": len(isrcs)}


@app.get("/batch/{job_id}/status")
async def get_batch_status(
    job_id: str,
    x_internal_token: str | None = Header(default=None),
):
    """Estado actual del job.

    Devuelve: {estado, hechos, total, calls_used, not_found_count}

    estados: pending | running | done | cancelled | error
    """
    _validate_job_id(job_id)
    _check_token(x_internal_token)

    from svc import jobs

    status = jobs.get_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="Job no encontrado.")
    return status


@app.get("/batch/{job_id}/result.json")
async def get_batch_result_json(
    job_id: str,
    x_internal_token: str | None = Header(default=None),
):
    """Resumen JSON del resultado del job.

    Disponible cuando el job está en estado 'done', 'cancelled' o 'error'
    (resultado parcial si el fichero existe). Incluye `playlists` enriquecidas
    para que el front (BatchResults) pueda renderizar la tabla directamente.
    """
    _validate_job_id(job_id)
    _check_token(x_internal_token)

    from svc import jobs

    path = jobs.get_result_path(job_id)
    if path is None:
        status = jobs.get_status(job_id)
        if not status:
            raise HTTPException(status_code=404, detail="Job no encontrado.")
        raise HTTPException(
            status_code=409,
            detail=f"El resultado aún no está disponible (estado: {status['estado']}).",
        )
    return FileResponse(
        path=str(path),
        media_type="application/json",
        filename=f"resultado_{job_id}.json",
    )


@app.get("/batch/{job_id}/result.csv")
async def get_batch_result_csv(
    job_id: str,
    x_internal_token: str | None = Header(default=None),
):
    """Fichero CSV con todas las playlists del batch.

    Disponible cuando el job está en estado 'done', 'cancelled' o 'error'
    (resultado parcial si el fichero existe).
    """
    _validate_job_id(job_id)
    _check_token(x_internal_token)

    from svc import jobs

    path = jobs.get_csv_path(job_id)
    if path is None:
        status = jobs.get_status(job_id)
        if not status:
            raise HTTPException(status_code=404, detail="Job no encontrado.")
        raise HTTPException(
            status_code=409,
            detail=f"El resultado aún no está disponible (estado: {status['estado']}).",
        )
    return FileResponse(
        path=str(path),
        media_type="text/csv",
        filename=f"playlists_{job_id}.csv",
    )


@app.get("/batch/{job_id}/result.xlsx")
async def get_batch_result_xlsx(
    job_id: str,
    x_internal_token: str | None = Header(default=None),
):
    """Fichero Excel con todas las playlists del batch.

    Disponible cuando el job está en estado 'done', 'cancelled' o 'error'
    (resultado parcial si el fichero existe).
    """
    _validate_job_id(job_id)
    _check_token(x_internal_token)

    from svc import jobs

    path = jobs.get_xlsx_path(job_id)
    if path is None:
        status = jobs.get_status(job_id)
        if not status:
            raise HTTPException(status_code=404, detail="Job no encontrado.")
        raise HTTPException(
            status_code=409,
            detail=f"El resultado aún no está disponible (estado: {status['estado']}).",
        )
    return FileResponse(
        path=str(path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"playlists_{job_id}.xlsx",
    )


@app.post("/batch/{job_id}/cancel")
async def cancel_batch(
    job_id: str,
    x_internal_token: str | None = Header(default=None),
):
    """Cancela un job en curso.

    Si el job está en 'pending' o 'running', activa el flag de cancelación.
    El worker termina limpiamente al final del ISRC actual.
    Si el job ya está terminado, devuelve 409.
    """
    _validate_job_id(job_id)
    _check_token(x_internal_token)

    from svc import jobs

    ok = jobs.cancel_job(job_id)
    if not ok:
        status = jobs.get_status(job_id)
        if not status:
            raise HTTPException(status_code=404, detail="Job no encontrado.")
        raise HTTPException(
            status_code=409,
            detail=f"El job no se puede cancelar (estado: {status['estado']}).",
        )
    return {"ok": True, "job_id": job_id}


# ── FUGA — modelo de cuerpo JSON ──────────────────────────────────────────────

class _FugaJobBody(BaseModel):
    date_from: str
    date_to:   str


# ── FUGA endpoints ─────────────────────────────────────────────────────────────

@app.post("/fuga", status_code=202)
async def crear_fuga(
    body: _FugaJobBody,
    x_internal_token: str | None = Header(default=None),
):
    """Crea un job de búsqueda de catálogo FUGA y lo arranca en background.

    Cuerpo JSON: {date_from: "YYYY-MM-DD", date_to: "YYYY-MM-DD"}
    Respuesta 202: {job_id}

    Fail-closed en credenciales FUGA: 401 con {error: "no_credentials"} si
    FUGA_USER / FUGA_PASS no están en el entorno.
    """
    _check_token(x_internal_token)

    from svc import fuga as _fuga
    from svc import fuga_jobs

    # Validar credenciales FUGA antes de crear el job
    if not _fuga.has_credentials():
        return JSONResponse(
            status_code=401,
            content={
                "error":   "no_credentials",
                "message": (
                    "FUGA_USER y FUGA_PASS no están configurados en el servidor. "
                    "Define las variables de entorno antes de usar esta función."
                ),
            },
        )

    # Validar formato y rango de fechas
    try:
        d_from = date.fromisoformat(body.date_from)
        d_to   = date.fromisoformat(body.date_to)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="Formato de fecha inválido. Usa YYYY-MM-DD.",
        )

    if d_from > d_to:
        raise HTTPException(
            status_code=422,
            detail="date_from no puede ser posterior a date_to.",
        )

    if (d_to - d_from).days > 366:
        raise HTTPException(
            status_code=422,
            detail=(
                "El rango de fechas no puede superar 366 días. "
                "Divide la búsqueda en rangos más cortos si necesitas cubrir más de un año."
            ),
        )

    job_id = fuga_jobs.create_job(body.date_from, body.date_to)
    fuga_jobs.start_job(job_id)

    logger.info(
        "svc: FUGA job creado job_id=%s (%s → %s).",
        job_id, body.date_from, body.date_to,
    )
    return {"job_id": job_id}


@app.get("/fuga/{job_id}/status")
async def get_fuga_status(
    job_id: str,
    x_internal_token: str | None = Header(default=None),
):
    """Estado actual del job FUGA.

    Devuelve: {estado, pages_done, pages_total, status_text,
               isrcs_found, releases_found, error_msg?}

    estados: running | done | cancelled | error
    """
    _validate_job_id(job_id)
    _check_token(x_internal_token)

    from svc import fuga_jobs

    status = fuga_jobs.get_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="Job no encontrado.")
    return status


@app.get("/fuga/{job_id}/result.json")
async def get_fuga_result_json(
    job_id: str,
    x_internal_token: str | None = Header(default=None),
):
    """Resultado JSON del job FUGA.

    Devuelve: {rows, date_from, date_to, isrcs_total, releases_total}
    donde rows = [{isrc, product_name, artist_name, label, release_date}]

    Disponible cuando el job está en done, cancelled o error (resultado parcial).
    """
    _validate_job_id(job_id)
    _check_token(x_internal_token)

    from svc import fuga_jobs

    paths = fuga_jobs.get_result_paths(job_id)
    if paths is None:
        status = fuga_jobs.get_status(job_id)
        if not status:
            raise HTTPException(status_code=404, detail="Job no encontrado.")
        raise HTTPException(
            status_code=409,
            detail=f"Resultado no disponible aún (estado: {status['estado']}).",
        )
    if paths["json"] is None:
        raise HTTPException(status_code=404, detail="Fichero de resultado no encontrado.")
    return FileResponse(
        path=str(paths["json"]),
        media_type="application/json",
        filename=f"fuga_resultado_{job_id}.json",
    )


@app.get("/fuga/{job_id}/result.csv")
async def get_fuga_result_csv(
    job_id: str,
    x_internal_token: str | None = Header(default=None),
):
    """CSV completo del resultado FUGA.

    Columnas: isrc, product_name, artist_name, label, release_date.
    Disponible cuando el job está en done, cancelled o error.
    """
    _validate_job_id(job_id)
    _check_token(x_internal_token)

    from svc import fuga_jobs

    paths = fuga_jobs.get_result_paths(job_id)
    if paths is None:
        status = fuga_jobs.get_status(job_id)
        if not status:
            raise HTTPException(status_code=404, detail="Job no encontrado.")
        raise HTTPException(
            status_code=409,
            detail=f"Resultado no disponible aún (estado: {status['estado']}).",
        )
    if paths["csv"] is None:
        raise HTTPException(status_code=404, detail="Fichero CSV no encontrado.")
    return FileResponse(
        path=str(paths["csv"]),
        media_type="text/csv",
        filename=f"fuga_isrcs_{job_id}.csv",
    )


@app.get("/fuga/{job_id}/result.xlsx")
async def get_fuga_result_xlsx(
    job_id: str,
    xlsx_type: str = Query(default="full", description="'full' (todas las columnas) o 'isrc' (solo ISRC)"),
    x_internal_token: str | None = Header(default=None),
):
    """Excel del resultado FUGA.

    ?xlsx_type=full  → todas las columnas (isrc, product_name, artist_name, label, release_date)
    ?xlsx_type=isrc  → solo columna ISRC (lista compacta para carga masiva)

    Disponible cuando el job está en done, cancelled o error.
    """
    _validate_job_id(job_id)
    _check_token(x_internal_token)

    if xlsx_type not in ("full", "isrc"):
        raise HTTPException(
            status_code=400,
            detail="Parámetro 'xlsx_type' debe ser 'full' o 'isrc'.",
        )

    from svc import fuga_jobs

    paths = fuga_jobs.get_result_paths(job_id)
    if paths is None:
        status = fuga_jobs.get_status(job_id)
        if not status:
            raise HTTPException(status_code=404, detail="Job no encontrado.")
        raise HTTPException(
            status_code=409,
            detail=f"Resultado no disponible aún (estado: {status['estado']}).",
        )

    xlsx_key = "xlsx_full" if xlsx_type == "full" else "xlsx_isrc"
    xlsx_path = paths.get(xlsx_key)
    if xlsx_path is None:
        raise HTTPException(status_code=404, detail="Fichero Excel no encontrado.")

    suffix = "" if xlsx_type == "full" else "_solo_isrc"
    return FileResponse(
        path=str(xlsx_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"fuga_isrcs{suffix}_{job_id}.xlsx",
    )


@app.post("/fuga/{job_id}/cancel")
async def cancel_fuga(
    job_id: str,
    x_internal_token: str | None = Header(default=None),
):
    """Cancela un job FUGA en curso.

    Activa el flag de cancelación: el worker para entre páginas y materializa
    el resultado parcial acumulado hasta ese momento.
    """
    _validate_job_id(job_id)
    _check_token(x_internal_token)

    from svc import fuga_jobs

    ok = fuga_jobs.cancel_job(job_id)
    if not ok:
        status = fuga_jobs.get_status(job_id)
        if not status:
            raise HTTPException(status_code=404, detail="Job no encontrado.")
        raise HTTPException(
            status_code=409,
            detail=f"El job no se puede cancelar (estado: {status['estado']}).",
        )
    return {"ok": True, "job_id": job_id}


@app.get("/search")
def search_single(
    isrc: str = Query(..., description="Código ISRC del track (12 chars)"),
    scope: str = Query(
        default="importantes",
        description="'importantes', 'todas' o nombre de plataforma individual",
    ),
    bust: str = Query(
        default="",
        description="Cache-buster opcional; si cambia fuerza re-fetch a Soundcharts",
    ),
    x_internal_token: str | None = Header(default=None),
):
    """Búsqueda síncrona de un único ISRC en Soundcharts.

    Declarado como `def` (no async) para que FastAPI lo ejecute en su threadpool
    y la llamada bloqueante a Soundcharts (requests) no congele el event loop.

    Contrato de respuesta (200 OK):
      {
        meta: {uuid, song_name, credit_name, release_date} | null,
        playlists: [{platform, playlist_name, playlist_type,
                     subscriber_count, position, ...}],
        calls_used: int,
        elapsed_ms: int,
        platforms_count: int,   // DSPs con ≥1 resultado
        total_platforms: int,   // DSPs consultadas según el scope
      }

    meta=null si el ISRC no existe en Soundcharts (sigue siendo 200, no 404).
    429 de Soundcharts  → 429 con {error: "rate_limited", message: str}.
    429 de cuota diaria → 429 con {error: "rate_limit_daily", message: str}.
    503 si credenciales Soundcharts no configuradas.
    """
    _check_token(x_internal_token)

    # Guardia de cuota diaria (paridad con Streamlit app.py)
    max_per_day = int(os.environ.get("SOUNDCHARTS_MAX_PER_DAY", "5000"))
    cur_calls, _ = _daily_snapshot()
    if cur_calls >= max_per_day:
        return JSONResponse(
            status_code=429,
            content={
                "error": "rate_limit_daily",
                "message": (
                    f"Límite diario de {max_per_day} llamadas a Soundcharts alcanzado. "
                    "El contador se resetea a medianoche."
                ),
            },
        )

    # Normalizar y validar el ISRC
    isrc_norm = isrc.strip().upper()
    if not _ISRC_RE.match(isrc_norm):
        raise HTTPException(
            status_code=422,
            detail=(
                f"ISRC '{isrc}' inválido. Formato esperado: "
                "2 letras de país + 3 alfanuméricos + 7 dígitos (ej. ES14H2600001)."
            ),
        )

    platforms = _platforms_for_scope(scope)

    from svc import soundcharts as _sc
    from svc.soundcharts import SoundchartsRateLimitError

    t0 = time.time()
    try:
        result = _sc.search_isrc(isrc_norm, platforms, buster=bust)
    except SoundchartsRateLimitError:
        return JSONResponse(
            status_code=429,
            content={
                "error": "rate_limited",
                "message": (
                    "Soundcharts ha devuelto 429 (rate limit). "
                    "Espera unos segundos y reintenta."
                ),
            },
        )
    except EnvironmentError as exc:
        # _sc_headers() lanza EnvironmentError si faltan SOUNDCHARTS_APP_ID/API_KEY
        raise HTTPException(
            status_code=503,
            detail=f"Credenciales Soundcharts no configuradas: {exc}",
        )
    except RuntimeError as exc:
        # RuntimeError genérico inesperado del cliente → 502
        raise HTTPException(
            status_code=502,
            detail=f"Error al consultar Soundcharts: {exc}",
        )

    elapsed_ms = int((time.time() - t0) * 1000)
    playlists = result.get("playlists") or []
    platforms_count = len({p["platform"] for p in playlists})

    # Acumular en el contador diario DESPUÉS de la llamada exitosa
    _accum_calls(result.get("calls_used", 0))

    logger.info(
        "svc: /search isrc=%s scope=%s platforms=%d results=%d elapsed_ms=%d",
        isrc_norm, scope, len(platforms), len(playlists), elapsed_ms,
    )

    return JSONResponse(content={
        "meta": result.get("meta"),
        "playlists": playlists,
        "calls_used": result.get("calls_used", 0),
        "elapsed_ms": elapsed_ms,
        "platforms_count": platforms_count,
        "total_platforms": len(platforms),
    })


# ── Spotify — modelos de cuerpo JSON ──────────────────────────────────────────

_SP_MAX_QUEUED = 5  # máximo de jobs en cola simultáneos (pending + running)


class _PlaylistJobBody(BaseModel):
    isrcs:       Annotated[list[str], Field(max_length=10_000)]
    name:        Annotated[str,       Field(max_length=200)]
    description: Annotated[str,       Field("", max_length=300)] = ""
    public:      bool = False


class _SetupExchangeBody(BaseModel):
    code:         str
    state:        str
    redirect_uri: str


# ── Helpers de admin para endpoints de setup ──────────────────────────────────

def _check_admin(x_user_email: str | None) -> None:
    """Valida que el email sea admin de Spotify (defensa en profundidad).

    - SPOTIFY_CENTRAL_ADMINS no configurado → 403 fail-closed.
    - Email ausente o no en la lista → 403.
    """
    admins_raw = os.environ.get("SPOTIFY_CENTRAL_ADMINS", "").strip()
    if not admins_raw:
        raise HTTPException(
            status_code=403,
            detail=(
                "SPOTIFY_CENTRAL_ADMINS no configurado en el servidor. "
                "El setup de la cuenta Spotify está deshabilitado."
            ),
        )
    admins = {a.strip().lower() for a in admins_raw.split(",") if a.strip()}
    email  = (x_user_email or "").strip().lower()
    if not email or email not in admins:
        raise HTTPException(
            status_code=403,
            detail="Acceso restringido a administradores.",
        )


# ── Spotify setup endpoints ────────────────────────────────────────────────────
# IMPORTANTE: registrar ANTES de los endpoints /{job_id}/* para evitar ambigüedad
# entre /playlist/setup/... y /playlist/{job_id}/...

@app.get("/playlist/setup/status")
async def get_playlist_setup_status(
    x_internal_token: str | None = Header(default=None),
    x_user_email:     str | None = Header(default=None),
):
    """Estado de la cuenta Spotify central.

    Solo accesible por admins (doble capa: Next proxy + svc).
    Devuelve: {connected, account_name, expires_at}
    """
    _check_token(x_internal_token)
    _check_admin(x_user_email)

    from svc import spotify as _sp
    return _sp.get_setup_status()


@app.get("/playlist/setup/connect")
async def get_playlist_setup_connect(
    x_internal_token: str | None = Header(default=None),
    x_user_email:     str | None = Header(default=None),
):
    """Genera la URL de autorización OAuth de Spotify para el admin.

    El estado HMAC incluye el email del admin para verificación en /exchange.
    Devuelve: {login_url}

    GATE de despliegue: la redirect_uri (APP_BASE_URL + /api/playlist/setup/callback)
    DEBE estar registrada en developer.spotify.com antes de usar este endpoint.
    """
    _check_token(x_internal_token)
    _check_admin(x_user_email)

    app_base_url = os.environ.get("APP_BASE_URL", "").rstrip("/")
    redirect_uri = f"{app_base_url}/api/playlist/setup/callback"

    from svc import spotify as _sp
    login_url = _sp.generate_login_url(x_user_email or "", redirect_uri)
    if login_url is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "SPOTIFY_CLIENT_ID o SPOTIFY_CLIENT_SECRET no configurados. "
                "Define las variables de entorno antes de usar el setup."
            ),
        )
    return {"login_url": login_url}


@app.post("/playlist/setup/exchange")
async def playlist_setup_exchange(
    body: _SetupExchangeBody,
    x_internal_token: str | None = Header(default=None),
):
    """Intercambia el code OAuth por tokens y guarda el refresh_token central.

    El state HMAC incluye el email del admin:
      - Si la firma no es válida → 400 invalid_state.
      - Si el email no es admin → 403.
      - Si el ID de usuario no coincide con SPOTIFY_CENTRAL_EXPECTED_USER_ID → 400.

    No requiere X-User-Email: la identidad del admin está en el state firmado.
    """
    _check_token(x_internal_token)

    from svc import spotify as _sp
    result = _sp.exchange_code(body.code, body.state, body.redirect_uri)

    if result.get("error") == "invalid_state":
        raise HTTPException(status_code=400, detail="State OAuth inválido o caducado.")
    if result.get("error") == "not_admin":
        raise HTTPException(status_code=403, detail="El email del state no tiene permisos de admin.")
    if result.get("error") == "not_configured":
        raise HTTPException(status_code=503, detail="Spotify CLIENT_ID/SECRET no configurados.")
    if result.get("error") == "expected_user_id_not_configured":
        raise HTTPException(
            status_code=503,
            detail=(
                "SPOTIFY_CENTRAL_EXPECTED_USER_ID no está configurado en el servidor. "
                "Es obligatorio para el setup (fail-closed). "
                "Añade la variable de entorno antes de continuar."
            ),
        )
    if result.get("error") == "account_mismatch":
        raise HTTPException(
            status_code=400,
            detail=(
                "La cuenta autorizada no coincide con SPOTIFY_CENTRAL_EXPECTED_USER_ID. "
                "Verifica que estás conectando la cuenta correcta."
            ),
        )
    if result.get("error"):
        raise HTTPException(status_code=502, detail=f"Error en el intercambio OAuth: {result['error']}")

    return result


@app.post("/playlist/setup/disconnect")
async def playlist_setup_disconnect(
    x_internal_token: str | None = Header(default=None),
    x_user_email:     str | None = Header(default=None),
):
    """Elimina el token central y limpia la caché de acceso.

    Solo accesible por admins.
    """
    _check_token(x_internal_token)
    _check_admin(x_user_email)

    from svc import spotify as _sp
    _sp.delete_central_token()
    return {"ok": True}


# ── Spotify job endpoints ──────────────────────────────────────────────────────

@app.post("/playlist", status_code=202)
async def crear_playlist(
    body: _PlaylistJobBody,
    x_internal_token: str | None = Header(default=None),
):
    """Crea un job de creación de playlist Spotify y lo arranca en background.

    Fail-closed: si no hay cuenta central configurada → 401 not_configured.
    Valida que isrcs no esté vacío y name no esté vacío → 422.

    Devuelve 202 con {job_id, total}.
    """
    _check_token(x_internal_token)

    from svc import spotify as _sp
    from svc import spotify_jobs

    # Verificar cuenta central configurada
    if not _sp.has_central_token():
        return JSONResponse(
            status_code=401,
            content={
                "error":   "not_configured",
                "message": (
                    "No hay cuenta Spotify central configurada. "
                    "El admin debe conectar una cuenta en /playlist/setup."
                ),
            },
        )

    # Verificar límite de cola (evitar abuso y OOM)
    active = spotify_jobs.count_active_jobs()
    if active >= _SP_MAX_QUEUED:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Cola llena: hay {active} job(s) en curso. "
                f"Máximo {_SP_MAX_QUEUED} simultáneos. "
                "Espera a que alguno termine antes de crear otro."
            ),
        )

    # Validar y normalizar ISRCs
    isrcs = [i.strip().upper() for i in (body.isrcs or []) if i.strip()]
    valid_isrcs = [i for i in isrcs if _ISRC_RE.match(i)]

    if not valid_isrcs:
        raise HTTPException(
            status_code=422,
            detail="El listado de ISRCs está vacío o no contiene ISRCs válidos.",
        )

    name = (body.name or "").strip()
    if not name:
        raise HTTPException(
            status_code=422,
            detail="El nombre de la playlist no puede estar vacío.",
        )

    job_id = spotify_jobs.create_job(
        valid_isrcs,
        name,
        (body.description or "").strip(),
        bool(body.public),
    )
    spotify_jobs.start_job(job_id)

    logger.info(
        "svc: Spotify playlist job creado job_id=%s, total=%d ISRCs.",
        job_id, len(valid_isrcs),
    )
    return {"job_id": job_id, "total": len(valid_isrcs)}


@app.get("/playlist/{job_id}/status")
async def get_playlist_status(
    job_id: str,
    x_internal_token: str | None = Header(default=None),
):
    """Estado actual del job de playlist.

    Calcula el estado efectivo: si el job está en 'running' y cooldown_until
    es futuro, expone estado 'cooldown' para que el frontend haga polling lento.

    Contrato:
      {estado, phase, resolved, total, added, not_found, progress_pct,
       status_text, cooldown_until, error_msg}
    """
    _validate_job_id(job_id)
    _check_token(x_internal_token)

    from svc import spotify_jobs

    status = spotify_jobs.get_status(job_id)
    if not status:
        raise HTTPException(status_code=404, detail="Job no encontrado.")

    # Calcular estado efectivo: 'cooldown' si hay penalty-box activo
    effective_estado  = status["estado"]
    cooldown_until    = status["cooldown_until"]
    if effective_estado == "running" and cooldown_until:
        try:
            cd = datetime.fromisoformat(cooldown_until)
            if cd > datetime.now(timezone.utc):
                effective_estado = "cooldown"
        except Exception:
            pass

    return {
        "estado":         effective_estado,
        "phase":          status["phase"],
        "resolved":       status["resolved"],
        "total":          status["total"],
        "added":          status["added"],
        "not_found":      status["not_found_count"],
        "progress_pct":   status["progress_pct"],
        "status_text":    status["status_text"],
        "cooldown_until": cooldown_until,
        "error_msg":      status["error_msg"],
    }


@app.get("/playlist/{job_id}/result.json")
async def get_playlist_result_json(
    job_id: str,
    x_internal_token: str | None = Header(default=None),
):
    """Resultado JSON de la playlist creada.

    Disponible cuando estado ∈ {done, cancelled, error}.
    Devuelve: {playlist_url, playlist_name, tracks_added, not_found_isrcs,
               total_isrcs, errors_count}
      - errors_count: ISRCs que no se pudieron resolver + lotes de add que fallaron.
    """
    _validate_job_id(job_id)
    _check_token(x_internal_token)

    from svc import spotify_jobs

    path = spotify_jobs.get_result_path(job_id)
    if path is None:
        status = spotify_jobs.get_status(job_id)
        if not status:
            raise HTTPException(status_code=404, detail="Job no encontrado.")
        if status["estado"] in ("running", "pending"):
            raise HTTPException(
                status_code=409,
                detail=f"Resultado no disponible aún (estado: {status['estado']}).",
            )
        # Job terminal pero sin fichero (cancelado antes de materializarse): devolver vacío
        return JSONResponse({
            "playlist_url":    "",
            "playlist_name":   "",
            "tracks_added":    0,
            "not_found_isrcs": [],
            "total_isrcs":     status.get("total", 0),
            "errors_count":    0,
        })
    return FileResponse(
        path=str(path),
        media_type="application/json",
        filename=f"playlist_{job_id}.json",
    )


@app.get("/playlist/{job_id}/result/not_found.csv")
async def get_playlist_result_not_found_csv(
    job_id: str,
    x_internal_token: str | None = Header(default=None),
):
    """CSV con los ISRCs no encontrados en Spotify.

    Columna: ISRC (una por fila).
    Disponible cuando estado ∈ {done, cancelled, error}.
    """
    _validate_job_id(job_id)
    _check_token(x_internal_token)

    from svc import spotify_jobs

    path = spotify_jobs.get_not_found_csv_path(job_id)
    if path is None:
        status = spotify_jobs.get_status(job_id)
        if not status:
            raise HTTPException(status_code=404, detail="Job no encontrado.")
        if status["estado"] in ("running", "pending"):
            raise HTTPException(
                status_code=409,
                detail=f"Resultado no disponible aún (estado: {status['estado']}).",
            )
        # Job terminal pero sin fichero (cancelado antes de materializarse): CSV vacío
        from fastapi.responses import Response as _Response
        return _Response(
            content="ISRC\n",
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="isrcs_no_encontrados_{job_id}.csv"',
            },
        )
    return FileResponse(
        path=str(path),
        media_type="text/csv",
        filename=f"isrcs_no_encontrados_{job_id}.csv",
    )


@app.post("/playlist/{job_id}/cancel")
async def cancel_playlist(
    job_id: str,
    x_internal_token: str | None = Header(default=None),
):
    """Cancela un job de playlist en curso.

    El worker para limpiamente al final de la operación en curso (rate-limit slot,
    lote de tracks, o cooldown interruptible).
    """
    _validate_job_id(job_id)
    _check_token(x_internal_token)

    from svc import spotify_jobs

    ok = spotify_jobs.cancel_job(job_id)
    if not ok:
        status = spotify_jobs.get_status(job_id)
        if not status:
            raise HTTPException(status_code=404, detail="Job no encontrado.")
        raise HTTPException(
            status_code=409,
            detail=f"El job no se puede cancelar (estado: {status['estado']}).",
        )
    return {"ok": True, "job_id": job_id}
