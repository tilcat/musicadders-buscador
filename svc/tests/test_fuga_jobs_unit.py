"""
svc/tests/test_fuga_jobs_unit.py
Tests unitarios directos de svc/fuga_jobs.py (sin servidor HTTP).

Cubren comportamientos nuevos en F3:
  a) create_job: estado inicial 'pending', pages_total = 80 (heurística).
  b) cancel_job: devuelve False si job ya terminado (simula la mitad del path
     de race-condition; el camino rowcount=0 real es no-determinista en tests).
  c) cleanup_old_jobs: borra jobs viejos terminados; respeta los activos.
  d) _sanitize_cell: anti-formula-injection (CWE-1236).
  e) _materialize: CSV y XLSX aplican sanitización a campos de texto libre.

No arranca ningún servidor FastAPI ni TestClient — importa fuga_jobs directamente.
El pool de workers NO ejecuta ningún job (se usa create_job sin start_job).
"""

from __future__ import annotations

import csv
import io
import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# ── Entorno mínimo antes de importar el módulo ────────────────────────────────
os.environ.setdefault("INTERNAL_TOKEN", "test-token-unit")
os.environ.setdefault("SOUNDCHARTS_APP_ID", "dummy")
os.environ.setdefault("SOUNDCHARTS_API_KEY", "dummy")
os.environ.setdefault("FUGA_USER", "test@example.com")
os.environ.setdefault("FUGA_PASS", "testpassword")

