"""
svc/spotify_jobs.py — Job-store para creación de playlists Spotify (F4).

Esquema SQLite (svc/data/spotify_jobs.db):
  spotify_jobs(
    id              TEXT PRIMARY KEY,
    estado          TEXT,      -- pending | running | done | cancelled | error
    phase           TEXT,      -- resolving | creating | adding
    total           INT,       -- total ISRCs recibidos
    resolved        INT,       -- ISRCs resueltos a URI
    added           INT,       -- tracks añadidos a la playlist
    not_found_count INT,       -- ISRCs sin URI + errores de resolución
    progress_pct    REAL,      -- 0-100
    status_text     TEXT,
    cooldown_until  TEXT,      -- ISO-8601 UTC | NULL (penalty-box activo)
    error_msg       TEXT,      -- NULL si ok
    params          TEXT,      -- JSON: {isrcs, name, description, public}
    created_at      TEXT       -- ISO-8601 UTC
  )

Resultados en svc/data/spotify_results/:
  <job_id>.json          — {playlist_url, playlist_name, tracks_added,
                             not_found_isrcs, total_isrcs}
  <job_id>_not_found.csv — columna ISRC de los ISRCs no encontrados

Diferencias respecto a fuga_jobs.py:
  - Pool de 1 worker (el rate-limit de Spotify Dev Mode exige secuencialidad).
  - Estado "cooldown" no es un estado de la DB (sigue en 'running'); el endpoint
    /status lo calcula de forma dinámica a partir de cooldown_until.
  - cleanup_old_jobs() retención de 7 días (resultado es solo URL + ISRCs).
  - Aplica todos los aprendizajes de F3: estado 'pending'→'running' atómico,
    cancel con rowcount, materialización fallo→error, _CANCEL_FLAGS limpiado
    en _set_final, cancel_event respetado entre llamadas Spotify.

Importaciones a nivel de módulo para que los tests puedan parchear:
  @patch("svc.spotify_jobs.resolve_isrcs", ...)
  @patch("svc.spotify_jobs.create_playlist", ...)
  @patch("svc.spotify_jobs.add_tracks_to_playlist", ...)
"""

from __future__ import annotations

import csv
import json
import logging
import sqlite3
import threading
import uuid as _uuid_mod
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from svc.spotify import (  # noqa: F401 — importación nivel módulo para patching
    resolve_isrcs,
    create_playlist,
    add_tracks_to_playlist,
    has_central_token,
    get_setup_status,
    generate_login_url,
    exchange_code,
    delete_central_token,
    is_admin,
)

logger = logging.getLogger(__name__)

# ── Rutas ─────────────────────────────────────────────────────────────────────

_SVC_DIR     = Path(__file__).parent
_DATA_DIR    = _SVC_DIR / "data"
_DB_PATH     = _DATA_DIR / "spotify_jobs.db"
_RESULTS_DIR = _DATA_DIR / "spotify_results"

_DATA_DIR.mkdir(parents=True, exist_ok=True)
_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# ── Pool de ejecución ─────────────────────────────────────────────────────────
# 1 worker: rate-limit de Spotify Dev Mode exige serializar los jobs.

_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="svc-spotify")

_CANCEL_FLAGS: dict[str, threading.Event] = {}
_FLAGS_LOCK = threading.Lock()


# ── SQLite helpers ─────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _init_db() -> None:
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS spotify_jobs (
                id              TEXT PRIMARY KEY,
                estado          TEXT NOT NULL DEFAULT 'pending',
                phase           TEXT NOT NULL DEFAULT 'resolving',
                total           INT  NOT NULL DEFAULT 0,
                resolved        INT  NOT NULL DEFAULT 0,
                added           INT  NOT NULL DEFAULT 0,
                not_found_count INT  NOT NULL DEFAULT 0,
                progress_pct    REAL NOT NULL DEFAULT 0.0,
                status_text     TEXT NOT NULL DEFAULT '',
                cooldown_until  TEXT,
                error_msg       TEXT,
                params          TEXT NOT NULL DEFAULT '{}',
                created_at      TEXT NOT NULL
            )
        """)
        conn.commit()


def _recover_running_jobs() -> None:
    """Marca como 'error' los jobs en 'pending' o 'running' de una sesión previa."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM spotify_jobs WHERE estado IN ('running', 'pending')"
        ).fetchall()
        if rows:
            ids = [r["id"] for r in rows]
            conn.execute(
                f"UPDATE spotify_jobs SET estado = 'error', "
                f"error_msg = 'Interrumpido al reiniciar el servicio' "
                f"WHERE id IN ({','.join('?' * len(ids))})",
                ids,
            )
            conn.commit()
            for jid in ids:
                logger.warning("spotify_jobs: job %s marcado como error (interrumpido).", jid)


