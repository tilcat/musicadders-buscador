"""
svc/fuga_jobs.py — Job-store para búsquedas de catálogo FUGA.

Esquema SQLite (svc/data/fuga_jobs.db):
  fuga_jobs(
    id             TEXT PRIMARY KEY,
    estado         TEXT,    -- pending | running | done | cancelled | error
    pages_done     INT,     -- páginas completadas (actualizado por progress_cb)
    pages_total    INT,     -- heurística = 80 al crear; barra determinada desde el inicio
    status_text    TEXT,    -- texto descriptivo del progreso ("página 3 · 15 en rango")
    isrcs_found    INT,     -- ISRCs únicos en el rango (0 durante progreso, final al terminar)
    releases_found INT,     -- releases en rango (actualizado por progress_cb)
    date_from      TEXT,    -- YYYY-MM-DD
    date_to        TEXT,    -- YYYY-MM-DD
    created_at     TEXT,    -- ISO-8601 UTC
    error_msg      TEXT     -- NULL si ok; mensaje descriptivo si error
  )

Resultados en svc/data/fuga_results/:
  <job_id>.json       — {rows, date_from, date_to, isrcs_total, releases_total}
  <job_id>.csv        — columnas: isrc, product_name, artist_name, label, release_date
  <job_id>.xlsx       — ídem en Excel (full)
  <job_id>_isrc.xlsx  — solo columna ISRC (descarga rápida de lista)

Diferencias con svc/jobs.py (batch):
  - DB y directorio de resultados separados (no comparte espacio con batch).
  - Estado inicial: 'pending' → 'running' (transición atómica en el worker).
  - 1 worker (FUGA tiene rate limits estrictos; jobs concurrentes multiplicarían
    el riesgo de 429 y ban temporal).
  - Progress fields distintos (pages_done, status_text, releases_found, isrcs_found).
  - pages_total = 80 al crear (heurística); barra determinada desde el primer tick.
  - Sanitización anti-formula-injection en CSV y XLSX.
  - cleanup_old_jobs() para evitar crecimiento ilimitado de svc/data/fuga_results/.
"""

from __future__ import annotations

import csv
import json
import logging
import sqlite3
import threading
import uuid as _uuid_mod
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# ── Importación a nivel de módulo para que los tests puedan parchear
# svc.fuga_jobs.find_isrcs_in_date_range con @patch("svc.fuga_jobs.find_isrcs_in_date_range").
# (Si el import es local dentro de _run_job, el parche en svc.fuga no afecta al worker.)
from svc.fuga import find_isrcs_in_date_range  # noqa: F401 (usado vía nombre de módulo)

logger = logging.getLogger(__name__)

# ── Rutas ─────────────────────────────────────────────────────────────────────

_SVC_DIR     = Path(__file__).parent
_DATA_DIR    = _SVC_DIR / "data"
_DB_PATH     = _DATA_DIR / "fuga_jobs.db"
_RESULTS_DIR = _DATA_DIR / "fuga_results"

_DATA_DIR.mkdir(parents=True, exist_ok=True)
_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# ── Pool de ejecución ─────────────────────────────────────────────────────────
# 1 worker: FUGA tiene rate limits estrictos; no queremos jobs concurrentes que
# puedan causar un ban temporal por exceso de peticiones.
_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="svc-fuga")

# Flags de cancelación en memoria, indexados por job_id.
_CANCEL_FLAGS: dict[str, threading.Event] = {}
_FLAGS_LOCK = threading.Lock()

# Heurística de páginas totales para mostrar barra de progreso determinada.
# FUGA catálogo: ~55k productos, 100 por página → hasta ~550 páginas máximas.
# Para un rango típico de 30-90 días la búsqueda para antes. 80 es la
# heurística del Streamlit original; mantener paridad.
_PAGES_TOTAL_ESTIMATE = 80

# Prefijos que Excel/LibreOffice interpretan como fórmulas.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


# ── Sanitización de inyección de fórmulas (CWE-1236) ─────────────────────────

