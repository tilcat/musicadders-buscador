"""
svc/jobs.py — Job-store persistente en SQLite + ThreadPoolExecutor para el
procesado batch de ISRCs.

Esquema SQLite (svc/data/jobs.db):
  jobs(
    id TEXT PRIMARY KEY,
    estado TEXT,            -- pending | running | done | cancelled | error
    total INT,
    hechos INT,
    calls_used INT,
    not_found_count INT,
    created_at TEXT,        -- ISO-8601 UTC
    params TEXT             -- JSON con {platforms, scope, buster}
  )

Resultado materializado en svc/data/results/<job_id>.jsonl (una línea JSON
por cada ISRC procesado). Al finalizar el job se construye:
  - svc/data/results/<job_id>.json  → resumen (meta count, playlists, not_found)
  - svc/data/results/<job_id>.csv   → filas de playlists (para descarga)
  - svc/data/results/<job_id>.xlsx  → ídem en Excel

Al arrancar el módulo, los jobs que quedaron en estado 'running' de una
ejecución previa se marcan como 'error' (interrumpido), porque su estado
en memoria se perdió con el proceso. No se pueden reanudar.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid as _uuid_mod
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Rutas ─────────────────────────────────────────────────────────────────────

_SVC_DIR = Path(__file__).parent
_DATA_DIR = _SVC_DIR / "data"
_DB_PATH = _DATA_DIR / "jobs.db"
_RESULTS_DIR = _DATA_DIR / "results"

_DATA_DIR.mkdir(parents=True, exist_ok=True)
_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# ── Pool de ejecución ─────────────────────────────────────────────────────────
# 1-2 workers: el procesado batch es CPU-light pero I/O-heavy (llamadas HTTP
# a Soundcharts). 2 workers permite dos jobs concurrentes sin sobrecargar la
# cuota de la API.
_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="svc-batch")

# Flags de cancelación en memoria, indexados por job_id.
# Se consultan entre ISRCs en el worker. Ligeros e inmutables: dict[str, threading.Event]
_CANCEL_FLAGS: dict[str, threading.Event] = {}
_FLAGS_LOCK = threading.Lock()


# ── SQLite helpers ─────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    """Conexión SQLite con WAL, timeout y check_same_thread=False.

    WAL permite lecturas concurrentes mientras el worker escribe progreso,
    eliminando el 'database is locked' con 2 workers activos.
    timeout=30 evita fallar inmediatamente en contención puntual.
    """
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _init_db() -> None:
    """Crea la tabla jobs si no existe."""
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id             TEXT PRIMARY KEY,
                estado         TEXT NOT NULL DEFAULT 'pending',
                total          INT  NOT NULL DEFAULT 0,
                hechos         INT  NOT NULL DEFAULT 0,
                calls_used     INT  NOT NULL DEFAULT 0,
                not_found_count INT NOT NULL DEFAULT 0,
                created_at     TEXT NOT NULL,
                params         TEXT NOT NULL DEFAULT '{}'
            )
        """)
        conn.commit()


def _recover_running_jobs() -> None:
    """Marca como 'error' los jobs que quedaron en 'running' de una sesión previa.

    Un job 'running' cuyo worker murió con el proceso no puede reanudarse:
    el estado en memoria (progreso parcial, fichero .jsonl abierto) se perdió.
    Los marcamos como 'error' para que la UI no los muestre como activos.
    """
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT id FROM jobs WHERE estado = 'running'"
        ).fetchall()
        if rows:
            ids = [r["id"] for r in rows]
            conn.execute(
                f"UPDATE jobs SET estado = 'error' WHERE id IN ({','.join('?' * len(ids))})",
                ids,
            )
            conn.commit()
            for jid in ids:
                logger.warning(
                    "jobs: job %s marcado como 'error' (interrumpido al reiniciar el servicio).",
                    jid,
                )


# Inicializar al importar el módulo.
_init_db()
_recover_running_jobs()


# ── CRUD del job-store ────────────────────────────────────────────────────────