_init_db()
_recover_running_jobs()


# ── CRUD ───────────────────────────────────────────────────────────────────────

def create_job(
    isrcs: list[str],
    name: str,
    description: str,
    public: bool,
) -> str:
    """Crea un job de creación de playlist y devuelve su job_id (UUID4)."""
    job_id = str(_uuid_mod.uuid4())
    now    = datetime.now(timezone.utc).isoformat()
    params = json.dumps({"isrcs": isrcs, "name": name, "description": description, "public": public})

    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO spotify_jobs (
                id, estado, phase, total, resolved, added, not_found_count,
                progress_pct, status_text, cooldown_until, error_msg, params, created_at
            ) VALUES (?, 'pending', 'resolving', ?, 0, 0, 0, 0.0, 'En cola…', NULL, NULL, ?, ?)
            """,
            (job_id, len(isrcs), params, now),
        )
        conn.commit()

    with _FLAGS_LOCK:
        _CANCEL_FLAGS[job_id] = threading.Event()

    logger.info("spotify_jobs: creado job %s con %d ISRCs.", job_id, len(isrcs))
    return job_id


def start_job(job_id: str) -> None:
    """Encola el job en el ThreadPoolExecutor."""
    _POOL.submit(_run_job, job_id)
    logger.info("spotify_jobs: job %s encolado.", job_id)


def get_status(job_id: str) -> dict | None:
    """Devuelve el estado del job o None si no existe."""
    with _get_conn() as conn:
        row = conn.execute(
            """
            SELECT id, estado, phase, total, resolved, added, not_found_count,
                   progress_pct, status_text, cooldown_until, error_msg
            FROM spotify_jobs WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
    if not row:
        return None
    return dict(row)


def cancel_job(job_id: str) -> bool:
    """Cancela el job si está en 'pending' o 'running'.

    Devuelve True si la cancelación se registró; False si el job no existe o
    ya está en estado terminal (→ el endpoint responde 409 o 404).
    """
    status = get_status(job_id)
    if not status:
        return False
    if status["estado"] not in ("pending", "running"):
        return False

    with _FLAGS_LOCK:
        flag = _CANCEL_FLAGS.get(job_id)
        if flag:
            flag.set()

    with _get_conn() as conn:
        cur = conn.execute(
            "UPDATE spotify_jobs SET estado = 'cancelled' "
            "WHERE id = ? AND estado IN ('pending', 'running')",
            (job_id,),
        )
        conn.commit()

    if cur.rowcount == 0:
        logger.info("spotify_jobs: cancel_job sin efecto (race condition) para %s.", job_id)
        return False

    logger.info("spotify_jobs: cancelación solicitada para job %s.", job_id)
    return True


def get_result_path(job_id: str) -> Path | None:
    """Devuelve la ruta al .json de resultado si está disponible."""
    status = get_status(job_id)
    if not status or status["estado"] not in ("done", "cancelled", "error"):
        return None
    p = _RESULTS_DIR / f"{job_id}.json"
    return p if p.exists() else None


def get_not_found_csv_path(job_id: str) -> Path | None:
    """Devuelve la ruta al CSV de ISRCs no encontrados si está disponible."""
    status = get_status(job_id)
    if not status or status["estado"] not in ("done", "cancelled", "error"):
        return None
    p = _RESULTS_DIR / f"{job_id}_not_found.csv"
    return p if p.exists() else None


def count_active_jobs() -> int:
    """Devuelve el número de jobs activos (pending + running)."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM spotify_jobs WHERE estado IN ('pending', 'running')"
        ).fetchone()
    return int(row[0]) if row else 0


def cleanup_old_jobs(max_age_days: int = 7) -> int:
    """Borra jobs finalizados con created_at mayor que max_age_days.

    No toca jobs activos (pending/running).
    Devuelve el número de jobs borrados.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=max_age_days)
    ).isoformat()

    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM spotify_jobs "
            "WHERE created_at < ? AND estado NOT IN ('pending', 'running')",
            (cutoff,),
        ).fetchall()

    if not rows:
        return 0

    ids = [r["id"] for r in rows]
    for job_id in ids:
        for fname in (f"{job_id}.json", f"{job_id}_not_found.csv"):
            try:
                (_RESULTS_DIR / fname).unlink(missing_ok=True)
            except OSError:
                pass

    with _get_conn() as conn:
        conn.execute(
            f"DELETE FROM spotify_jobs WHERE id IN ({','.join('?' * len(ids))})",
            ids,
        )
        conn.commit()

    logger.info("spotify_jobs: cleanup eliminó %d jobs (>%d días).", len(ids), max_age_days)
    return len(ids)