def _sanitize_cell(v) -> str:
    """Prefija con comilla simple celdas que empiecen por prefijos de fórmula.

    Solo se aplica a columnas de texto libre (product_name, artist_name, label).
    """
    s = str(v) if v is not None else ""
    if s and s[0] in _FORMULA_PREFIXES:
        return "'" + s
    return s


# ── SQLite helpers ─────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    """Conexión SQLite con WAL — lecturas concurrentes durante el progreso del worker."""
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _init_db() -> None:
    """Crea la tabla fuga_jobs si no existe."""
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS fuga_jobs (
                id             TEXT PRIMARY KEY,
                estado         TEXT NOT NULL DEFAULT 'pending',
                pages_done     INT  NOT NULL DEFAULT 0,
                pages_total    INT,
                status_text    TEXT NOT NULL DEFAULT '',
                isrcs_found    INT  NOT NULL DEFAULT 0,
                releases_found INT  NOT NULL DEFAULT 0,
                date_from      TEXT NOT NULL,
                date_to        TEXT NOT NULL,
                created_at     TEXT NOT NULL,
                error_msg      TEXT
            )
        """)
        conn.commit()


def _recover_running_jobs() -> None:
    """Marca como 'error' los jobs en 'pending' o 'running' de una sesión previa.

    Un job 'running'/'pending' cuyo worker murió con el proceso no puede reanudarse.
    """
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM fuga_jobs WHERE estado IN ('running', 'pending')"
        ).fetchall()
        if rows:
            ids = [r["id"] for r in rows]
            conn.execute(
                f"UPDATE fuga_jobs SET estado = 'error', "
                f"error_msg = 'Interrumpido al reiniciar el servicio' "
                f"WHERE id IN ({','.join('?' * len(ids))})",
                ids,
            )
            conn.commit()
            for jid in ids:
                logger.warning(
                    "fuga_jobs: job %s marcado como error (interrumpido).", jid
                )


# Inicializar al importar el módulo.
_init_db()
_recover_running_jobs()


# ── CRUD del job-store ────────────────────────────────────────────────────────

def create_job(date_from: str, date_to: str) -> str:
    """Crea un registro de job FUGA en SQLite y devuelve su job_id (UUID4).

    El job se crea en estado 'pending' con pages_total = _PAGES_TOTAL_ESTIMATE (80)
    para que el frontend pueda mostrar una barra determinada desde el primer tick.
    El worker transiciona de 'pending' a 'running' de forma atómica.
    """
    job_id = str(_uuid_mod.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO fuga_jobs (
                id, estado, pages_done, pages_total, status_text,
                isrcs_found, releases_found, date_from, date_to, created_at, error_msg
            ) VALUES (?, 'pending', 0, ?, 'En cola…', 0, 0, ?, ?, ?, NULL)
            """,
            (job_id, _PAGES_TOTAL_ESTIMATE, date_from, date_to, now),
        )
        conn.commit()

    with _FLAGS_LOCK:
        _CANCEL_FLAGS[job_id] = threading.Event()

    logger.info("fuga_jobs: creado job %s (%s → %s).", job_id, date_from, date_to)
    return job_id


def start_job(job_id: str) -> None:
    """Encola el job en el ThreadPoolExecutor."""
    _POOL.submit(_run_job, job_id)
    logger.info("fuga_jobs: job %s encolado en el pool.", job_id)