def create_job(isrcs: list[str], platforms: list[str], scope: str) -> str:
    """Crea un registro de job en SQLite y devuelve su job_id (UUID4).

    No arranca el procesado: llama a start_job() tras crear el job.
    """
    job_id = str(_uuid_mod.uuid4())
    params = json.dumps({"platforms": platforms, "scope": scope, "isrcs": isrcs})
    now = datetime.now(timezone.utc).isoformat()

    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO jobs (id, estado, total, hechos, calls_used,
                              not_found_count, created_at, params)
            VALUES (?, 'pending', ?, 0, 0, 0, ?, ?)
            """,
            (job_id, len(isrcs), now, params),
        )
        conn.commit()

    # Crear flag de cancelación para este job
    with _FLAGS_LOCK:
        _CANCEL_FLAGS[job_id] = threading.Event()

    logger.info("jobs: creado job %s con %d ISRCs.", job_id, len(isrcs))
    return job_id


def start_job(job_id: str) -> None:
    """Encola el job en el ThreadPoolExecutor.

    El job pasa a 'running' en el momento en que el worker lo arranca
    (no al encolar), para reflejar cuándo empieza a consumir cuota.
    """
    _POOL.submit(_run_job, job_id)
    logger.info("jobs: job %s encolado en el pool.", job_id)


def get_status(job_id: str) -> dict | None:
    """Devuelve el estado del job o None si no existe."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT id, estado, total, hechos, calls_used, not_found_count, created_at "
            "FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    if not row:
        return None
    return dict(row)


def get_result_path(job_id: str) -> Path | None:
    """Devuelve la ruta al fichero .json de resumen si está disponible.

    Disponible cuando estado es 'done', 'cancelled' o 'error' (resultado parcial).
    En el caso de 'error', solo se sirve si el fichero existe (materialización
    parcial exitosa). Si el job sigue pending/running devuelve None.
    """
    status = get_status(job_id)
    if not status or status["estado"] not in ("done", "cancelled", "error"):
        return None
    p = _RESULTS_DIR / f"{job_id}.json"
    return p if p.exists() else None


def get_csv_path(job_id: str) -> Path | None:
    """Devuelve la ruta al .csv de playlists si existe.

    Accesible también en estado 'error' (resultado parcial).
    """
    status = get_status(job_id)
    if not status or status["estado"] not in ("done", "cancelled", "error"):
        return None
    p = _RESULTS_DIR / f"{job_id}.csv"
    return p if p.exists() else None


def get_xlsx_path(job_id: str) -> Path | None:
    """Devuelve la ruta al .xlsx de playlists si existe.

    Accesible también en estado 'error' (resultado parcial).
    """
    status = get_status(job_id)
    if not status or status["estado"] not in ("done", "cancelled", "error"):
        return None
    p = _RESULTS_DIR / f"{job_id}.xlsx"
    return p if p.exists() else None


def cancel_job(job_id: str) -> bool:
    """Señaliza cancelación del job.

    Si el job está en 'pending' o 'running', activa el flag y lo marca
    en SQLite. El worker lo leerá entre ISRCs y terminará limpiamente.
    Si ya está terminado, no hace nada y devuelve False.
    """
    status = get_status(job_id)
    if not status:
        return False
    if status["estado"] not in ("pending", "running"):
        return False

    # Activar flag en memoria (el worker lo lee entre ISRCs)
    with _FLAGS_LOCK:
        flag = _CANCEL_FLAGS.get(job_id)
        if flag:
            flag.set()

    # Marcar en SQLite (para jobs pending que aún no arrancaron en memoria)
    with _get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET estado = 'cancelled' WHERE id = ? AND estado IN ('pending', 'running')",
            (job_id,),
        )
        conn.commit()

    logger.info("jobs: cancelación solicitada para job %s.", job_id)
    return True


# ── Worker ────────────────────────────────────────────────────────────────────

# Número de ISRCs entre cada escritura de progreso a SQLite.
# Valor bajo = polling fluido; valor alto = menos escrituras a disco.
_PROGRESS_INTERVAL = 5


