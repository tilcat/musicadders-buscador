"""
svc/tests/test_spotify_jobs.py
Tests del backend de playlist Spotify (F4): svc/spotify_jobs.py + endpoints /playlist/...

Cubren:
  a) Lifecycle completo: POST /playlist → polling /status → done → result.json + not_found.csv
  b) ISRCs no encontrados: not_found_isrcs en result.json y CSV descargable
  c) Auth 401 — no central token: POST /playlist → 401 {error: "not_configured"}
  d) Auth 401 — token incorrecto / ausente → 401
  e) Setup admin-only: GET /playlist/setup/status sin SPOTIFY_CENTRAL_ADMINS → 403;
     email no-admin → 403
  f) State OAuth inválido: POST /playlist/setup/exchange con state inválido → 400
  g) ISRCs vacíos / name vacío → 422
  h) Cooldown dinámico: estado 'cooldown' cuando cooldown_until es futuro
  i) Cancelación de job ya terminado → 409
  j) job_id con formato inválido → 400
  k) Job inexistente (UUID válido) → 404
  l) cancel_job directo: False para job terminado (unit test del módulo)
  m) cleanup_old_jobs: borra jobs finalizados viejos (unit test del módulo)

Mockea svc.spotify_jobs.{resolve_isrcs, create_playlist, add_tracks_to_playlist} y
svc.spotify.has_central_token para no requerir cuenta real de Spotify ni hacer
peticiones reales a la API.

NUNCA se conecta a la API real de Spotify.
"""

from __future__ import annotations

import csv
import io
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ── Entorno mínimo antes de importar la app ───────────────────────────────────
# IMPORTANTE: setdefault NO sobrescribe si otra fixture ya lo configuró (ej.
# test_fuga_endpoint.py). Leer el token efectivo después de setdefault para que
# los headers de los tests siempre coincidan con lo que hay en el entorno.
os.environ.setdefault("INTERNAL_TOKEN",       "test-internal-token-spotify-f4")
os.environ.setdefault("SOUNDCHARTS_APP_ID",   "dummy")
os.environ.setdefault("SOUNDCHARTS_API_KEY",  "dummy")
os.environ.setdefault("FUGA_USER",            "test@example.com")
os.environ.setdefault("FUGA_PASS",            "testpassword")

_TEST_TOKEN  = os.environ["INTERNAL_TOKEN"]   # token efectivo (puede haber sido fijado por otro test file)
_ADMIN_EMAIL = "spotifyadmin@test.com"
# SPOTIFY_CENTRAL_ADMINS se controla por test con patch.dict para no contaminar
# otros módulos que lo leen al importarse.

from svc.main import app                          # noqa: E402
import svc.spotify_jobs as spotify_jobs           # noqa: E402
from svc.spotify_jobs import _get_conn, _RESULTS_DIR  # noqa: E402

# ── Fixtures ──────────────────────────────────────────────────────────────────
#
# scope="session": el pool de workers de spotify_jobs es un singleton global;
# un único TestClient evita que el pool quede en shutdown=True entre tests.

@pytest.fixture(scope="session")
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(scope="session")
def auth_headers():
    return {"X-Internal-Token": _TEST_TOKEN}


@pytest.fixture(scope="session")
def admin_headers():
    return {
        "X-Internal-Token": _TEST_TOKEN,
        "X-User-Email":      _ADMIN_EMAIL,
    }


# ── Mocks de las funciones Spotify ────────────────────────────────────────────

_SAMPLE_ISRCS = ["USRC11300001", "USRC11300002", "USRC11300003"]

_FAKE_URIS = [
    "spotify:track:1aaaaaaaaaaaaaaaaaaaaa",
    "spotify:track:2aaaaaaaaaaaaaaaaaaaaa",
    "spotify:track:3aaaaaaaaaaaaaaaaaaaaa",
]


def _fake_resolve_all(isrcs, progress_cb=None, cooldown_cb=None, cancel_event=None):
    """Mock resolve_isrcs: todos los ISRCs se resuelven a URI."""
    if progress_cb:
        progress_cb(len(isrcs), len(isrcs), 0, "Todos los ISRCs resueltos.")
    return {"uris": _FAKE_URIS[: len(isrcs)], "not_found": [], "errors": []}


def _fake_resolve_with_not_found(isrcs, progress_cb=None, cooldown_cb=None, cancel_event=None):
    """Mock resolve_isrcs: resuelve los primeros 2, el resto → not_found."""
    found    = isrcs[:2]
    not_found = isrcs[2:]
    if progress_cb:
        progress_cb(len(found), len(isrcs), len(not_found), "Resolución parcial.")
    return {"uris": _FAKE_URIS[: len(found)], "not_found": not_found, "errors": []}


def _fake_create_playlist(name, description, public):
    """Mock create_playlist: devuelve estructura mínima esperada por el worker."""
    return {
        "id":             "playlist_test_fake_id",
        "name":           name,
        "external_urls":  {"spotify": "https://open.spotify.com/playlist/playlist_test_fake_id"},
    }


def _fake_add_tracks(playlist_id, uris, progress_cb=None, cancel_event=None):
    """Mock add_tracks_to_playlist: llama progress_cb y devuelve dict {added, failed}."""
    if progress_cb:
        progress_cb(len(uris), len(uris))
    return {"added": len(uris), "failed": 0}


def _fake_add_tracks_all_fail(playlist_id, uris, progress_cb=None, cancel_event=None):
    """Mock add_tracks_to_playlist: simula token expirado → added=0, failed=all."""
    return {"added": 0, "failed": len(uris)}