def get_status(job_id: str) -> dict | None:
    """Devuelve el estado del job o None si no existe."""
    with _get_conn() as conn:
        row = conn.execute(
            """
            SELECT id, estado, pages_done, pages_total, status_text,
                   isrcs_found, releases_found, date_from, date_to, error_msg
            FROM fuga_jobs WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
    if not row:
        return None
    return dict(row)


def cancel_job(job_id: str) -> bool:
    """Señaliza cancelación del job.

    Devuelve True solo si el UPDATE afectó ≥1 fila (el job estaba en
    'pending' o 'running' y pudo cancelarse antes de que terminara).

    Devuelve False si:
    - El job no existe (→ el endpoint devuelve 404).
    - El job ya había terminado (→ el endpoint devuelve 409).
    - Hay una race condition: el job pasó a 'done' entre el get_status() y el
      UPDATE (rowcount=0 → el endpoint devuelve 409, el frontend carga el
      resultado completo).
    """
    status = get_status(job_id)
    if not status:
        return False
    if status["estado"] not in ("pending", "running"):
        return False

    # Activar flag de cancelación para que el worker pare entre páginas
    with _FLAGS_LOCK:
        flag = _CANCEL_FLAGS.get(job_id)
        if flag:
            flag.set()

    with _get_conn() as conn:
        cur = conn.execute(
            "UPDATE fuga_jobs SET estado = 'cancelled' "
            "WHERE id = ? AND estado IN ('pending', 'running')",
            (job_id,),
        )
        conn.commit()

    # Si el worker terminó entre get_status() y el UPDATE, rowcount=0.
    # Devolvemos False para que el endpoint responda 409 y el cliente cargue
    # el resultado completo en lugar de mostrar "cancelado sin datos".
    if cur.rowcount == 0:
        logger.info(
            "fuga_jobs: cancel_job sin efecto para %s (race condition — job ya terminado).",
            job_id,
        )
        return False

    logger.info("fuga_jobs: cancelación solicitada para job %s.", job_id)
    return True


def get_result_paths(job_id: str) -> dict | None:
    """Devuelve dict con rutas de resultado o None si no están disponibles.

    Disponible cuando estado ∈ {done, cancelled, error} Y el fichero .json existe.
    """
    status = get_status(job_id)
    if not status or status["estado"] not in ("done", "cancelled", "error"):
        return None

    json_path      = _RESULTS_DIR / f"{job_id}.json"
    csv_path       = _RESULTS_DIR / f"{job_id}.csv"
    xlsx_full_path = _RESULTS_DIR / f"{job_id}.xlsx"
    xlsx_isrc_path = _RESULTS_DIR / f"{job_id}_isrc.xlsx"

    if not json_path.exists():
        return None

    return {
        "json":      json_path       if json_path.exists()      else None,
        "csv":       csv_path        if csv_path.exists()        else None,
        "xlsx_full": xlsx_full_path  if xlsx_full_path.exists()  else None,
        "xlsx_isrc": xlsx_isrc_path  if xlsx_isrc_path.exists()  else None,
    }


def cleanup_old_jobs(max_age_days: int = 30) -> int:
    """Borra jobs finalizados con created_at más antiguo que max_age_days.

    Elimina los registros de la DB y los ficheros de resultado en
    svc/data/fuga_results/. No toca jobs activos (running/pending).

    Returns: número de jobs borrados.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=max_age_days)
    ).isoformat()

    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM fuga_jobs "
            "WHERE created_at < ? AND estado NOT IN ('pending', 'running')",
            (cutoff,),
        ).fetchall()

    if not rows:
        return 0

    ids = [r["id"] for r in rows]
    for job_id in ids:
        for fname in (
            f"{job_id}.json",
            f"{job_id}.csv",
            f"{job_id}.xlsx",
            f"{job_id}_isrc.xlsx",
        ):
            try:
                (_RESULTS_DIR / fname).unlink(missing_ok=True)
            except OSError:
                pass

    with _get_conn() as conn:
        conn.execute(
            f"DELETE FROM fuga_jobs WHERE id IN ({','.join('?' * len(ids))})",
            ids,
        )
        conn.commit()

    logger.info(
        "fuga_jobs: cleanup eliminó %d jobs con más de %d días.",
        len(ids), max_age_days,
    )
    return len(ids)


def shutdown_pool() -> None:
    """Cierra el ThreadPoolExecutor. Seguro llamarlo múltiples veces (idempotente)."""
    _POOL.shutdown(wait=False, cancel_futures=True)
    logger.info("fuga_jobs: pool de workers cerrado.")


# ── Worker helpers ────────────────────────────────────────────────────────────

