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
from datetime import date
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

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
    # Startup: nada especial (DB y pool se inicializan al importar jobs.py)
    yield
    # Shutdown: cancel_futures=True, wait=False (no espera llamadas HTTP largas)
    from svc import jobs
    try:
        jobs.shutdown_pool()
        logger.info("svc: pool de workers cerrado correctamente.")
    except Exception as exc:
        logger.warning("svc: error al cerrar el pool de workers: %s", exc)


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