def _make_slow_resolve(delay: float = 0.4):
    """Factoría: mock de resolve_isrcs que duerme y respeta cancel_event."""
    def _slow(isrcs, progress_cb=None, cooldown_cb=None, cancel_event=None):
        steps = int(delay / 0.05)
        for _ in range(steps):
            if cancel_event and cancel_event.is_set():
                return {"uris": [], "not_found": isrcs, "errors": []}
            time.sleep(0.05)
        if progress_cb:
            progress_cb(len(isrcs), len(isrcs), 0, "Resueltos.")
        return {"uris": _FAKE_URIS[: len(isrcs)], "not_found": [], "errors": []}
    return _slow


# ── Utilidad: esperar estado terminal ─────────────────────────────────────────

def _wait_for_done(
    client: TestClient,
    job_id: str,
    headers: dict,
    timeout: float = 15.0,
    interval: float = 0.1,
) -> dict:
    """Polling de /playlist/{job_id}/status hasta estado terminal."""
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        r = client.get(f"/playlist/{job_id}/status", headers=headers)
        assert r.status_code == 200, f"Status inesperado {r.status_code}: {r.text}"
        last = r.json()
        if last["estado"] in ("done", "cancelled", "error"):
            return last
        time.sleep(interval)
    raise TimeoutError(
        f"Job {job_id} no terminó en {timeout}s. Último estado: {last}"
    )


# UUID para pruebas de job inexistente
_UUID_INEXISTENTE = "ffffffff-0000-4000-8000-000000000001"


# ── CASO a: lifecycle completo ────────────────────────────────────────────────