def _update_progress(
    job_id: str, pages_done: int, releases_found: int, status_text: str
) -> None:
    """Escribe progreso incremental en SQLite. Llamado desde el worker."""
    with _get_conn() as conn:
        conn.execute(
            """
            UPDATE fuga_jobs
            SET pages_done = ?, releases_found = ?, status_text = ?
            WHERE id = ?
            """,
            (pages_done, releases_found, status_text, job_id),
        )
        conn.commit()


def _set_final(
    job_id: str,
    estado: str,
    isrcs_found: int,
    releases_found: int,
    status_text: str,
    error_msg: str | None,
) -> None:
    """Escribe el estado final del job en SQLite y limpia el flag de cancelación."""
    with _get_conn() as conn:
        conn.execute(
            """
            UPDATE fuga_jobs
            SET estado = ?, isrcs_found = ?, releases_found = ?,
                status_text = ?, error_msg = ?
            WHERE id = ?
            """,
            (estado, isrcs_found, releases_found, status_text, error_msg, job_id),
        )
        conn.commit()

    # Liberar el Event de cancelación para no acumular objetos en procesos largos.
    with _FLAGS_LOCK:
        _CANCEL_FLAGS.pop(job_id, None)


# ── Worker ────────────────────────────────────────────────────────────────────

def _run_job(job_id: str) -> None:
    """Worker FUGA. Se ejecuta en el ThreadPoolExecutor.

    Algoritmo:
    1. Lee date_from/date_to del job en SQLite.
    2. Transición atómica pending → running (WHERE estado='pending').
       Si rowcount=0 (job cancelado antes de arrancar), sale limpiamente.
    3. Llama a find_isrcs_in_date_range con un progress_cb que actualiza SQLite
       y un cancel_event para parada limpia entre páginas.
    4. Materializa los ficheros de resultado (.json, .csv, .xlsx, .xlsx_isrc).
       Si la materialización falla (disco lleno, etc.), el job termina en 'error'.
    5. Actualiza el estado final en SQLite (done/cancelled/error).
    """
    # Leer params del job
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT estado, date_from, date_to FROM fuga_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()

    if not row:
        logger.error("fuga_jobs: worker arrancó para job %s inexistente.", job_id)
        return

    if row["estado"] == "cancelled":
        logger.info("fuga_jobs: job %s ya cancelado antes de arrancar el worker.", job_id)
        _set_final(job_id, "cancelled", 0, 0, "Cancelado antes de iniciar.", None)
        return

    date_from_str = row["date_from"]
    date_to_str   = row["date_to"]

    # Transición atómica pending → running.
    # Si el job fue cancelado entre create_job y aquí, rowcount=0 y el worker sale.
    with _get_conn() as conn:
        cur = conn.execute(
            "UPDATE fuga_jobs SET estado = 'running', "
            "status_text = 'Iniciando búsqueda…' "
            "WHERE id = ? AND estado = 'pending'",
            (job_id,),
        )
        conn.commit()

    if cur.rowcount == 0:
        logger.info(
            "fuga_jobs: worker del job %s no pasó a 'running' "
            "(cancelado entre create_job y el arranque del worker).",
            job_id,
        )
        return

    try:
        date_from = date.fromisoformat(date_from_str)
        date_to   = date.fromisoformat(date_to_str)
    except ValueError as exc:
        _set_final(job_id, "error", 0, 0, "Error interno.", f"Fechas inválidas: {exc}")
        return

    with _FLAGS_LOCK:
        cancel_flag = _CANCEL_FLAGS.get(job_id, threading.Event())

    def progress_cb(page: int, releases_in_range: int, msg: str) -> None:
        """Actualiza el progreso en SQLite. No-op si el job fue cancelado."""
        if cancel_flag.is_set():
            return
        _update_progress(job_id, page, releases_in_range, msg)

    try:
        rows, error_msg = find_isrcs_in_date_range(
            date_from,
            date_to,
            progress_cb=progress_cb,
            cancel_event=cancel_flag,
        )
    except Exception as exc:
        logger.exception("fuga_jobs: error inesperado en worker del job %s.", job_id)
        _set_final(
            job_id, "error", 0, 0,
            "Error inesperado.",
            f"Error inesperado: {str(exc)[:200]}",
        )
        return

    # Leer releases_found actual (último valor escrito por progress_cb)
    current = get_status(job_id)
    releases_done = current["releases_found"] if current else 0

    # Determinar si fue cancelado durante la búsqueda
    was_cancelled = cancel_flag.is_set()

    # Materializar siempre lo que haya (resultado parcial si cancelado/error).
    # Si la materialización falla (disco lleno, permisos, etc.) el job termina
    # en 'error' para no reportar "completado" sin ficheros descargables.
    safe_rows = rows or []
    try:
        _materialize(job_id, safe_rows, date_from_str, date_to_str, releases_done)
    except Exception as mat_exc:
        logger.error(
            "fuga_jobs: error materializando resultado del job %s: %s",
            job_id, mat_exc,
        )
        _set_final(
            job_id, "error",
            len(safe_rows), releases_done,
            "Error al generar los ficheros de resultado.",
            f"Error de materialización: {str(mat_exc)[:300]}",
        )
        return

    isrcs_total = len(safe_rows)

    if was_cancelled:
        _set_final(
            job_id, "cancelled", isrcs_total, releases_done,
            f"Cancelado — {isrcs_total:,} ISRCs parciales.", None,
        )
        logger.info("fuga_jobs: job %s cancelado — %d ISRCs.", job_id, isrcs_total)
        return

    if error_msg:
        _set_final(
            job_id, "error", isrcs_total, releases_done,
            "Error al consultar FUGA.", error_msg[:500],
        )
        logger.warning("fuga_jobs: job %s terminado con error: %s", job_id, error_msg)
        return

    _set_final(
        job_id, "done", isrcs_total, releases_done,
        f"Completado — {isrcs_total:,} ISRCs.", None,
    )
    logger.info(
        "fuga_jobs: job %s finalizado — %d ISRCs, %d releases.",
        job_id, isrcs_total, releases_done,
    )