def _update_progress(job_id: str, hechos: int, calls_used: int, not_found_count: int) -> None:
    """Escribe progreso incremental en SQLite (llamada desde el worker)."""
    with _get_conn() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET hechos = ?, calls_used = ?, not_found_count = ?
            WHERE id = ?
            """,
            (hechos, calls_used, not_found_count, job_id),
        )
        conn.commit()


def _run_job(job_id: str) -> None:
    """Worker que ejecuta el batch. Se lanza en el ThreadPoolExecutor.

    Algoritmo:
    1. Lee params del job desde SQLite.
    2. Marca el job como 'running'.
    3. Abre fichero .jsonl de resultados parciales.
    4. Itera ISRCs: llama search_isrc, escribe resultado en .jsonl,
       actualiza progreso en SQLite cada _PROGRESS_INTERVAL ISRCs.
    5. Entre ISRCs: comprueba el flag de cancelación.
    6. Al terminar: materializa .json/.csv/.xlsx, marca done/cancelled/error.
    """
    from svc.soundcharts import search_isrc, _is_official_type

    # Leer params
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT estado, params FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
    if not row:
        logger.error("jobs: worker arrancó para job %s inexistente.", job_id)
        return
    if row["estado"] == "cancelled":
        logger.info("jobs: job %s ya cancelado antes de arrancar el worker.", job_id)
        return

    params = json.loads(row["params"] or "{}")
    isrcs: list[str] = params.get("isrcs", [])
    platforms: list[str] = params.get("platforms", ["spotify", "apple-music", "amazon", "deezer"])

    # Obtener flag de cancelación
    with _FLAGS_LOCK:
        cancel_flag = _CANCEL_FLAGS.get(job_id, threading.Event())

    # Marcar running — solo si sigue en 'pending' (cancelación atómica).
    # Si entre create_job y aquí llegó un cancel_job, el estado ya es 'cancelled'
    # y rowcount será 0: el worker sale sin sobrescribir el estado.
    with _get_conn() as conn:
        cur = conn.execute(
            "UPDATE jobs SET estado = 'running' WHERE id = ? AND estado = 'pending'",
            (job_id,),
        )
        conn.commit()
    if cur.rowcount == 0:
        logger.info(
            "jobs: worker del job %s no pudo pasar a 'running' (ya cancelado o no existe).",
            job_id,
        )
        return

    jsonl_path = _RESULTS_DIR / f"{job_id}.jsonl"
    hechos = 0
    calls_used = 0
    not_found_count = 0
    job_error: str | None = None
    cancelled_early = False

    try:
        with open(jsonl_path, "w", encoding="utf-8") as fout:
            for i, isrc in enumerate(isrcs):
                # Comprobar cancelación entre ISRCs
                if cancel_flag.is_set():
                    # Marcar los restantes como no procesados
                    for isrc_pend in isrcs[i:]:
                        record = {
                            "isrc": isrc_pend,
                            "status": "cancelled",
                            "meta": None,
                            "playlists": [],
                        }
                        fout.write(json.dumps(record, ensure_ascii=False) + "\n")
                        not_found_count += 1
                    cancelled_early = True
                    break

                # Procesar ISRC
                try:
                    res = search_isrc(isrc, platforms)
                except RuntimeError as exc:
                    # 429: detener el job completo — respetar la cuota
                    logger.error(
                        "jobs: job %s detenido por 429 en ISRC %s: %s", job_id, isrc, exc
                    )
                    job_error = str(exc)
                    # Registrar los ISRCs restantes como no procesados
                    for isrc_rem in isrcs[i:]:
                        record = {
                            "isrc": isrc_rem,
                            "status": "error_429",
                            "meta": None,
                            "playlists": [],
                        }
                        fout.write(json.dumps(record, ensure_ascii=False) + "\n")
                        not_found_count += 1
                    break
                except Exception as exc:
                    logger.warning(
                        "jobs: error en ISRC %s (job %s): %s", isrc, job_id, exc
                    )
                    record = {
                        "isrc": isrc,
                        "status": "error",
                        "error": str(exc)[:200],
                        "meta": None,
                        "playlists": [],
                    }
                    fout.write(json.dumps(record, ensure_ascii=False) + "\n")
                    not_found_count += 1
                    hechos += 1
                    calls_used += 1
                else:
                    status_isrc = "found" if res.get("meta") else "not_found"
                    if not res.get("meta"):
                        not_found_count += 1
                    record = {
                        "isrc": isrc,
                        "status": status_isrc,
                        "meta": res.get("meta"),
                        "playlists": res.get("playlists", []),
                    }
                    fout.write(json.dumps(record, ensure_ascii=False) + "\n")
                    hechos += 1
                    calls_used += res.get("calls_used", 1)

                # Actualizar progreso incremental cada N ISRCs
                if (i + 1) % _PROGRESS_INTERVAL == 0:
                    _update_progress(job_id, hechos, calls_used, not_found_count)

        # Progreso final antes de materializar
        _update_progress(job_id, hechos, calls_used, not_found_count)

        # Materializar resultados desde el .jsonl.
        # Se ejecuta SIEMPRE (done, cancelled y error) para que el trabajo
        # consumido no se pierda: el front puede descargar el resultado parcial.
        _materialize(job_id, jsonl_path)

        # Determinar estado final
        if job_error:
            final_estado = "error"
        elif cancelled_early or cancel_flag.is_set():
            final_estado = "cancelled"
        else:
            final_estado = "done"

        with _get_conn() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET estado = ?, hechos = ?, calls_used = ?, not_found_count = ?
                WHERE id = ?
                """,
                (final_estado, hechos, calls_used, not_found_count, job_id),
            )
            conn.commit()
        logger.info(
            "jobs: job %s finalizado → estado=%s, hechos=%d, calls=%d, not_found=%d.",
            job_id, final_estado, hechos, calls_used, not_found_count,
        )

    except Exception as exc:
        logger.exception("jobs: error inesperado en worker del job %s.", job_id)
        # Intentar materializar lo que haya en el .jsonl antes de marcar error,
        # para no perder el trabajo parcial completado hasta ese punto.
        try:
            if jsonl_path.exists():
                _materialize(job_id, jsonl_path)
        except Exception as mat_exc:
            logger.warning(
                "jobs: no se pudo materializar resultado parcial del job %s: %s",
                job_id, mat_exc,
            )
        with _get_conn() as conn:
            conn.execute(
                "UPDATE jobs SET estado = 'error' WHERE id = ?", (job_id,)
            )
            conn.commit()


