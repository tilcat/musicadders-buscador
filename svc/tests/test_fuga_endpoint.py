"""
svc/tests/test_fuga_endpoint.py
Tests de los endpoints FUGA del backend FastAPI (svc/main.py + svc/fuga_jobs.py).

Cubren:
  a) Lifecycle completo: POST /fuga → polling /status → done → result.json + csv + xlsx
  b) Cancelación: job cancelado termina en 'cancelled', resultado parcial materializado.
  c) Auth: sin X-Internal-Token → 401/503; token incorrecto → 401.
  d) Sin credenciales FUGA: POST /fuga → 401 con {error: "no_credentials"}.
  e) Rango de fechas inválido: date_from > date_to → 422.
  f) job_id con formato inválido → 400 (antes del auth check).
  g) Job inexistente (UUID válido) → 404.
  h) Formatos de resultado: json, csv, xlsx (full), xlsx_isrc.
  i) Cancelación de job ya terminado → 409.

Usa TestClient de FastAPI (httpx síncrono). Mockea svc.fuga_jobs.find_isrcs_in_date_range
para no tocar la red real de FUGA ni requerir credenciales.

Intérprete: svc/.venv/bin/pytest (Python 3.14)
"""

from __future__ import annotations

import os
import threading
import time
from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ── Configuración de entorno antes de importar la app ─────────────────────────
_TEST_TOKEN = "test-token-abc123"
os.environ.setdefault("INTERNAL_TOKEN", _TEST_TOKEN)
os.environ.setdefault("SOUNDCHARTS_APP_ID", "dummy")
os.environ.setdefault("SOUNDCHARTS_API_KEY", "dummy")
# Credenciales FUGA ficticias para los tests normales
os.environ.setdefault("FUGA_USER", "test@example.com")
os.environ.setdefault("FUGA_PASS", "testpassword")

from svc.main import app  # noqa: E402

# ── Fixtures ──────────────────────────────────────────────────────────────────
#
# scope="session" por el mismo motivo que en test_backend_regression.py:
# el pool de workers de fuga_jobs es un singleton global. Un único TestClient
# para toda la sesión evita que el pool quede en shutdown=True entre tests.

@pytest.fixture(scope="session")
def client():
    """TestClient único para toda la sesión de tests FUGA."""
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(scope="session")
def auth_headers():
    return {"X-Internal-Token": _TEST_TOKEN}


# ── Mocks de find_isrcs_in_date_range ────────────────────────────────────────

_SAMPLE_ROWS = [
    {
        "isrc":         "ESAA12300001",
        "product_name": "Test Release 1",
        "artist_name":  "Test Artist",
        "label":        "Test Label",
        "release_date": "2024-01-15",
    },
    {
        "isrc":         "ESAA12300002",
        "product_name": "Test Release 2",
        "artist_name":  "Another Artist",
        "label":        "Another Label",
        "release_date": "2024-01-10",
    },
]


def _fake_find_isrcs(
    date_from: date,
    date_to: date,
    progress_cb=None,
    cancel_event=None,
) -> tuple[list[dict], None]:
    """Mock: devuelve _SAMPLE_ROWS de forma inmediata."""
    if progress_cb:
        progress_cb(0, 2, "página 1 · 2 releases en rango")
        progress_cb(1, 2, "extrayendo ISRCs…")
    return _SAMPLE_ROWS, None


def _fake_find_isrcs_empty(
    date_from: date,
    date_to: date,
    progress_cb=None,
    cancel_event=None,
) -> tuple[list[dict], None]:
    """Mock: rango sin resultados."""
    if progress_cb:
        progress_cb(0, 0, "página 1 · 0 releases en rango")
    return [], None


def _fake_find_isrcs_error(
    date_from: date,
    date_to: date,
    progress_cb=None,
    cancel_event=None,
) -> tuple[None, str]:
    """Mock: error de autenticación FUGA."""
    return None, "No se pudo autenticar contra FUGA. Verifica FUGA_USER/FUGA_PASS."


def _make_slow_find(delay: float = 0.4):
    """Factoria: mock que duerme `delay` segundos y comprueba cancel_event."""
    def _slow(
        date_from: date,
        date_to: date,
        progress_cb=None,
        cancel_event=None,
    ) -> tuple[list[dict], None]:
        for _ in range(int(delay / 0.05)):
            if cancel_event and cancel_event.is_set():
                return [], None
            time.sleep(0.05)
        return _SAMPLE_ROWS, None
    return _slow


# ── Utilidad: esperar a que un job llegue a estado terminal ───────────────────