def _materialize(
    job_id: str,
    rows: list[dict],
    date_from: str,
    date_to: str,
    releases_total: int,
) -> None:
    """Genera los ficheros de resultado. Lanza excepción si algo falla.

    Produce:
      - {job_id}.json      — payload completo para el frontend
      - {job_id}.csv       — todas las columnas (saneado anti-formula-injection)
      - {job_id}.xlsx      — Excel completo (full, saneado)
      - {job_id}_isrc.xlsx — Excel solo columna ISRC (descarga rápida)
    """
    COLS      = ["isrc", "product_name", "artist_name", "label", "release_date"]
    TEXT_COLS = {"product_name", "artist_name", "label"}  # campos de texto libre

    json_path      = _RESULTS_DIR / f"{job_id}.json"
    csv_path       = _RESULTS_DIR / f"{job_id}.csv"
    xlsx_full_path = _RESULTS_DIR / f"{job_id}.xlsx"
    xlsx_isrc_path = _RESULTS_DIR / f"{job_id}_isrc.xlsx"

    # Filas saneadas para CSV y XLSX (defensa contra formula injection)
    sanitized_rows = [
        {
            col: (
                _sanitize_cell(row.get(col))
                if col in TEXT_COLS
                else (row.get(col) or "")
            )
            for col in COLS
        }
        for row in rows
    ]

    # result.json — contrato con el frontend (sin sanitizar: React escapa el texto)
    result = {
        "rows":           rows,
        "date_from":      date_from,
        "date_to":        date_to,
        "isrcs_total":    len(rows),
        "releases_total": releases_total,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    # result.csv (saneado)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(sanitized_rows)

    # result.xlsx (full) y _isrc.xlsx (saneados)
    import pandas as pd
    df = pd.DataFrame(sanitized_rows if sanitized_rows else [], columns=COLS)
    df.to_excel(str(xlsx_full_path), index=False, engine="openpyxl")
    df[["isrc"]].to_excel(str(xlsx_isrc_path), index=False, engine="openpyxl")

    logger.info(
        "fuga_jobs: materializado job %s — %d ISRCs, %d releases.",
        job_id, len(rows), releases_total,
    )