def test_playlist_lifecycle_done(client, auth_headers):
    """
    a) Lifecycle: POST /playlist → polling /status → done → result.json + not_found.csv.

    Verifica:
    - 202 con job_id al crear el job.
    - status llega a 'done'.
    - result.json tiene todos los campos del contrato.
    - not_found.csv es descargable (0 filas no encontradas).
    """
    with (
        patch("svc.spotify.has_central_token", return_value=True),
        patch("svc.spotify_jobs.resolve_isrcs",          side_effect=_fake_resolve_all),
        patch("svc.spotify_jobs.create_playlist",         side_effect=_fake_create_playlist),
        patch("svc.spotify_jobs.add_tracks_to_playlist",  side_effect=_fake_add_tracks),
    ):
        r = client.post(
            "/playlist",
            headers=auth_headers,
            json={
                "isrcs":       _SAMPLE_ISRCS,
                "name":        "Test Playlist",
                "description": "Descripción de prueba",
                "public":      False,
            },
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert "job_id" in body, f"'job_id' ausente en respuesta: {body}"
        job_id = body["job_id"]

        # Polling hasta done
        status = _wait_for_done(client, job_id, auth_headers)

    assert status["estado"] == "done", f"Estado inesperado: {status}"

    # result.json — contrato completo
    r_json = client.get(f"/playlist/{job_id}/result.json", headers=auth_headers)
    assert r_json.status_code == 200, r_json.text
    res = r_json.json()
    for campo in ("playlist_url", "playlist_name", "tracks_added", "not_found_isrcs", "total_isrcs"):
        assert campo in res, f"Campo '{campo}' ausente en result.json: {list(res.keys())}"
    assert res["total_isrcs"]    == len(_SAMPLE_ISRCS)
    assert res["tracks_added"]   == len(_SAMPLE_ISRCS)
    assert res["not_found_isrcs"] == []
    assert "playlist_test_fake_id" in res["playlist_url"]

    # not_found.csv — descargable (0 filas)
    r_csv = client.get(f"/playlist/{job_id}/result/not_found.csv", headers=auth_headers)
    assert r_csv.status_code == 200, r_csv.text
    lines = r_csv.text.strip().splitlines()
    assert lines[0].upper() == "ISRC", f"Cabecera CSV inesperada: {lines[0]}"
    assert len(lines) == 1, f"Esperada solo cabecera (0 not-found), hay {len(lines)} líneas"


# ── CASO b: ISRCs no encontrados ──────────────────────────────────────────────

def test_playlist_not_found_isrcs(client, auth_headers):
    """
    b) ISRCs no encontrados aparecen en result.json y en not_found.csv.

    El worker recibe 3 ISRCs; los últimos (de índice 2 en adelante) no se resuelven.
    """
    with (
        patch("svc.spotify.has_central_token", return_value=True),
        patch("svc.spotify_jobs.resolve_isrcs",         side_effect=_fake_resolve_with_not_found),
        patch("svc.spotify_jobs.create_playlist",        side_effect=_fake_create_playlist),
        patch("svc.spotify_jobs.add_tracks_to_playlist", side_effect=_fake_add_tracks),
    ):
        r = client.post(
            "/playlist",
            headers=auth_headers,
            json={
                "isrcs":  _SAMPLE_ISRCS,
                "name":   "Partial Playlist",
                "public": False,
            },
        )
        assert r.status_code == 202, r.text
        job_id = r.json()["job_id"]

        status = _wait_for_done(client, job_id, auth_headers)

    assert status["estado"] == "done", f"Estado inesperado: {status}"
    assert status["not_found"] >= 1, "Debe haber al menos 1 ISRC no encontrado"

    r_json = client.get(f"/playlist/{job_id}/result.json", headers=auth_headers)
    assert r_json.status_code == 200, r_json.text
    res = r_json.json()
    not_found = res["not_found_isrcs"]
    assert isinstance(not_found, list)
    assert len(not_found) >= 1, f"not_found_isrcs vacío inesperado: {res}"
    # El tercer ISRC debería estar en not_found
    assert _SAMPLE_ISRCS[2] in not_found, (
        f"{_SAMPLE_ISRCS[2]} debería estar en not_found_isrcs: {not_found}"
    )

    # not_found.csv contiene los ISRCs no encontrados
    r_csv = client.get(f"/playlist/{job_id}/result/not_found.csv", headers=auth_headers)
    assert r_csv.status_code == 200, r_csv.text
    reader = csv.DictReader(io.StringIO(r_csv.text))
    csv_isrcs = [row["ISRC"] for row in reader]
    assert _SAMPLE_ISRCS[2] in csv_isrcs, (
        f"{_SAMPLE_ISRCS[2]} debería estar en el CSV not_found: {csv_isrcs}"
    )


# ── CASO c: auth 401 — no hay cuenta central ──────────────────────────────────

def test_playlist_no_central_token_returns_401_not_configured(client, auth_headers):
    """
    c) POST /playlist sin cuenta central → 401 con {error: "not_configured"}.

    Fail-closed: el endpoint no crea el job si has_central_token() devuelve False.
    """
    with patch("svc.spotify.has_central_token", return_value=False):
        r = client.post(
            "/playlist",
            headers=auth_headers,
            json={"isrcs": _SAMPLE_ISRCS, "name": "Playlist"},
        )
    assert r.status_code == 401, (
        f"Sin cuenta central esperado 401, obtenido {r.status_code}: {r.text}"
    )
    data = r.json()
    assert data.get("error") == "not_configured", (
        f"error='not_configured' esperado, obtenido: {data}"
    )
    assert "message" in data, f"Campo 'message' ausente: {data}"


# ── CASO d: auth del token interno ────────────────────────────────────────────

def test_playlist_no_internal_token_returns_401(client):
    """d-1) POST /playlist sin X-Internal-Token → 401."""
    r = client.post(
        "/playlist",
        json={"isrcs": _SAMPLE_ISRCS, "name": "Playlist"},
    )
    assert r.status_code == 401, (
        f"Sin token esperado 401, obtenido {r.status_code}: {r.text}"
    )


def test_playlist_wrong_internal_token_returns_401(client):
    """d-2) POST /playlist con token incorrecto → 401."""
    r = client.post(
        "/playlist",
        headers={"X-Internal-Token": "INVALID_TOKEN"},
        json={"isrcs": _SAMPLE_ISRCS, "name": "Playlist"},
    )
    assert r.status_code == 401, (
        f"Token incorrecto esperado 401, obtenido {r.status_code}: {r.text}"
    )


def test_playlist_status_no_token_returns_401(client):
    """d-3) GET /playlist/{uuid}/status sin token → 401."""
    r = client.get(f"/playlist/{_UUID_INEXISTENTE}/status")
    assert r.status_code == 401, (
        f"Sin token esperado 401, obtenido {r.status_code}: {r.text}"
    )


def test_playlist_cancel_no_token_returns_401(client):
    """d-4) POST /playlist/{uuid}/cancel sin token → 401."""
    r = client.post(f"/playlist/{_UUID_INEXISTENTE}/cancel")
    assert r.status_code == 401, (
        f"Sin token esperado 401, obtenido {r.status_code}: {r.text}"
    )


# ── CASO e: setup admin-only ──────────────────────────────────────────────────

def test_setup_status_no_admins_env_returns_403(client, auth_headers):
    """
    e-1) GET /playlist/setup/status sin SPOTIFY_CENTRAL_ADMINS → 403 fail-closed.

    Si la variable no está configurada, el setup debe estar completamente deshabilitado.
    """
    # Asegurar que la variable no está en el entorno
    env_sin_admins = {k: v for k, v in os.environ.items() if k != "SPOTIFY_CENTRAL_ADMINS"}
    with patch.dict(os.environ, env_sin_admins, clear=True):
        r = client.get(
            "/playlist/setup/status",
            headers={
                "X-Internal-Token": _TEST_TOKEN,
                "X-User-Email":      _ADMIN_EMAIL,
            },
        )
    assert r.status_code == 403, (
        f"Sin SPOTIFY_CENTRAL_ADMINS esperado 403, obtenido {r.status_code}: {r.text}"
    )


def test_setup_status_non_admin_email_returns_403(client):
    """
    e-2) GET /playlist/setup/status con email que no está en SPOTIFY_CENTRAL_ADMINS → 403.
    """
    with patch.dict(os.environ, {"SPOTIFY_CENTRAL_ADMINS": _ADMIN_EMAIL}):
        r = client.get(
            "/playlist/setup/status",
            headers={
                "X-Internal-Token": _TEST_TOKEN,
                "X-User-Email":      "notanadmin@test.com",
            },
        )
    assert r.status_code == 403, (
        f"Email no-admin esperado 403, obtenido {r.status_code}: {r.text}"
    )


def test_setup_status_valid_admin_returns_200(client):
    """
    e-3) GET /playlist/setup/status con admin válido y token → 200.

    El setup status responde aunque no haya token central (connected=False).
    """
    with (
        patch.dict(os.environ, {"SPOTIFY_CENTRAL_ADMINS": _ADMIN_EMAIL}),
        patch("svc.spotify.get_setup_status", return_value={"connected": False, "account_name": None, "expires_at": None}),
    ):
        r = client.get(
            "/playlist/setup/status",
            headers={
                "X-Internal-Token": _TEST_TOKEN,
                "X-User-Email":      _ADMIN_EMAIL,
            },
        )
    assert r.status_code == 200, (
        f"Admin válido esperado 200, obtenido {r.status_code}: {r.text}"
    )
    data = r.json()
    assert "connected" in data, f"Campo 'connected' ausente: {data}"


# ── CASO f: state OAuth inválido ──────────────────────────────────────────────

def test_setup_exchange_invalid_state_returns_400(client, auth_headers):
    """
    f) POST /playlist/setup/exchange con state HMAC inválido → 400 invalid_state.

    El backend rechaza cualquier state cuya firma HMAC no sea válida.
    """
    with patch("svc.spotify.exchange_code", return_value={"error": "invalid_state"}):
        r = client.post(
            "/playlist/setup/exchange",
            headers=auth_headers,
            json={
                "code":         "fake_code_12345",
                "state":        "invalid.hmac.state",
                "redirect_uri": "https://example.com/callback",
            },
        )
    assert r.status_code == 400, (
        f"State inválido esperado 400, obtenido {r.status_code}: {r.text}"
    )
    detail = r.json().get("detail", "")
    assert "state" in detail.lower() or "inválido" in detail.lower(), (
        f"Mensaje de error inesperado: {detail!r}"
    )


def test_setup_exchange_not_admin_returns_403(client, auth_headers):
    """
    f-2) POST /playlist/setup/exchange con email de no-admin en el state → 403.
    """
    with patch("svc.spotify.exchange_code", return_value={"error": "not_admin"}):
        r = client.post(
            "/playlist/setup/exchange",
            headers=auth_headers,
            json={
                "code":         "fake_code",
                "state":        "valid_but_not_admin",
                "redirect_uri": "https://example.com/callback",
            },
        )
    assert r.status_code == 403, (
        f"not_admin esperado 403, obtenido {r.status_code}: {r.text}"
    )


# ── CASO g: validación de entrada ─────────────────────────────────────────────

def test_playlist_empty_isrcs_returns_422(client, auth_headers):
    """
    g-1) POST /playlist con isrcs=[] → 422 (array vacío no es válido).
    """
    with patch("svc.spotify.has_central_token", return_value=True):
        r = client.post(
            "/playlist",
            headers=auth_headers,
            json={"isrcs": [], "name": "Playlist"},
        )
    assert r.status_code == 422, (
        f"ISRCs vacíos esperado 422, obtenido {r.status_code}: {r.text}"
    )


def test_playlist_invalid_isrcs_all_filtered_returns_422(client, auth_headers):
    """
    g-2) POST /playlist con ISRCs que no pasan el regex ISRC → 422.

    Los ISRCs no válidos se filtran; si todos fallan, el resultado es array vacío → 422.
    """
    with patch("svc.spotify.has_central_token", return_value=True):
        r = client.post(
            "/playlist",
            headers=auth_headers,
            json={"isrcs": ["NOT_AN_ISRC", "TAMBIÉN_MAL"], "name": "Playlist"},
        )
    # El endpoint filtra ISRCs inválidos y si quedan 0 devuelve 422
    assert r.status_code == 422, (
        f"ISRCs inválidos esperado 422, obtenido {r.status_code}: {r.text}"
    )


def test_playlist_missing_name_returns_422(client, auth_headers):
    """
    g-3) POST /playlist sin campo 'name' → 422 (Pydantic valida el campo requerido).
    """
    with patch("svc.spotify.has_central_token", return_value=True):
        r = client.post(
            "/playlist",
            headers=auth_headers,
            json={"isrcs": _SAMPLE_ISRCS},
        )
    assert r.status_code == 422, (
        f"Name ausente esperado 422, obtenido {r.status_code}: {r.text}"
    )


def test_playlist_empty_name_returns_422(client, auth_headers):
    """
    g-4) POST /playlist con name='' (solo blancos) → 422.
    """
    with patch("svc.spotify.has_central_token", return_value=True):
        r = client.post(
            "/playlist",
            headers=auth_headers,
            json={"isrcs": _SAMPLE_ISRCS, "name": "   "},
        )
    assert r.status_code == 422, (
        f"Name vacío esperado 422, obtenido {r.status_code}: {r.text}"
    )


# ── CASO h: cooldown dinámico ─────────────────────────────────────────────────

def test_playlist_status_cooldown_when_cooldown_until_future(client, auth_headers):
    """
    h) GET /playlist/{id}/status devuelve estado='cooldown' cuando cooldown_until
    es futuro y el job está en 'running'.

    El estado 'cooldown' se computa dinámicamente en el endpoint (no se almacena en la DB).
    """
    job_id     = str(uuid.uuid4())
    now_iso    = datetime.now(timezone.utc).isoformat()
    future_iso = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()

    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO spotify_jobs (
                id, estado, phase, total, resolved, added, not_found_count,
                progress_pct, status_text, cooldown_until, error_msg, params, created_at
            ) VALUES (?, 'running', 'resolving', 3, 1, 0, 0, 15.0,
                      'Esperando fin de cooldown de Spotify…', ?, NULL, '{}', ?)
            """,
            (job_id, future_iso, now_iso),
        )
        conn.commit()

    r = client.get(f"/playlist/{job_id}/status", headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["estado"] == "cooldown", (
        f"Con cooldown_until futuro se esperaba estado='cooldown', "
        f"obtenido '{data['estado']}'. Respuesta completa: {data}"
    )
    assert data["cooldown_until"] == future_iso, (
        f"cooldown_until incorrecto: {data['cooldown_until']!r}"
    )

    # Limpiar: pasar a error para que cleanup lo recoja
    with _get_conn() as conn:
        conn.execute("UPDATE spotify_jobs SET estado='error' WHERE id=?", (job_id,))
        conn.commit()


def test_playlist_status_not_cooldown_when_cooldown_expired(client, auth_headers):
    """
    h-2) Si cooldown_until es PASADO, el estado permanece 'running' (no 'cooldown').

    La penalización ya expiró; el worker debería haber continuado.
    """
    job_id    = str(uuid.uuid4())
    now_iso   = datetime.now(timezone.utc).isoformat()
    past_iso  = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()

    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO spotify_jobs (
                id, estado, phase, total, resolved, added, not_found_count,
                progress_pct, status_text, cooldown_until, error_msg, params, created_at
            ) VALUES (?, 'running', 'resolving', 3, 1, 0, 0, 15.0,
                      'Resolviendo ISRCs…', ?, NULL, '{}', ?)
            """,
            (job_id, past_iso, now_iso),
        )
        conn.commit()

    r = client.get(f"/playlist/{job_id}/status", headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["estado"] == "running", (
        f"Con cooldown expirado se esperaba estado='running', "
        f"obtenido '{data['estado']}'. Respuesta: {data}"
    )

    # Limpiar
    with _get_conn() as conn:
        conn.execute("UPDATE spotify_jobs SET estado='error' WHERE id=?", (job_id,))
        conn.commit()


# ── CASO i: cancelación de job ya terminado → 409 ────────────────────────────

def test_playlist_cancel_done_job_returns_409(client, auth_headers):
    """
    i) POST /playlist/{id}/cancel sobre un job ya 'done' → 409.
    """
    with (
        patch("svc.spotify.has_central_token", return_value=True),
        patch("svc.spotify_jobs.resolve_isrcs",         side_effect=_fake_resolve_all),
        patch("svc.spotify_jobs.create_playlist",        side_effect=_fake_create_playlist),
        patch("svc.spotify_jobs.add_tracks_to_playlist", side_effect=_fake_add_tracks),
    ):
        r = client.post(
            "/playlist",
            headers=auth_headers,
            json={"isrcs": _SAMPLE_ISRCS[:1], "name": "Cancel Test"},
        )
        assert r.status_code == 202, r.text
        job_id = r.json()["job_id"]

        status = _wait_for_done(client, job_id, auth_headers)
        assert status["estado"] == "done", f"Estado inesperado: {status}"

    r_cancel = client.post(f"/playlist/{job_id}/cancel", headers=auth_headers)
    assert r_cancel.status_code == 409, (
        f"Cancel de job done esperado 409, obtenido {r_cancel.status_code}: {r_cancel.text}"
    )


# ── CASO j: job_id con formato inválido → 400 ─────────────────────────────────

def test_playlist_invalid_job_id_returns_400(client, auth_headers):
    """
    j) job_id con formato no-UUID → 400 (defensa path traversal, antes del auth).
    """
    invalid_ids = [
        "not-a-uuid",
        "00000000000000000000000000000000",   # sin guiones
        "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE",  # mayúsculas
        "fake-no-uuid-here",                  # demasiado corto
    ]
    for bad_id in invalid_ids:
        r = client.get(f"/playlist/{bad_id}/status", headers=auth_headers)
        assert r.status_code == 400, (
            f"job_id='{bad_id}': esperado 400, obtenido {r.status_code}: {r.text}"
        )

    r = client.get(f"/playlist/not-a-uuid/result.json", headers=auth_headers)
    assert r.status_code == 400, f"result.json con id inválido: {r.status_code}"

    r = client.post(f"/playlist/not-a-uuid/cancel", headers=auth_headers)
    assert r.status_code == 400, f"cancel con id inválido: {r.status_code}"


# ── CASO k: job inexistente (UUID válido) → 404 ────────────────────────────────

def test_playlist_status_unknown_job_returns_404(client, auth_headers):
    """k-1) GET /playlist/{uuid-válido-inexistente}/status → 404."""
    r = client.get(f"/playlist/{_UUID_INEXISTENTE}/status", headers=auth_headers)
    assert r.status_code == 404, r.text


def test_playlist_result_json_unknown_job_returns_404(client, auth_headers):
    """k-2) GET /playlist/{uuid-válido-inexistente}/result.json → 404."""
    r = client.get(f"/playlist/{_UUID_INEXISTENTE}/result.json", headers=auth_headers)
    assert r.status_code == 404, r.text


def test_playlist_cancel_unknown_job_returns_404(client, auth_headers):
    """k-3) POST /playlist/{uuid-válido-inexistente}/cancel → 404."""
    r = client.post(f"/playlist/{_UUID_INEXISTENTE}/cancel", headers=auth_headers)
    assert r.status_code == 404, r.text


# ── CASO l: cancel_job directo (unit del módulo) ──────────────────────────────

def test_cancel_job_direct_returns_false_for_done():
    """
    l-1) spotify_jobs.cancel_job devuelve False si el job ya está en 'done'.

    La guardia previa al UPDATE garantiza que el endpoint responda 409.
    """
    job_id  = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()

    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO spotify_jobs (
                id, estado, phase, total, resolved, added, not_found_count,
                progress_pct, status_text, cooldown_until, error_msg, params, created_at
            ) VALUES (?, 'done', 'adding', 3, 3, 3, 0, 100.0, 'Completado.', NULL, NULL, '{}', ?)
            """,
            (job_id, now_iso),
        )
        conn.commit()

    result = spotify_jobs.cancel_job(job_id)
    assert result is False, (
        f"cancel_job sobre job 'done' debe devolver False, obtenido {result!r}."
    )


def test_cancel_job_direct_returns_false_for_nonexistent():
    """l-2) cancel_job devuelve False para job inexistente."""
    result = spotify_jobs.cancel_job(str(uuid.uuid4()))
    assert result is False, "cancel_job de job inexistente debe devolver False."


def test_cancel_job_direct_returns_true_for_pending():
    """
    l-3) cancel_job devuelve True para un job en estado 'pending'.

    El job aún no ha arrancado (no está en el pool), así que la cancelación
    debe marcarlo como cancelled inmediatamente.
    """
    job_id  = str(uuid.uuid4())
    now_iso = datetime.now(timezone.utc).isoformat()

    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO spotify_jobs (
                id, estado, phase, total, resolved, added, not_found_count,
                progress_pct, status_text, cooldown_until, error_msg, params, created_at
            ) VALUES (?, 'pending', 'resolving', 3, 0, 0, 0, 0.0, 'En cola…', NULL, NULL, '{}', ?)
            """,
            (job_id, now_iso),
        )
        conn.commit()

    import svc.spotify_jobs as sj
    sj._CANCEL_FLAGS[job_id] = __import__("threading").Event()

    result = spotify_jobs.cancel_job(job_id)
    assert result is True, (
        f"cancel_job sobre job 'pending' debe devolver True, obtenido {result!r}."
    )

    status = spotify_jobs.get_status(job_id)
    assert status is not None
    assert status["estado"] == "cancelled", (
        f"El job debe quedar en 'cancelled', estado: {status['estado']}"
    )