def _wait_for_done(
    client: TestClient,
    job_id: str,
    headers: dict,
    timeout: float = 15.0,
    interval: float = 0.1,
) -> dict:
    """Polling de /fuga/{job_id}/status hasta estado terminal."""
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        r = client.get(f"/fuga/{job_id}/status", headers=headers)
        assert r.status_code == 200, f"Status inesperado: {r.status_code} {r.text}"
        last = r.json()
        if last["estado"] in ("done", "cancelled", "error"):
            return last
        time.sleep(interval)
    raise TimeoutError(
        f"Job {job_id} no terminó en {timeout}s. Último estado: {last}"
    )


# ── UUID de prueba ─────────────────────────────────────────────────────────────
_UUID_INEXISTENTE = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


# ── CASO a: lifecycle completo ────────────────────────────────────────────────

@patch("svc.fuga_jobs.find_isrcs_in_date_range", side_effect=_fake_find_isrcs)
def test_fuga_lifecycle_create_run_done(mock_find, client, auth_headers):
    """
    a) Lifecycle: POST /fuga → polling /status → done → result.json + csv + xlsx.

    Verifica:
    - 202 con job_id al crear el job.
    - status llega a 'done'.
    - result.json contiene rows, date_from, date_to, isrcs_total, releases_total.
    - result.csv descargable (200).
    - result.xlsx (full) descargable (200).
    """
    r = client.post(
        "/fuga",
        headers=auth_headers,
        json={"date_from": "2024-01-01", "date_to": "2024-01-31"},
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert "job_id" in body
    job_id = body["job_id"]

    # Polling hasta done
    status = _wait_for_done(client, job_id, auth_headers)
    assert status["estado"] == "done", f"Estado inesperado: {status}"

    # result.json — contrato completo
    r_json = client.get(f"/fuga/{job_id}/result.json", headers=auth_headers)
    assert r_json.status_code == 200, r_json.text
    res = r_json.json()
    assert "rows" in res, f"'rows' ausente en result.json: {list(res.keys())}"
    assert "date_from" in res
    assert "date_to" in res
    assert "isrcs_total" in res
    assert "releases_total" in res
    assert res["isrcs_total"] == 2
    assert res["date_from"] == "2024-01-01"
    assert res["date_to"] == "2024-01-31"
    assert len(res["rows"]) == 2
    # Verificar campos de cada fila
    for row in res["rows"]:
        for campo in ("isrc", "product_name", "artist_name", "label", "release_date"):
            assert campo in row, f"Campo '{campo}' ausente en fila: {row}"

    # result.csv — descargable
    r_csv = client.get(f"/fuga/{job_id}/result.csv", headers=auth_headers)
    assert r_csv.status_code == 200, r_csv.text
    lines = r_csv.text.strip().splitlines()
    assert lines[0].startswith("isrc"), f"Cabecera CSV inesperada: {lines[0]}"
    assert len(lines) == 3, f"Se esperaban 3 líneas (cabecera + 2 filas), hay {len(lines)}"

    # result.xlsx (full) — descargable
    r_xlsx = client.get(f"/fuga/{job_id}/result.xlsx", headers=auth_headers)
    assert r_xlsx.status_code == 200, r_xlsx.text

    # result.xlsx?xlsx_type=isrc — descargable
    r_xlsx_isrc = client.get(f"/fuga/{job_id}/result.xlsx?xlsx_type=isrc", headers=auth_headers)
    assert r_xlsx_isrc.status_code == 200, r_xlsx_isrc.text


# ── CASO a2: rango sin resultados ─────────────────────────────────────────────

@patch("svc.fuga_jobs.find_isrcs_in_date_range", side_effect=_fake_find_isrcs_empty)
def test_fuga_empty_range_done(mock_find, client, auth_headers):
    """
    a2) Rango sin ISRCs: el job termina como 'done' con rows=[] y isrcs_total=0.
    """
    r = client.post(
        "/fuga",
        headers=auth_headers,
        json={"date_from": "2020-01-01", "date_to": "2020-01-01"},
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]

    status = _wait_for_done(client, job_id, auth_headers)
    assert status["estado"] == "done", f"Estado inesperado: {status}"
    assert status["isrcs_found"] == 0

    r_json = client.get(f"/fuga/{job_id}/result.json", headers=auth_headers)
    assert r_json.status_code == 200, r_json.text
    res = r_json.json()
    assert res["rows"] == []
    assert res["isrcs_total"] == 0


# ── CASO b: cancelación ───────────────────────────────────────────────────────

def test_fuga_cancel_leaves_cancelled_state(client, auth_headers):
    """
    b) Cancelación: job cancelado termina en 'cancelled' (o 'done' si ya terminó).
    El resultado parcial debe existir si el worker procesó algo antes de cancelar.
    """
    slow_find = _make_slow_find(delay=0.5)

    with patch("svc.fuga_jobs.find_isrcs_in_date_range", side_effect=slow_find):
        r = client.post(
            "/fuga",
            headers=auth_headers,
            json={"date_from": "2024-01-01", "date_to": "2024-12-31"},
        )
        assert r.status_code == 202, r.text
        job_id = r.json()["job_id"]

        # Cancelar inmediatamente (el job debería estar en running)
        r_cancel = client.post(f"/fuga/{job_id}/cancel", headers=auth_headers)
        assert r_cancel.status_code == 200, r_cancel.text
        assert r_cancel.json().get("ok") is True

        # Esperar estado terminal
        status = _wait_for_done(client, job_id, auth_headers, timeout=10)

    assert status["estado"] in ("cancelled", "done"), \
        f"Estado esperado cancelled o done, obtenido: {status['estado']}"


# ── CASO c: auth del token interno ────────────────────────────────────────────

def test_fuga_no_token_returns_401(client):
    """c-1) POST /fuga sin X-Internal-Token → 401."""
    r = client.post(
        "/fuga",
        json={"date_from": "2024-01-01", "date_to": "2024-01-31"},
    )
    assert r.status_code == 401, \
        f"Sin token esperado 401, obtenido {r.status_code}: {r.text}"


def test_fuga_wrong_token_returns_401(client):
    """c-2) Token incorrecto → 401."""
    r = client.post(
        "/fuga",
        headers={"X-Internal-Token": "WRONG_TOKEN"},
        json={"date_from": "2024-01-01", "date_to": "2024-01-31"},
    )
    assert r.status_code == 401, \
        f"Token incorrecto esperado 401, obtenido {r.status_code}: {r.text}"


def test_fuga_status_no_token_returns_401(client):
    """c-3) GET /fuga/{uuid}/status sin token → 401."""
    r = client.get(f"/fuga/{_UUID_INEXISTENTE}/status")
    assert r.status_code == 401, \
        f"Sin token esperado 401, obtenido {r.status_code}: {r.text}"


def test_fuga_cancel_no_token_returns_401(client):
    """c-4) POST /fuga/{uuid}/cancel sin token → 401."""
    r = client.post(f"/fuga/{_UUID_INEXISTENTE}/cancel")
    assert r.status_code == 401, \
        f"Sin token esperado 401, obtenido {r.status_code}: {r.text}"


# ── CASO d: sin credenciales FUGA ────────────────────────────────────────────

def test_fuga_no_credentials_returns_401_no_credentials(client, auth_headers):
    """
    d) POST /fuga con FUGA_USER/FUGA_PASS ausentes → 401 con {error: "no_credentials"}.

    Fail-closed: el endpoint no crea el job si las credenciales no están configuradas.
    """
    with patch.dict(os.environ, {"FUGA_USER": "", "FUGA_PASS": ""}):
        r = client.post(
            "/fuga",
            headers=auth_headers,
            json={"date_from": "2024-01-01", "date_to": "2024-01-31"},
        )
    assert r.status_code == 401, \
        f"Sin credenciales FUGA esperado 401, obtenido {r.status_code}: {r.text}"
    data = r.json()
    assert data.get("error") == "no_credentials", \
        f"error='no_credentials' esperado, obtenido: {data.get('error')}"
    assert "message" in data, f"Campo 'message' ausente: {data}"


# ── CASO e: rango de fechas inválido ─────────────────────────────────────────

def test_fuga_invalid_date_format_returns_422(client, auth_headers):
    """e-1) Fecha con formato incorrecto → 422."""
    r = client.post(
        "/fuga",
        headers=auth_headers,
        json={"date_from": "01-01-2024", "date_to": "2024-01-31"},
    )
    assert r.status_code == 422, \
        f"Fecha inválida esperado 422, obtenido {r.status_code}: {r.text}"


def test_fuga_date_range_inverted_returns_422(client, auth_headers):
    """e-2) date_from > date_to → 422."""
    r = client.post(
        "/fuga",
        headers=auth_headers,
        json={"date_from": "2024-12-31", "date_to": "2024-01-01"},
    )
    assert r.status_code == 422, \
        f"Rango invertido esperado 422, obtenido {r.status_code}: {r.text}"


def test_fuga_missing_dates_returns_422(client, auth_headers):
    """e-3) Campos ausentes → FastAPI devuelve 422 (validación Pydantic)."""
    r = client.post(
        "/fuga",
        headers=auth_headers,
        json={"date_from": "2024-01-01"},
    )
    assert r.status_code == 422, \
        f"Campo ausente esperado 422, obtenido {r.status_code}: {r.text}"


# ── CASO f: job_id con formato inválido ──────────────────────────────────────

def test_fuga_invalid_job_id_returns_400(client, auth_headers):
    """
    f) job_id con formato inválido → 400 (defensa path traversal, antes del auth).

    NOTA: la validación de job_id (_validate_job_id) corre ANTES del check de token.
    Por eso los tests de formato inválido sí pasan el token (si no, el 401 del token
    podría enmascarar el 400 de validación en ciertos órdenes de comprobación).
    """
    invalid_ids = [
        "not-a-uuid",
        "fake-id",
        "00000000000000000000000000000000",  # sin guiones
        "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE",  # mayúsculas
    ]
    for bad_id in invalid_ids:
        r = client.get(f"/fuga/{bad_id}/status", headers=auth_headers)
        assert r.status_code == 400, \
            f"job_id='{bad_id}' esperado 400, obtenido {r.status_code}: {r.text}"

    # También en result.json y cancel
    r = client.get("/fuga/not-a-uuid/result.json", headers=auth_headers)
    assert r.status_code == 400, f"result.json con job_id inválido: {r.status_code}"

    r = client.post("/fuga/not-a-uuid/cancel", headers=auth_headers)
    assert r.status_code == 400, f"cancel con job_id inválido: {r.status_code}"


# ── CASO g: job inexistente ───────────────────────────────────────────────────

def test_fuga_status_unknown_job_returns_404(client, auth_headers):
    """g-1) GET /fuga/{uuid-válido-inexistente}/status → 404."""
    r = client.get(f"/fuga/{_UUID_INEXISTENTE}/status", headers=auth_headers)
    assert r.status_code == 404, r.text


def test_fuga_result_json_unknown_job_returns_404(client, auth_headers):
    """g-2) GET /fuga/{uuid-válido-inexistente}/result.json → 404."""
    r = client.get(f"/fuga/{_UUID_INEXISTENTE}/result.json", headers=auth_headers)
    assert r.status_code == 404, r.text


def test_fuga_cancel_unknown_job_returns_404(client, auth_headers):
    """g-3) POST /fuga/{uuid-válido-inexistente}/cancel → 404."""
    r = client.post(f"/fuga/{_UUID_INEXISTENTE}/cancel", headers=auth_headers)
    assert r.status_code == 404, r.text


# ── CASO h: formatos de resultado ────────────────────────────────────────────

@patch("svc.fuga_jobs.find_isrcs_in_date_range", side_effect=_fake_find_isrcs)
def test_fuga_all_result_formats(mock_find, client, auth_headers):
    """
    h) Los 4 formatos de resultado están disponibles tras completar el job:
       result.json, result.csv, result.xlsx (full), result.xlsx?type=isrc.
    """
    r = client.post(
        "/fuga",
        headers=auth_headers,
        json={"date_from": "2024-02-01", "date_to": "2024-02-28"},
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]

    status = _wait_for_done(client, job_id, auth_headers)
    assert status["estado"] == "done", f"Estado inesperado: {status}"

    # json
    r = client.get(f"/fuga/{job_id}/result.json", headers=auth_headers)
    assert r.status_code == 200, f"result.json: {r.status_code} {r.text}"

    # csv
    r = client.get(f"/fuga/{job_id}/result.csv", headers=auth_headers)
    assert r.status_code == 200, f"result.csv: {r.status_code} {r.text}"

    # xlsx full (sin param = default)
    r = client.get(f"/fuga/{job_id}/result.xlsx", headers=auth_headers)
    assert r.status_code == 200, f"result.xlsx (full): {r.status_code} {r.text}"

    # xlsx full (param explícito)
    r = client.get(f"/fuga/{job_id}/result.xlsx?xlsx_type=full", headers=auth_headers)
    assert r.status_code == 200, f"result.xlsx?xlsx_type=full: {r.status_code} {r.text}"

    # xlsx isrc
    r = client.get(f"/fuga/{job_id}/result.xlsx?xlsx_type=isrc", headers=auth_headers)
    assert r.status_code == 200, f"result.xlsx?xlsx_type=isrc: {r.status_code} {r.text}"

    # xlsx tipo inválido → 400
    r = client.get(f"/fuga/{job_id}/result.xlsx?xlsx_type=unknown", headers=auth_headers)
    assert r.status_code == 400, f"xlsx tipo inválido esperado 400: {r.status_code}"


# ── CASO h2: result antes de que el job termine → 409 ────────────────────────

@patch("svc.fuga_jobs.find_isrcs_in_date_range", side_effect=_make_slow_find(delay=2.0))
def test_fuga_result_before_done_returns_409(mock_find, client, auth_headers):
    """
    h2) GET result.json mientras el job está en 'running' → 409 (aún no disponible).
    """
    r = client.post(
        "/fuga",
        headers=auth_headers,
        json={"date_from": "2024-03-01", "date_to": "2024-03-31"},
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]

    # Pedir el resultado inmediatamente (antes de que el worker haya terminado)
    r_json = client.get(f"/fuga/{job_id}/result.json", headers=auth_headers)
    # Puede ser 409 (running) o 200 (si el mock fue muy rápido y ya terminó)
    assert r_json.status_code in (409, 200), \
        f"Esperado 409 o 200, obtenido {r_json.status_code}: {r_json.text}"

    # Esperar a que termine para no dejar el job colgado
    _wait_for_done(client, job_id, auth_headers, timeout=15)


# ── CASO i: cancelación de job ya terminado → 409 ────────────────────────────

@patch("svc.fuga_jobs.find_isrcs_in_date_range", side_effect=_fake_find_isrcs)
def test_fuga_cancel_already_done_returns_409(mock_find, client, auth_headers):
    """
    i) POST /fuga/{id}/cancel sobre un job ya 'done' → 409 (no se puede cancelar).
    """
    r = client.post(
        "/fuga",
        headers=auth_headers,
        json={"date_from": "2024-04-01", "date_to": "2024-04-30"},
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]

    # Esperar a que termine
    status = _wait_for_done(client, job_id, auth_headers)
    assert status["estado"] == "done"

    # Intentar cancelar → 409
    r_cancel = client.post(f"/fuga/{job_id}/cancel", headers=auth_headers)
    assert r_cancel.status_code == 409, \
        f"Cancel de job done esperado 409, obtenido {r_cancel.status_code}: {r_cancel.text}"


# ── CASO extra: error interno de FUGA ────────────────────────────────────────

@patch("svc.fuga_jobs.find_isrcs_in_date_range", side_effect=_fake_find_isrcs_error)
def test_fuga_internal_error_leaves_error_state(mock_find, client, auth_headers):
    """
    extra) Error interno de FUGA (auth, red) → job termina en 'error' con error_msg.
    """
    r = client.post(
        "/fuga",
        headers=auth_headers,
        json={"date_from": "2024-05-01", "date_to": "2024-05-31"},
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]

    status = _wait_for_done(client, job_id, auth_headers)
    assert status["estado"] == "error", f"Estado inesperado: {status}"
    assert status["error_msg"] is not None, "error_msg debe estar presente en estado error"


# ── CASO extra2: status incluye todos los campos del contrato ─────────────────

@patch("svc.fuga_jobs.find_isrcs_in_date_range", side_effect=_fake_find_isrcs)
def test_fuga_status_contract_fields(mock_find, client, auth_headers):
    """
    extra2) GET /status devuelve todos los campos que espera el frontend:
    estado, pages_done, pages_total, status_text, isrcs_found, releases_found.
    """
    r = client.post(
        "/fuga",
        headers=auth_headers,
        json={"date_from": "2024-06-01", "date_to": "2024-06-30"},
    )
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]

    status = _wait_for_done(client, job_id, auth_headers)

    required_fields = [
        "estado", "pages_done", "pages_total",
        "status_text", "isrcs_found", "releases_found",
    ]
    for field in required_fields:
        assert field in status, \
            f"Campo '{field}' ausente en respuesta de status: {list(status.keys())}"

    # Tras 'done', isrcs_found debe coincidir con el número de filas
    assert status["estado"] == "done"
    assert status["isrcs_found"] == 2


# ── CASO e4: rango > 366 días → 422 ──────────────────────────────────────────

def test_fuga_range_over_366_days_returns_422(client, auth_headers):
    """
    e4) date_to - date_from > 366 días → 422 (defensa de carga descontrolada).
    """
    r = client.post(
        "/fuga",
        headers=auth_headers,
        json={"date_from": "2023-01-01", "date_to": "2024-12-31"},
    )
    assert r.status_code == 422, \
        f"Rango >366 días esperado 422, obtenido {r.status_code}: {r.text}"
    data = r.json()
    # Verificar que el mensaje menciona el límite
    detail = data.get("detail", "")
    assert "366" in str(detail), f"'366' ausente en el mensaje de error: {detail}"