def _materialize(job_id: str, jsonl_path: Path) -> None:
    """Lee el .jsonl y construye .json (resumen), .csv y .xlsx.

    Materializa por lotes para no acumular todo en RAM de golpe —
    la deuda OOM del procesado batch original de Streamlit.
    """
    import csv

    json_path = _RESULTS_DIR / f"{job_id}.json"
    csv_path = _RESULTS_DIR / f"{job_id}.csv"
    xlsx_path = _RESULTS_DIR / f"{job_id}.xlsx"

    # Columnas del CSV/XLSX de playlists
    COLS = [
        "isrc", "song_name", "credit_name",
        "platform", "playlist_name", "playlist_type",
        "subscriber_count", "position", "peak_position",
        "country_code", "entry_date", "playlist_uuid",
    ]

    meta_count = 0
    total_playlists = 0
    not_found: list[str] = []
    # Lista de playlists enriquecidas para el .json (para el front BatchResults).
    # Para batches de 200-500 ISRCs con playlists típicas es asumible en RAM.
    all_playlists_enriched: list[dict] = []

    # Construir CSV y resumen JSON en un solo paso sobre el .jsonl
    with (
        open(jsonl_path, "r", encoding="utf-8") as fin,
        open(csv_path, "w", newline="", encoding="utf-8") as fcsv,
    ):
        writer = csv.DictWriter(fcsv, fieldnames=COLS, extrasaction="ignore")
        writer.writeheader()

        resumen_meta: dict[str, Any] = {}

        for line in fin:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue

            isrc = rec.get("isrc", "")
            status = rec.get("status", "")
            meta = rec.get("meta") or {}
            playlists = rec.get("playlists") or []

            if status == "found" and meta:
                meta_count += 1
                resumen_meta[isrc] = meta
            else:
                not_found.append(isrc)

            song_name = meta.get("song_name") or ""
            credit_name = meta.get("credit_name") or ""

            for pl in playlists:
                # Enriquecer con isrc, song_name y credit_name del contexto del registro
                enriched = {
                    "isrc": isrc,
                    "song_name": song_name,
                    "credit_name": credit_name,
                    **{k: pl.get(k) for k in COLS if k not in ("isrc", "song_name", "credit_name")},
                }
                all_playlists_enriched.append(enriched)
                writer.writerow(enriched)
                total_playlists += 1

    # Resumen JSON: incluye `playlists` para que el front (BatchResults)
    # pueda renderizar la tabla directamente desde este endpoint.
    resumen = {
        "job_id": job_id,
        "meta_count": meta_count,
        "total_playlists": total_playlists,
        "not_found": not_found,
        "not_found_count": len(not_found),
        "meta": resumen_meta,
        "playlists": all_playlists_enriched,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(resumen, f, ensure_ascii=False, indent=2)

    # XLSX: se construye desde el CSV para no re-parsear el .jsonl
    try:
        import pandas as pd
        df = pd.read_csv(csv_path, dtype=str)
        df.to_excel(str(xlsx_path), index=False, engine="openpyxl")
    except Exception as exc:
        logger.warning("jobs: no se pudo generar .xlsx para job %s: %s", job_id, exc)

    logger.info(
        "jobs: materializado job %s — %d canciones, %d playlists.",
        job_id, meta_count, total_playlists,
    )


def shutdown_pool() -> None:
    """Cierra el ThreadPoolExecutor sin bloquear el shutdown de uvicorn.

    cancel_futures=True descarta los jobs que aún no arrancaron (pending en cola).
    wait=False no espera a que los workers activos terminen sus llamadas HTTP
    de hasta 20 s: el proceso cierra en cuanto el sistema operativo lo permita.
    Los jobs en 'running' quedarán en ese estado hasta que _recover_running_jobs
    los marque como 'error' en el próximo arranque.
    """
    _POOL.shutdown(wait=False, cancel_futures=True)
    logger.info("jobs: pool de workers cerrado (cancel_futures=True, wait=False).")