# ── CASO m: cleanup_old_jobs (unit del módulo) ────────────────────────────────

def test_cleanup_old_jobs_removes_finished_old():
    """
    m-1) cleanup_old_jobs elimina jobs terminados con created_at > max_age_days.
    """
    job_id  = str(uuid.uuid4())
    old_ts  = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO spotify_jobs (
                id, estado, phase, total, resolved, added, not_found_count,
                progress_pct, status_text, cooldown_until, error_msg, params, created_at
            ) VALUES (?, 'done', 'adding', 1, 1, 1, 0, 100.0, 'Completado.', NULL, NULL, '{}', ?)
            """,
            (job_id, old_ts),
        )
        conn.commit()

    assert spotify_jobs.get_status(job_id) is not None, "El job debe existir antes del cleanup"

    n = spotify_jobs.cleanup_old_jobs(max_age_days=7)
    assert n >= 1, f"cleanup_old_jobs debería haber borrado >= 1 job, borró {n}"
    assert spotify_jobs.get_status(job_id) is None, (
        f"Job de 30 días con max_age_days=7 debe haberse borrado."
    )


def test_cleanup_old_jobs_respects_active_jobs():
    """
    m-2) cleanup_old_jobs NO borra jobs en 'running' aunque sean viejos.
    """
    job_id = str(uuid.uuid4())
    old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO spotify_jobs (
                id, estado, phase, total, resolved, added, not_found_count,
                progress_pct, status_text, cooldown_until, error_msg, params, created_at
            ) VALUES (?, 'running', 'resolving', 3, 1, 0, 0, 15.0,
                      'En ejecución…', NULL, NULL, '{}', ?)
            """,
            (job_id, old_ts),
        )
        conn.commit()

    spotify_jobs.cleanup_old_jobs(max_age_days=7)

    assert spotify_jobs.get_status(job_id) is not None, (
        "cleanup_old_jobs NO debe borrar jobs en estado 'running'."
    )

    # Limpiar
    with _get_conn() as conn:
        conn.execute("UPDATE spotify_jobs SET estado='error' WHERE id=?", (job_id,))
        conn.commit()