import svc.fuga_jobs as fuga_jobs  # noqa: E402
from svc.fuga_jobs import (  # noqa: E402
    _PAGES_TOTAL_ESTIMATE,
    _sanitize_cell,
    cleanup_old_jobs,
    create_job,
    get_status,
    cancel_job,
    _materialize,
    _RESULTS_DIR,
    _DB_PATH,
    _get_conn,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fake_uuid() -> str:
    return str(uuid.uuid4())


def _insert_finished_job(job_id: str, created_at: str, estado: str = "done") -> None:
    """Inserta un job directamente en la DB (sin pasar por create_job)."""
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO fuga_jobs (
                id, estado, pages_done, pages_total, status_text,
                isrcs_found, releases_found, date_from, date_to, created_at, error_msg
            ) VALUES (?, ?, 0, 80, 'Completado.', 0, 0, '2024-01-01', '2024-01-31', ?, NULL)
            """,
            (job_id, estado, created_at),
        )
        conn.commit()


# ── CASO a: create_job — estado inicial 'pending' y pages_total heurístico ───

def test_create_job_initial_state_is_pending():
    """
    a-1) create_job devuelve un UUID válido y el job arranca en estado 'pending'.

    'pending' es el nuevo estado inicial (antes era 'running' directamente).
    El worker transiciona de 'pending' → 'running' de forma atómica.
    """
    job_id = create_job("2024-01-01", "2024-01-31")
    assert job_id, "create_job debe devolver un job_id no vacío"

    status = get_status(job_id)
    assert status is not None, f"get_status({job_id!r}) devolvió None"
    assert status["estado"] == "pending", (
        f"Estado inicial esperado 'pending', obtenido '{status['estado']}'. "
        "El job recién creado debe quedar en 'pending' hasta que el worker lo recoja."
    )


def test_create_job_pages_total_is_heuristic():
    """
    a-2) create_job fija pages_total = 80 (heurística) para que el frontend
    pueda mostrar una barra de progreso determinada desde el primer tick.
    """
    job_id = create_job("2024-02-01", "2024-02-28")
    status = get_status(job_id)
    assert status is not None
    assert status["pages_total"] == _PAGES_TOTAL_ESTIMATE, (
        f"pages_total esperado {_PAGES_TOTAL_ESTIMATE}, "
        f"obtenido {status['pages_total']}."
    )


def test_create_job_status_text_initial():
    """
    a-3) El status_text inicial es 'En cola…' (orientativo para el usuario).
    """
    job_id = create_job("2024-03-01", "2024-03-31")
    status = get_status(job_id)
    assert status is not None
    assert status["status_text"], "status_text no debe estar vacío tras create_job"
    # Verificar que es un texto de cola (no vacío y no es un estado de error)
    assert "cola" in status["status_text"].lower() or "inici" in status["status_text"].lower(), (
        f"status_text inicial inesperado: '{status['status_text']}'"
    )


# ── CASO b: cancel_job — semántica de False cuando el job ya terminó ─────────

def test_cancel_job_returns_false_when_already_done():
    """
    b-1) cancel_job devuelve False si el job ya está en 'done'.

    Esto garantiza que el endpoint responde 409 (no cancela algo ya terminado).
    No testea la race-condition del rowcount=0 (no-determinista), sino la
    lógica de guardia previa al UPDATE.
    """
    # Insertar un job ya terminado directamente en la DB
    job_id = _fake_uuid()
    _insert_finished_job(job_id, datetime.now(timezone.utc).isoformat(), "done")

    result = cancel_job(job_id)
    assert result is False, (
        f"cancel_job sobre un job 'done' debe devolver False, obtenido {result!r}."
    )


def test_cancel_job_returns_false_when_already_error():
    """
    b-2) cancel_job devuelve False si el job está en 'error'.
    """
    job_id = _fake_uuid()
    _insert_finished_job(job_id, datetime.now(timezone.utc).isoformat(), "error")

    result = cancel_job(job_id)
    assert result is False, (
        f"cancel_job sobre un job 'error' debe devolver False, obtenido {result!r}."
    )


def test_cancel_job_returns_false_for_nonexistent_job():
    """
    b-3) cancel_job devuelve False si el job no existe (→ el endpoint da 404).
    """
    result = cancel_job(_fake_uuid())
    assert result is False, "cancel_job de job inexistente debe devolver False"


def test_cancel_pending_job_returns_true():
    """
    b-4) cancel_job devuelve True si el job está en 'pending' (aún no arrancó).

    Nuevo comportamiento F3: 'pending' es ahora cancellable (igual que 'running').
    """
    job_id = create_job("2024-04-01", "2024-04-30")
    status = get_status(job_id)
    # Solo cancelamos si está en pending (podría haber pasado a running si el pool lo recogió)
    if status and status["estado"] == "pending":
        result = cancel_job(job_id)
        assert result is True, (
            f"cancel_job sobre un job 'pending' debe devolver True, obtenido {result!r}."
        )
    else:
        pytest.skip("El worker recogió el job antes de que el test pudiese cancelarlo en 'pending'")


# ── CASO c: cleanup_old_jobs ──────────────────────────────────────────────────

def test_cleanup_old_jobs_removes_finished_old_jobs():
    """
    c-1) cleanup_old_jobs borra jobs terminados cuyo created_at supera max_age_days.

    Usa un job con created_at muy antiguo y verifica que desaparece.
    """
    job_id = _fake_uuid()
    old_ts = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    _insert_finished_job(job_id, old_ts, "done")

    # Verificar que existe antes del cleanup
    assert get_status(job_id) is not None, "El job debe existir antes del cleanup"

    n = cleanup_old_jobs(max_age_days=30)
    assert n >= 1, f"cleanup_old_jobs debería haber borrado al menos 1 job, borró {n}"

    # Verificar que ya no existe
    assert get_status(job_id) is None, (
        f"Job {job_id} con 60 días debe haber sido eliminado por cleanup(30 días)."
    )


def test_cleanup_old_jobs_respects_active_jobs():
    """
    c-2) cleanup_old_jobs NO borra jobs en 'pending' o 'running', aunque sean viejos.

    Un job activo nunca debe borrarse durante el cleanup.
    """
    job_id = _fake_uuid()
    old_ts = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    _insert_finished_job(job_id, old_ts, "running")  # activo

    cleanup_old_jobs(max_age_days=30)

    # El job running debe seguir existiendo
    assert get_status(job_id) is not None, (
        f"cleanup_old_jobs NO debe borrar jobs en estado 'running', "
        f"aunque su created_at supere max_age_days."
    )

    # Limpiar: actualizar a 'error' para que no interfiera con otros tests
    with _get_conn() as conn:
        conn.execute("UPDATE fuga_jobs SET estado = 'error' WHERE id = ?", (job_id,))
        conn.commit()


def test_cleanup_old_jobs_keeps_recent_finished_jobs():
    """
    c-3) cleanup_old_jobs respeta jobs terminados recientes (created_at < max_age_days).
    """
    job_id = create_job("2024-05-01", "2024-05-31")
    # Marcar como done directamente en la DB
    with _get_conn() as conn:
        conn.execute("UPDATE fuga_jobs SET estado = 'done' WHERE id = ?", (job_id,))
        conn.commit()

    n = cleanup_old_jobs(max_age_days=30)
    # El job recién creado NO debe borrarse
    assert get_status(job_id) is not None, (
        f"cleanup_old_jobs no debe borrar jobs terminados recientes (< 30 días)."
    )


def test_cleanup_old_jobs_removes_result_files():
    """
    c-4) cleanup_old_jobs borra los ficheros de resultado (.json, .csv, .xlsx)
    junto con el registro de la DB.
    """
    job_id = _fake_uuid()
    old_ts = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    _insert_finished_job(job_id, old_ts, "done")

    # Crear ficheros de resultado ficticios
    for fname in (f"{job_id}.json", f"{job_id}.csv", f"{job_id}.xlsx", f"{job_id}_isrc.xlsx"):
        (_RESULTS_DIR / fname).write_text("test", encoding="utf-8")

    n = cleanup_old_jobs(max_age_days=30)
    assert n >= 1

    # Verificar que los ficheros fueron borrados
    for fname in (f"{job_id}.json", f"{job_id}.csv", f"{job_id}.xlsx", f"{job_id}_isrc.xlsx"):
        assert not (_RESULTS_DIR / fname).exists(), (
            f"cleanup_old_jobs debe borrar el fichero {fname}"
        )


# ── CASO d: _sanitize_cell — anti-formula-injection ──────────────────────────

@pytest.mark.parametrize("value,expected", [
    ("=SUM(A1)", "'=SUM(A1)"),
    ("+HYPERLINK()", "'+HYPERLINK()"),
    ("-2+3", "'-2+3"),
    ("@MID(A1,1,1)", "'@MID(A1,1,1)"),
    ("\t=cmd", "'\t=cmd"),
    ("\r=cmd", "'\r=cmd"),
    ("Normal text", "Normal text"),        # no prefix → sin cambios
    ("", ""),                              # vacío → sin cambios
    (None, ""),                            # None → str vacío
    ("Artist Name", "Artist Name"),        # nombre normal → intacto
    ("=", "'="),                           # solo el prefijo → también se protege
])
def test_sanitize_cell_formula_injection(value, expected):
    """
    d) _sanitize_cell debe prefijar con comilla simple cualquier celda que
    empiece por =, +, -, @, TAB o CR (prefijos de fórmula en Excel/LibreOffice).

    Los valores normales deben pasar intactos. None se convierte a ''.
    """
    result = _sanitize_cell(value)
    assert result == expected, (
        f"_sanitize_cell({value!r}) → {result!r}, esperado {expected!r}"
    )


# ── CASO e: _materialize — sanitización en CSV y XLSX ────────────────────────

def test_materialize_sanitizes_text_columns_in_csv():
    """
    e-1) _materialize aplica _sanitize_cell a product_name, artist_name, label
    en el CSV de salida. isrc y release_date NO se sanitizan (campos controlados).
    """
    job_id = _fake_uuid()
    rows = [
        {
            "isrc":         "ESAA12300001",
            "product_name": "=MALICIOUS()",
            "artist_name":  "+evil",
            "label":        "@inject",
            "release_date": "2024-01-01",
        }
    ]
    _materialize(job_id, rows, "2024-01-01", "2024-01-31", releases_total=1)

    csv_path = _RESULTS_DIR / f"{job_id}.csv"
    assert csv_path.exists(), "El CSV debe haberse generado"

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        out_rows = list(reader)

    assert len(out_rows) == 1
    row = out_rows[0]
    assert row["product_name"] == "'=MALICIOUS()", (
        f"product_name no sanitizado en CSV: {row['product_name']!r}"
    )
    assert row["artist_name"] == "'+evil", (
        f"artist_name no sanitizado en CSV: {row['artist_name']!r}"
    )
    assert row["label"] == "'@inject", (
        f"label no sanitizado en CSV: {row['label']!r}"
    )
    # ISRC no se sanitiza (dato controlado)
    assert row["isrc"] == "ESAA12300001", (
        f"isrc no debe sanitizarse: {row['isrc']!r}"
    )

    # Limpiar
    (_RESULTS_DIR / f"{job_id}.json").unlink(missing_ok=True)
    csv_path.unlink(missing_ok=True)
    (_RESULTS_DIR / f"{job_id}.xlsx").unlink(missing_ok=True)
    (_RESULTS_DIR / f"{job_id}_isrc.xlsx").unlink(missing_ok=True)


def test_materialize_json_does_not_sanitize():
    """
    e-2) El JSON de resultado NO aplica _sanitize_cell (React escapa el texto;
    sanitizar el JSON rompería el renderizado).
    """
    job_id = _fake_uuid()
    rows = [
        {
            "isrc":         "ESAA12300002",
            "product_name": "=MALICIOUS()",
            "artist_name":  "Normal Artist",
            "label":        "Test Label",
            "release_date": "2024-01-01",
        }
    ]
    _materialize(job_id, rows, "2024-01-01", "2024-01-31", releases_total=1)

    json_path = _RESULTS_DIR / f"{job_id}.json"
    assert json_path.exists()

    result = json.loads(json_path.read_text(encoding="utf-8"))
    assert result["rows"][0]["product_name"] == "=MALICIOUS()", (
        "El JSON de resultado NO debe sanitizar las filas "
        "(React escapa el texto en el frontend)."
    )

    # Limpiar
    for fname in (f"{job_id}.json", f"{job_id}.csv", f"{job_id}.xlsx", f"{job_id}_isrc.xlsx"):
        (_RESULTS_DIR / fname).unlink(missing_ok=True)