def shutdown_pool() -> None:
    _POOL.shutdown(wait=False, cancel_futures=True)
    logger.info("spotify_jobs: pool cerrado.")


# ── Worker helpers ─────────────────────────────────────────────────────────────

def _update_progress(
    job_id: str,
    phase: str,
    resolved: int,
    total: int,
    not_found_count: int,
    added: int,
    progress_pct: float,
    status_text: str,
    cooldown_until: str | None,
) -> None:
    """Actualiza el progreso incremental en SQLite. Llamado desde el worker."""
    with _get_conn() as conn:
        conn.execute(
            """
            UPDATE spotify_jobs
            SET phase=?, resolved=?, total=?, not_found_count=?,
                added=?, progress_pct=?, status_text=?, cooldown_until=?
            WHERE id=?
            """,
            (phase, resolved, total, not_found_count,
             added, progress_pct, status_text, cooldown_until, job_id),
        )
        conn.commit()


def _set_final(
    job_id: str,
    estado: str,
    phase: str,
    resolved: int,
    total: int,
    not_found_count: int,
    added: int,
    progress_pct: float,
    status_text: str,
    error_msg: str | None,
) -> None:
    """Escribe el estado final en SQLite y limpia el flag de cancelación."""
    with _get_conn() as conn:
        conn.execute(
            """
            UPDATE spotify_jobs
            SET estado=?, phase=?, resolved=?, total=?, not_found_count=?,
                added=?, progress_pct=?, status_text=?, cooldown_until=NULL, error_msg=?
            WHERE id=?
            """,
            (estado, phase, resolved, total, not_found_count,
             added, progress_pct, status_text, error_msg, job_id),
        )
        conn.commit()

    with _FLAGS_LOCK:
        _CANCEL_FLAGS.pop(job_id, None)


# ── Materialización de resultados ─────────────────────────────────────────────