def test_cleanup_old_jobs_removes_result_files():
    """
    m-3) cleanup_old_jobs elimina los ficheros de resultado junto con el registro DB.
    """
    job_id = str(uuid.uuid4())
    old_ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO spotify_jobs (
                id, estado, phase, total, resolved, added, not_found_count,
                progress_pct, status_text, cooldown_until, error_msg, params, created_at
            ) VALUES (?, 'done', 'adding', 1, 1, 1, 0, 100.0, 'Completado.', NULL, NULL, '{}', ?)
            """,
            (job_id, old_ts),
        )
        conn.commit()

    # Crear ficheros de resultado ficticios
    for fname in (f"{job_id}.json", f"{job_id}_not_found.csv"):
        (_RESULTS_DIR / fname).write_text("test", encoding="utf-8")

    n = spotify_jobs.cleanup_old_jobs(max_age_days=7)
    assert n >= 1

    for fname in (f"{job_id}.json", f"{job_id}_not_found.csv"):
        assert not (_RESULTS_DIR / fname).exists(), (
            f"cleanup_old_jobs debe borrar el fichero {fname}"
        )


# ── CASO n: status contract fields ───────────────────────────────────────────

def test_playlist_status_contract_fields(client, auth_headers):
    """
    n) GET /playlist/{id}/status devuelve todos los campos que espera el frontend.

    Contrato: {estado, phase, resolved, total, added, not_found, progress_pct,
               status_text, cooldown_until, error_msg}
    """
    with (
        patch("svc.spotify.has_central_token", return_value=True),
        patch("svc.spotify_jobs.resolve_isrcs",         side_effect=_fake_resolve_all),
        patch("svc.spotify_jobs.create_playlist",        side_effect=_fake_create_playlist),
        patch("svc.spotify_jobs.add_tracks_to_playlist", side_effect=_fake_add_tracks),
    ):
        r = client.post(
            "/playlist",
            headers=auth_headers,
            json={"isrcs": _SAMPLE_ISRCS[:1], "name": "Contract Test"},
        )
        assert r.status_code == 202, r.text
        job_id = r.json()["job_id"]

        status = _wait_for_done(client, job_id, auth_headers)

    required_fields = [
        "estado", "phase", "resolved", "total", "added",
        "not_found", "progress_pct", "status_text", "cooldown_until", "error_msg",
    ]
    for field in required_fields:
        assert field in status, (
            f"Campo '{field}' ausente en respuesta de /status: {list(status.keys())}"
        )

    assert status["estado"] == "done"
    assert status["total"]   == 1
    assert status["added"]   == 1
    assert status["progress_pct"] == pytest.approx(100.0, abs=0.1)


# ── CASO o: cancelación en curso ──────────────────────────────────────────────

def test_playlist_cancel_running_job(client, auth_headers):
    """
    o) Cancelar un job en curso: responde 200 y el job termina en 'cancelled'
    (o 'done' si el worker fue demasiado rápido).
    """
    slow_resolve = _make_slow_resolve(delay=0.6)

    with (
        patch("svc.spotify.has_central_token", return_value=True),
        patch("svc.spotify_jobs.resolve_isrcs",         side_effect=slow_resolve),
        patch("svc.spotify_jobs.create_playlist",        side_effect=_fake_create_playlist),
        patch("svc.spotify_jobs.add_tracks_to_playlist", side_effect=_fake_add_tracks),
    ):
        r = client.post(
            "/playlist",
            headers=auth_headers,
            json={"isrcs": _SAMPLE_ISRCS, "name": "Cancel Running"},
        )
        assert r.status_code == 202, r.text
        job_id = r.json()["job_id"]

        r_cancel = client.post(f"/playlist/{job_id}/cancel", headers=auth_headers)
        assert r_cancel.status_code == 200, r_cancel.text
        assert r_cancel.json().get("ok") is True

        status = _wait_for_done(client, job_id, auth_headers, timeout=10)

    assert status["estado"] in ("cancelled", "done"), (
        f"Estado esperado cancelled o done, obtenido: {status['estado']}"
    )


# ── REGRESIÓN p: cooldown_cb actualiza DB y _set_final lo limpia ─────────────

def test_cooldown_cb_updates_db_and_cleared_on_done(client, auth_headers):
    """
    p-1) Regresión: cuando resolve_isrcs llama cooldown_cb(epoch_futuro), la DB
    refleja cooldown_until NO NULL durante la ejecución; y cuando el job termina
    en 'done', _set_final pone cooldown_until=NULL.

    Esta regresión cubre la brecha entre los tests de caso h (que insertan
    cooldown_until directamente en la DB) y el flujo real del worker:
      _cooldown_cb → UPDATE spotify_jobs SET cooldown_until=... → _set_final → NULL
    """
    import threading as _threading

    _cooldown_set   = _threading.Event()  # señal: cooldown_cb fue llamado
    _resume         = _threading.Event()  # señal: el test da permiso para continuar
    _captured_until = {}                  # almacena el valor de cooldown_until observado

    future_epoch = (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()

    def _resolve_with_cooldown_cb(isrcs, progress_cb=None, cooldown_cb=None, cancel_event=None):
        """Mock que llama cooldown_cb con un epoch futuro y espera al test."""
        if cooldown_cb:
            cooldown_cb(future_epoch)   # simula un 429 de Spotify
        _cooldown_set.set()             # notifica al test que cooldown_cb fue invocado
        _resume.wait(timeout=5)         # espera que el test lea la DB
        if progress_cb:
            progress_cb(len(isrcs), len(isrcs), 0, "Resueltos.")
        return {"uris": _FAKE_URIS[: len(isrcs)], "not_found": [], "errors": []}

    with (
        patch("svc.spotify.has_central_token", return_value=True),
        patch("svc.spotify_jobs.resolve_isrcs",         side_effect=_resolve_with_cooldown_cb),
        patch("svc.spotify_jobs.create_playlist",        side_effect=_fake_create_playlist),
        patch("svc.spotify_jobs.add_tracks_to_playlist", side_effect=_fake_add_tracks),
    ):
        r = client.post(
            "/playlist",
            headers=auth_headers,
            json={"isrcs": _SAMPLE_ISRCS[:2], "name": "Cooldown Regression"},
        )
        assert r.status_code == 202, r.text
        job_id = r.json()["job_id"]

        # Esperar a que cooldown_cb haya actualizado la DB
        assert _cooldown_set.wait(timeout=5), "cooldown_cb no fue invocado en 5s"

        # Leer cooldown_until directamente de la DB mientras el worker espera
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT cooldown_until FROM spotify_jobs WHERE id=?", (job_id,)
            ).fetchone()
        _captured_until["value"] = row["cooldown_until"] if row else None

        # Dejar que el worker continúe y el job termine
        _resume.set()
        final_status = _wait_for_done(client, job_id, auth_headers, timeout=10)

    # 1) Mientras el worker estaba en cooldown, cooldown_until debía ser NOT NULL
    assert _captured_until["value"] is not None, (
        "cooldown_cb debe haber escrito cooldown_until en la DB mientras el job corría. "
        f"Valor capturado: {_captured_until['value']!r}"
    )

    # 2) Una vez done, _set_final debe haber puesto cooldown_until=NULL
    assert final_status["estado"] == "done", f"Estado inesperado: {final_status}"
    assert final_status["cooldown_until"] is None, (
        "_set_final debe limpiar cooldown_until al terminar el job. "
        f"Valor en /status: {final_status['cooldown_until']!r}"
    )


def test_cooldown_cb_with_zero_clears_cooldown_until(client, auth_headers):
    """
    p-2) Regresión: cooldown_cb(0) limpia cooldown_until en la DB (fin del penalty-box).

    Simula el flujo real donde Spotify sale del cooldown (retry-after expirado):
      cooldown_cb(future_epoch) → cooldown_cb(0) → cooldown_until=NULL antes de done.
    """
    _first_cb  = __import__("threading").Event()
    _second_cb = __import__("threading").Event()
    _resume    = __import__("threading").Event()

    future_epoch = (datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()

    def _resolve_with_double_cb(isrcs, progress_cb=None, cooldown_cb=None, cancel_event=None):
        if cooldown_cb:
            cooldown_cb(future_epoch)   # pone cooldown_until
        _first_cb.set()
        _resume.wait(timeout=5)         # test verifica que cooldown_until no es NULL
        if cooldown_cb:
            cooldown_cb(0)              # limpia cooldown_until (retry-after expirado)
        _second_cb.set()
        if progress_cb:
            progress_cb(len(isrcs), len(isrcs), 0, "Resueltos.")
        return {"uris": _FAKE_URIS[: len(isrcs)], "not_found": [], "errors": []}

    with (
        patch("svc.spotify.has_central_token", return_value=True),
        patch("svc.spotify_jobs.resolve_isrcs",         side_effect=_resolve_with_double_cb),
        patch("svc.spotify_jobs.create_playlist",        side_effect=_fake_create_playlist),
        patch("svc.spotify_jobs.add_tracks_to_playlist", side_effect=_fake_add_tracks),
    ):
        r = client.post(
            "/playlist",
            headers=auth_headers,
            json={"isrcs": _SAMPLE_ISRCS[:1], "name": "Cooldown Clear Regression"},
        )
        assert r.status_code == 202, r.text
        job_id = r.json()["job_id"]

        # Primera CB: cooldown_until debe ser NOT NULL
        assert _first_cb.wait(timeout=5), "Primera cooldown_cb no invocada"
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT cooldown_until FROM spotify_jobs WHERE id=?", (job_id,)
            ).fetchone()
        assert row and row["cooldown_until"] is not None, (
            "Tras cooldown_cb(epoch_futuro), cooldown_until debe ser NOT NULL. "
            f"Valor: {row['cooldown_until'] if row else 'no row'!r}"
        )

        # Dejar pasar la segunda CB (cooldown_cb(0))
        _resume.set()
        assert _second_cb.wait(timeout=5), "Segunda cooldown_cb no invocada"

        # Leer la DB tras cooldown_cb(0): cooldown_until debe ser NULL
        with _get_conn() as conn:
            row2 = conn.execute(
                "SELECT cooldown_until FROM spotify_jobs WHERE id=?", (job_id,)
            ).fetchone()
        assert row2 and row2["cooldown_until"] is None, (
            "Tras cooldown_cb(0), cooldown_until debe ser NULL. "
            f"Valor: {row2['cooldown_until'] if row2 else 'no row'!r}"
        )

        _wait_for_done(client, job_id, auth_headers, timeout=10)


# ── CASO p: add_tracks 429 → sin pérdida silenciosa ──────────────────────────

def test_add_tracks_all_fail_sets_error(client, auth_headers):
    """
    p) Fix 1 / Fix 2b: si add_tracks_to_playlist devuelve added=0 con URIs disponibles
    (simula 429 irrecuperable o token expirado), el job debe terminar en estado 'error',
    no en 'done'. Ningún track se pierde silenciosamente.
    """
    with (
        patch("svc.spotify.has_central_token", return_value=True),
        patch("svc.spotify_jobs.resolve_isrcs",          side_effect=_fake_resolve_all),
        patch("svc.spotify_jobs.create_playlist",         side_effect=_fake_create_playlist),
        patch("svc.spotify_jobs.add_tracks_to_playlist",  side_effect=_fake_add_tracks_all_fail),
    ):
        r = client.post(
            "/playlist",
            headers=auth_headers,
            json={"isrcs": _SAMPLE_ISRCS[:2], "name": "All Fail Test"},
        )
        assert r.status_code == 202, r.text
        job_id = r.json()["job_id"]

        final = _wait_for_done(client, job_id, auth_headers, timeout=10)

    # Debe ser error, no done — sin pérdida silenciosa
    assert final["estado"] == "error", (
        f"Con added=0 y URIs disponibles, el job debe terminar en 'error', "
        f"no en '{final['estado']}'. error_msg={final.get('error_msg')!r}"
    )
    # El mensaje de error debe mencionar el token o los permisos
    assert final.get("error_msg"), "error_msg no debe estar vacío"


# ── CASO q: errors_count presente en result JSON ──────────────────────────────

def test_errors_count_in_result_json(client, auth_headers):
    """
    q) Fix 19: el result JSON debe incluir el campo 'errors_count' con el número
    de tracks que fallaron en el paso de add (lotes con error + ISRCs no resueltos).
    """
    def _fake_add_tracks_partial_fail(playlist_id, uris, progress_cb=None, cancel_event=None):
        """Simula 1 track fallido de N."""
        n = len(uris)
        added  = max(0, n - 1)
        failed = n - added
        if progress_cb:
            progress_cb(added, n)
        return {"added": added, "failed": failed}

    with (
        patch("svc.spotify.has_central_token", return_value=True),
        patch("svc.spotify_jobs.resolve_isrcs",          side_effect=_fake_resolve_all),
        patch("svc.spotify_jobs.create_playlist",         side_effect=_fake_create_playlist),
        patch("svc.spotify_jobs.add_tracks_to_playlist",  side_effect=_fake_add_tracks_partial_fail),
    ):
        r = client.post(
            "/playlist",
            headers=auth_headers,
            json={"isrcs": _SAMPLE_ISRCS[:3], "name": "Errors Count Test"},
        )
        assert r.status_code == 202, r.text
        job_id = r.json()["job_id"]

        final = _wait_for_done(client, job_id, auth_headers, timeout=10)

    # El job puede quedar done (añadió algo) o error (0 añadidos)
    # — en cualquier caso el result JSON debe tener errors_count
    rr = client.get(f"/playlist/{job_id}/result.json", headers=auth_headers)
    assert rr.status_code == 200, rr.text
    body = rr.json()

    assert "errors_count" in body, (
        f"El campo 'errors_count' debe estar en el result JSON. "
        f"Campos presentes: {list(body.keys())}"
    )
    # Con 3 ISRCs y failed=1, errors_count debe ser >= 1
    assert body["errors_count"] >= 1, (
        f"errors_count debe ser >= 1 cuando hubo tracks fallidos. "
        f"Valor: {body['errors_count']!r}"
    )


# ── CASO r: cola llena → 429 ──────────────────────────────────────────────────

def test_playlist_queue_full_returns_429(client, auth_headers):
    """
    r) POST /playlist cuando count_active_jobs() >= _SP_MAX_QUEUED → 429.

    La cola tiene un límite (_SP_MAX_QUEUED=5) para evitar abuso y OOM.
    Se provoca mockeando count_active_jobs para evitar crear 5 jobs reales.
    """
    with (
        patch("svc.spotify.has_central_token",       return_value=True),
        patch("svc.spotify_jobs.count_active_jobs",  return_value=5),  # == _SP_MAX_QUEUED
    ):
        r = client.post(
            "/playlist",
            headers=auth_headers,
            json={"isrcs": _SAMPLE_ISRCS, "name": "Queue Full Test"},
        )
    assert r.status_code == 429, (
        f"Cola llena esperado 429, obtenido {r.status_code}: {r.text}"
    )
    detail = r.json().get("detail", "")
    assert "cola" in detail.lower() or "máximo" in detail.lower() or "max" in detail.lower(), (
        f"Mensaje 429 debe mencionar la cola o el límite. Obtenido: {detail!r}"
    )