def _materialize(
    job_id: str,
    playlist_url: str,
    playlist_name: str,
    tracks_added: int,
    not_found_isrcs: list[str],
    total_isrcs: int,
    errors_count: int = 0,
) -> None:
    """Escribe los ficheros de resultado. Lanza excepción si algo falla.

    Produce:
      {job_id}.json           — payload para el frontend
      {job_id}_not_found.csv  — lista CSV de ISRCs genuinamente sin URI en Spotify

    errors_count: ISRCs que fallaron durante la resolución + tracks que fallaron
    al añadirse (distintos de los que simplemente no están en Spotify).
    """
    json_path     = _RESULTS_DIR / f"{job_id}.json"
    csv_path      = _RESULTS_DIR / f"{job_id}_not_found.csv"

    result = {
        "playlist_url":    playlist_url,
        "playlist_name":   playlist_name,
        "tracks_added":    tracks_added,
        "not_found_isrcs": not_found_isrcs,
        "total_isrcs":     total_isrcs,
        "errors_count":    errors_count,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ISRC"])
        for isrc in not_found_isrcs:
            writer.writerow([isrc])

    logger.info(
        "spotify_jobs: materializado job %s — added=%d, not_found=%d.",
        job_id, tracks_added, len(not_found_isrcs),
    )


# ── Worker ─────────────────────────────────────────────────────────────────────

def _run_job(job_id: str) -> None:
    """Worker principal. Se ejecuta en el ThreadPoolExecutor.

    Fases:
      1. resolving — resolve_isrcs(): ISRCs → URIs Spotify (0-70%)
      2. creating  — create_playlist(): crea la playlist en la cuenta central (70%)
      3. adding    — add_tracks_to_playlist(): añade URIs en lotes de 100 (70-100%)
    """
    # Leer parámetros del job
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT estado, params FROM spotify_jobs WHERE id = ?", (job_id,)
        ).fetchone()

    if not row:
        logger.error("spotify_jobs: worker arrancó para job %s inexistente.", job_id)
        return

    if row["estado"] == "cancelled":
        logger.info("spotify_jobs: job %s ya cancelado antes de arrancar.", job_id)
        _set_final(job_id, "cancelled", "resolving", 0, 0, 0, 0, 0.0, "Cancelado antes de iniciar.", None)
        return

    params      = json.loads(row["params"] or "{}")
    isrcs:  list[str] = params.get("isrcs", [])
    name:   str       = params.get("name", "New Playlist")
    description: str  = params.get("description", "")
    public: bool      = params.get("public", False)
    total             = len(isrcs)

    with _FLAGS_LOCK:
        cancel_event = _CANCEL_FLAGS.get(job_id, threading.Event())

    # Transición atómica pending → running
    with _get_conn() as conn:
        cur = conn.execute(
            "UPDATE spotify_jobs SET estado='running', status_text='Iniciando…' "
            "WHERE id=? AND estado='pending'",
            (job_id,),
        )
        conn.commit()

    if cur.rowcount == 0:
        logger.info(
            "spotify_jobs: worker del job %s no pasó a 'running' (cancelado entre create y arranque).",
            job_id,
        )
        return

    # Variables locales para el handler de except (fix 3: no resetear fase/contadores)
    _curr_phase         = "resolving"
    _curr_resolved      = 0
    _curr_not_found_cnt = 0
    _curr_added         = 0
    _curr_progress_pct  = 0.0
    _curr_not_found_isrcs: list[str] = []
    _curr_errors_count  = 0

    try:
        # ── Fase 1: Resolver ISRCs ─────────────────────────────────────────────

        def _progress_cb(resolved: int, tot: int, nf_count: int, status_text: str) -> None:
            pct = int(resolved / tot * 70) if tot else 0
            _update_progress(
                job_id, "resolving", resolved, tot, nf_count, 0,
                float(pct), status_text, None,
            )

        def _cooldown_cb(until_epoch: float) -> None:
            """Actualiza cooldown_until en la DB para que /status exponga estado 'cooldown'."""
            if until_epoch > 0:
                cd_iso = datetime.fromtimestamp(until_epoch, tz=timezone.utc).isoformat()
            else:
                cd_iso = None
            with _get_conn() as conn:
                conn.execute(
                    "UPDATE spotify_jobs SET cooldown_until=? WHERE id=?",
                    (cd_iso, job_id),
                )
                conn.commit()

        _update_progress(job_id, "resolving", 0, total, 0, 0, 0.0, "Resolviendo ISRCs…", None)

        resolve_result = resolve_isrcs(
            isrcs,
            progress_cb=_progress_cb,
            cooldown_cb=_cooldown_cb,
            cancel_event=cancel_event,
        )

        # Separar ISRCs genuinamente no encontrados de los que erraron (fix 19)
        uris                   = resolve_result["uris"]
        not_found_isrcs        = resolve_result["not_found"]   # genuinamente no en Spotify
        resolve_errors_isrcs   = resolve_result["errors"]      # errores de resolución
        not_found_count        = len(not_found_isrcs) + len(resolve_errors_isrcs)
        resolved_count         = len(uris)
        errors_count           = len(resolve_errors_isrcs)

        # Actualizar vars para except handler
        _curr_phase          = "resolving"
        _curr_resolved       = resolved_count
        _curr_not_found_cnt  = not_found_count
        _curr_not_found_isrcs = not_found_isrcs
        _curr_errors_count   = errors_count
        _curr_progress_pct   = float(int(resolved_count / total * 70) if total else 70)

        # Fix 2a: resolve devolvió stopped (CC token no disponible) → error, no playlist vacía
        if resolve_result.get("stopped"):
            _materialize(job_id, "", name, 0, not_found_isrcs, total, errors_count)
            _set_final(
                job_id, "error", "resolving",
                resolved_count, total, not_found_count, 0,
                _curr_progress_pct,
                "Parada anticipada: sin Client Credentials token.",
                "La resolución de ISRCs no pudo iniciarse. Verifica SPOTIFY_CLIENT_ID "
                "y SPOTIFY_CLIENT_SECRET en el entorno.",
            )
            return

        if cancel_event.is_set():
            _materialize(job_id, "", name, 0, not_found_isrcs, total, errors_count)
            _set_final(
                job_id, "cancelled", "resolving",
                resolved_count, total, not_found_count, 0,
                _curr_progress_pct,
                "Cancelado durante la resolución de ISRCs.",
                None,
            )
            return

        # ── Fase 2: Crear playlist ─────────────────────────────────────────────

        _curr_phase        = "creating"
        _curr_progress_pct = 70.0

        _update_progress(
            job_id, "creating", resolved_count, total, not_found_count,
            0, 70.0, "Creando playlist…", None,
        )

        pl = create_playlist(name, description, public)
        if pl is None:
            _materialize(job_id, "", name, 0, not_found_isrcs, total, errors_count)
            _set_final(
                job_id, "error", "creating",
                resolved_count, total, not_found_count, 0,
                70.0,
                "No se pudo crear la playlist.",
                "create_playlist devolvió None. Verifica el token de la cuenta central.",
            )
            return

        playlist_id  = pl["id"]
        playlist_url = pl.get("external_urls", {}).get("spotify", "")

        if cancel_event.is_set():
            _materialize(job_id, playlist_url, name, 0, not_found_isrcs, total, errors_count)
            _set_final(
                job_id, "cancelled", "creating",
                resolved_count, total, not_found_count, 0,
                70.0, "Cancelado tras crear la playlist.", None,
            )
            return

        # ── Fase 3: Añadir tracks ─────────────────────────────────────────────

        _curr_phase        = "adding"
        _curr_progress_pct = 72.0

        total_found = len(uris)
        _update_progress(
            job_id, "adding", resolved_count, total, not_found_count,
            0, 72.0,
            f"Añadiendo tracks (0/{total_found})…",
            None,
        )

        def _add_progress_cb(added_so_far: int, tot_f: int) -> None:
            pct = 70.0 + (added_so_far / tot_f * 30.0) if tot_f else 100.0
            _update_progress(
                job_id, "adding", resolved_count, total, not_found_count,
                added_so_far, pct,
                f"Añadiendo tracks ({added_so_far}/{tot_f})…",
                None,
            )

        add_result = add_tracks_to_playlist(
            playlist_id, uris,
            progress_cb=_add_progress_cb,
            cancel_event=cancel_event,
        )
        added         = add_result["added"]
        tracks_failed = add_result["failed"]
        total_errors  = errors_count + tracks_failed   # errores de resolve + add

        # Actualizar vars para except handler
        _curr_added         = added
        _curr_errors_count  = total_errors
        _curr_progress_pct  = 70.0 + (added / total_found * 30.0) if total_found else 100.0

        # Materializar siempre (resultado parcial si cancelado o fallido)
        _materialize(job_id, playlist_url, name, added, not_found_isrcs, total, total_errors)

        if cancel_event.is_set():
            _set_final(
                job_id, "cancelled", "adding",
                resolved_count, total, not_found_count, added,
                _curr_progress_pct,
                "Cancelado durante la adición de tracks.",
                None,
            )
            return

        # Fix 2b: token expirado/sin permisos → added==0 aunque había URIs que añadir
        if total_found > 0 and added == 0:
            _set_final(
                job_id, "error", "adding",
                resolved_count, total, not_found_count, 0,
                70.0,
                "No se pudo añadir ningún track a la playlist.",
                "El token central expiró o no tiene permisos. "
                "Reconecta la cuenta en /playlist/setup.",
            )
            return

        _set_final(
            job_id, "done", "adding",
            resolved_count, total, not_found_count, added,
            100.0,
            f"Completado — {added} track{'s' if added != 1 else ''} añadido{'s' if added != 1 else ''}.",
            None,
        )
        logger.info(
            "spotify_jobs: job %s completado — added=%d, not_found=%d, errors=%d.",
            job_id, added, not_found_count, total_errors,
        )

    except Exception as exc:
        logger.exception("spotify_jobs: error inesperado en worker del job %s.", job_id)
        # Fix 3: usar variables locales para reflejar el progreso real, no hardcodear fase/contadores
        try:
            _materialize(
                job_id, "", name, _curr_added, _curr_not_found_isrcs, total, _curr_errors_count,
            )
        except Exception as mat_exc:
            logger.warning("spotify_jobs: no se pudo materializar resultado parcial: %s", mat_exc)
        _set_final(
            job_id, "error", _curr_phase,
            _curr_resolved, total, _curr_not_found_cnt, _curr_added,
            _curr_progress_pct,
            "Error inesperado.",
            f"Error inesperado: {str(exc)[:300]}",
        )
