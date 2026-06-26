"""
svc/tests/test_search_endpoint.py
Tests del endpoint GET /search (F2 — búsqueda de 1 ISRC).

Cubren:
  a) ISRC válido (mock de soundcharts) → 200, contrato completo.
  b) ISRC inválido → 422 con mensaje claro.
  c) Auth: sin token → 401; token incorrecto → 401.
  d) ISRC inexistente en Soundcharts → meta null, 200.
  e) Scope → plataformas correctas (importantes=4, todas=9, individual=1).
  f) SoundchartsRateLimitError → 429 con {error: "rate_limited"} (Fix 8: por tipo).
  g) EnvironmentError (credenciales ausentes) → 503 controlado (Fix 7).
  h) Cuota diaria superada → 429 con {error: "rate_limit_daily"} (Fix 9).
  i) /health expone calls_today / calls_date / calls_limit (Fix 9).

Usa TestClient de FastAPI (httpx síncrono). Mockea svc.soundcharts.search_isrc
con unittest.mock.patch para no tocar red real.

Interprete: svc/.venv/bin/pytest  (Python 3.14)
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ── Configuracion de entorno antes de importar la app ─────────────────────────
_TEST_TOKEN = "test-token-abc123"
os.environ.setdefault("INTERNAL_TOKEN", _TEST_TOKEN)
os.environ.setdefault("SOUNDCHARTS_APP_ID", "dummy")
os.environ.setdefault("SOUNDCHARTS_API_KEY", "dummy")

from svc.main import app, _daily_state, _today_iso  # noqa: E402
from svc.soundcharts import SoundchartsRateLimitError  # noqa: E402

# ── ISRCs de test (formato valido, no existen en Soundcharts real) ─────────────
_ISRC_VALID   = "ESAA12300001"
_ISRC_VALID_2 = "USRC17607839"


# ── Fixtures ───────────────────────────────────────────────────────────────────
#
# scope="session": unico TestClient para toda la sesion de estos tests.
# /search no usa svc.jobs (pool de workers), por lo que el singleton de jobs
# no interfiere. Si la suite de batch corre en la misma sesion de pytest,
# ambos TestClients conviven sin conflicto porque /search es independiente
# del pool.

@pytest.fixture(scope="session")
def client():
    """TestClient de sesion para tests de /search."""
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture(scope="session")
def auth_headers():
    return {"X-Internal-Token": _TEST_TOKEN}


# ── Mocks de svc.soundcharts.search_isrc ──────────────────────────────────────

def _fake_found(isrc: str, platforms: list[str], buster: str = "") -> dict:
    """Mock: devuelve track valido con 2 playlists en 2 DSPs distintas."""
    return {
        "meta": {
            "uuid": "uuid-search-001",
            "song_name": "Search Test Song",
            "credit_name": "Search Artist",
            "release_date": "2024-03-15",
        },
        "playlists": [
            {
                "platform": "spotify",
                "playlist_uuid": "pl-uuid-s01",
                "playlist_id": "pl-id-s01",
                "playlist_name": "Top Hits",
                "playlist_type": "Editorial",
                "country_code": "ES",
                "subscriber_count": 1_200_000,
                "image_url": None,
                "position": 5,
                "peak_position": 2,
                "entry_date": "2024-03-01",
            },
            {
                "platform": "apple-music",
                "playlist_uuid": "pl-uuid-s02",
                "playlist_id": "pl-id-s02",
                "playlist_name": "New Music Daily",
                "playlist_type": "Algorithmic",
                "country_code": "",
                "subscriber_count": 800_000,
                "image_url": None,
                "position": 1,
                "peak_position": 1,
                "entry_date": "2024-03-10",
            },
        ],
        "calls_used": 3,
    }


def _fake_not_found(isrc: str, platforms: list[str], buster: str = "") -> dict:
    """Mock: ISRC no existe en Soundcharts (meta=null, 1 llamada consumida)."""
    return {"meta": None, "playlists": [], "calls_used": 1}


def _raise_rate_limit(isrc: str, platforms: list[str], buster: str = "") -> dict:
    """Mock: Soundcharts devuelve rate limit (Fix 8: usa SoundchartsRateLimitError)."""
    raise SoundchartsRateLimitError("Soundcharts 429 rate-limited")


def _raise_env_error(isrc: str, platforms: list[str], buster: str = "") -> dict:
    """Mock: credenciales SC no configuradas (Fix 7)."""
    raise EnvironmentError(
        "SOUNDCHARTS_APP_ID y SOUNDCHARTS_API_KEY deben estar definidas en el entorno."
    )


# ── a) ISRC valido → 200 + contrato completo ─────────────────────────────────

@patch("svc.soundcharts.search_isrc", side_effect=_fake_found)
def test_search_valid_isrc_returns_full_contract(mock_search, client, auth_headers):
    """a) ISRC valido mockeado → 200 con todos los campos del contrato."""
    r = client.get(
        f"/search?isrc={_ISRC_VALID}&scope=importantes",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()

    # meta presente con campos del contrato
    assert data["meta"] is not None
    assert data["meta"]["song_name"] == "Search Test Song"
    assert data["meta"]["credit_name"] == "Search Artist"
    assert data["meta"]["release_date"] == "2024-03-15"

    # playlists: lista con los campos que consume el frontend
    assert isinstance(data["playlists"], list)
    assert len(data["playlists"]) == 2
    for pl in data["playlists"]:
        for campo in ("platform", "playlist_name", "playlist_type",
                      "subscriber_count", "position"):
            assert campo in pl, f"Campo '{campo}' ausente en playlist: {pl}"

    # calls_used segun mock
    assert data["calls_used"] == 3

    # elapsed_ms: entero ≥ 0
    assert "elapsed_ms" in data
    assert isinstance(data["elapsed_ms"], int)
    assert data["elapsed_ms"] >= 0

    # platforms_count: 2 DSPs distintas (spotify + apple-music)
    assert data["platforms_count"] == 2, (
        f"platforms_count esperado 2, obtenido {data['platforms_count']}"
    )

    # total_platforms: scope=importantes → 4 plataformas consultadas
    assert data["total_platforms"] == 4, (
        f"total_platforms esperado 4, obtenido {data['total_platforms']}"
    )


# ── b) ISRC invalido → 422 ────────────────────────────────────────────────────

@pytest.mark.parametrize("bad_isrc", [
    "INVALID",          # demasiado corto / sin estructura
    "ES123",            # solo 5 chars
    "12AB34567890",     # empieza con digitos, no letras
    "TOOLONGISRC12345", # demasiado largo (>12 chars efectivos)
    "abc",              # minusculo y corto
    # "" vacío: FastAPI devuelve 422 por Query(...) requerido antes de validacion ISRC
    "",
])
def test_search_invalid_isrc_returns_422(bad_isrc, client, auth_headers):
    """b) ISRC con formato incorrecto → 422."""
    url = f"/search?isrc={bad_isrc}&scope=importantes"
    r = client.get(url, headers=auth_headers)
    assert r.status_code == 422, (
        f"ISRC='{bad_isrc}' esperado 422, obtenido {r.status_code}: {r.text}"
    )


# ── c) Auth ───────────────────────────────────────────────────────────────────

def test_search_no_token_returns_401(client):
    """c-1) Sin X-Internal-Token → 401 (fail-closed)."""
    r = client.get(f"/search?isrc={_ISRC_VALID}&scope=importantes")
    assert r.status_code == 401, (
        f"Sin token esperado 401, obtenido {r.status_code}: {r.text}"
    )


def test_search_wrong_token_returns_401(client):
    """c-2) Token incorrecto → 401."""
    r = client.get(
        f"/search?isrc={_ISRC_VALID}&scope=importantes",
        headers={"X-Internal-Token": "WRONG_TOKEN_XYZ"},
    )
    assert r.status_code == 401, (
        f"Token incorrecto esperado 401, obtenido {r.status_code}: {r.text}"
    )


# ── d) ISRC inexistente en Soundcharts → meta null, 200 ─────────────────────

@patch("svc.soundcharts.search_isrc", side_effect=_fake_not_found)
def test_search_isrc_not_in_soundcharts_returns_meta_null(mock_search, client, auth_headers):
    """d) ISRC valido pero no en Soundcharts → 200 con meta=null."""
    r = client.get(
        f"/search?isrc={_ISRC_VALID_2}&scope=importantes",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()

    assert data["meta"] is None, f"meta esperado null, obtenido: {data['meta']}"
    assert data["playlists"] == []
    assert data["calls_used"] == 1
    assert "elapsed_ms" in data
    assert data["platforms_count"] == 0, (
        f"platforms_count esperado 0 (sin resultados), obtenido {data['platforms_count']}"
    )


# ── e) Scope → plataformas correctas ─────────────────────────────────────────
#
# _capture_platforms almacena las plataformas recibidas en un atributo de funcion
# para inspeccion posterior. Thread-safe porque pytest corre tests en serie.

def _capture_platforms(isrc: str, platforms: list[str], buster: str = "") -> dict:
    """Mock que guarda platforms para inspeccion y devuelve not_found."""
    _capture_platforms.last = list(platforms)
    return {"meta": None, "playlists": [], "calls_used": 1}


_capture_platforms.last = []


@patch("svc.soundcharts.search_isrc", side_effect=_capture_platforms)
def test_search_scope_importantes_uses_4_default_platforms(mock_search, client, auth_headers):
    """e-1) scope=importantes → {spotify, apple-music, amazon, deezer} (4)."""
    _capture_platforms.last = []
    r = client.get(
        f"/search?isrc={_ISRC_VALID}&scope=importantes",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert set(_capture_platforms.last) == {"spotify", "apple-music", "amazon", "deezer"}, (
        f"scope=importantes esperaba 4 DSPs, obtuvo: {_capture_platforms.last}"
    )
    assert r.json()["total_platforms"] == 4


@patch("svc.soundcharts.search_isrc", side_effect=_capture_platforms)
def test_search_scope_todas_uses_9_platforms(mock_search, client, auth_headers):
    """e-2) scope=todas → 9 plataformas (las 4 + las 5 extra)."""
    _capture_platforms.last = []
    r = client.get(
        f"/search?isrc={_ISRC_VALID}&scope=todas",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert len(_capture_platforms.last) == 9, (
        f"scope=todas esperaba 9 DSPs, obtuvo {len(_capture_platforms.last)}: "
        f"{_capture_platforms.last}"
    )
    assert r.json()["total_platforms"] == 9


@patch("svc.soundcharts.search_isrc", side_effect=_capture_platforms)
def test_search_scope_single_platform(mock_search, client, auth_headers):
    """e-3) scope=spotify → solo [spotify], total_platforms=1."""
    _capture_platforms.last = []
    r = client.get(
        f"/search?isrc={_ISRC_VALID}&scope=spotify",
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert _capture_platforms.last == ["spotify"], (
        f"scope=spotify esperaba ['spotify'], obtuvo: {_capture_platforms.last}"
    )
    assert r.json()["total_platforms"] == 1


# ── f) SoundchartsRateLimitError → 429 con {error: "rate_limited"} ───────────
# Fix 8: captura por TIPO (SoundchartsRateLimitError), no por string "429".

@patch("svc.soundcharts.search_isrc", side_effect=_raise_rate_limit)
def test_search_soundcharts_rate_limit_returns_429(mock_search, client, auth_headers):
    """f) SoundchartsRateLimitError → endpoint devuelve 429 con error='rate_limited'."""
    r = client.get(
        f"/search?isrc={_ISRC_VALID}&scope=importantes",
        headers=auth_headers,
    )
    assert r.status_code == 429, (
        f"SoundchartsRateLimitError esperado status 429, obtenido {r.status_code}: {r.text}"
    )
    data = r.json()
    assert data.get("error") == "rate_limited", (
        f"Campo 'error' esperado 'rate_limited', obtenido: {data.get('error')}"
    )
    assert "message" in data, f"Campo 'message' ausente en respuesta 429: {data}"


def test_plain_runtime_error_returns_502_not_429(client, auth_headers):
    """f-extra) RuntimeError generico (no SoundchartsRateLimitError) → 502 (no 429).

    Verifica que la deteccion por tipo no confunde RuntimeError arbitrarios con 429.
    """
    def _raise_generic_runtime(isrc, platforms, buster=""):
        raise RuntimeError("Error generico de red o de logica")

    with patch("svc.soundcharts.search_isrc", side_effect=_raise_generic_runtime):
        r = client.get(
            f"/search?isrc={_ISRC_VALID}&scope=importantes",
            headers=auth_headers,
        )
    assert r.status_code == 502, (
        f"RuntimeError generico esperado 502, obtenido {r.status_code}: {r.text}"
    )


# ── g) EnvironmentError → 503 controlado (Fix 7) ─────────────────────────────

@patch("svc.soundcharts.search_isrc", side_effect=_raise_env_error)
def test_search_missing_credentials_returns_503(mock_search, client, auth_headers):
    """g) EnvironmentError (credenciales SC no configuradas) → 503 con detalle claro.

    Fix 7: antes salia 500 con traceback; ahora se captura y devuelve 503 controlado.
    """
    r = client.get(
        f"/search?isrc={_ISRC_VALID}&scope=importantes",
        headers=auth_headers,
    )
    assert r.status_code == 503, (
        f"EnvironmentError esperado 503, obtenido {r.status_code}: {r.text}"
    )


# ── h) Cuota diaria superada → 429 rate_limit_daily (Fix 9) ──────────────────

def test_search_daily_quota_exceeded_returns_429(client, auth_headers):
    """h) Contador diario >= SOUNDCHARTS_MAX_PER_DAY → 429 con error='rate_limit_daily'.

    Fix 9: paridad con Streamlit app.py:1229-1235 que comprueba la cuota antes de buscar.
    Manipula _daily_state directamente (variable global en svc.main) para aislar el test.
    """
    # Guardar estado original y poner el contador al maximo
    original_date  = _daily_state["date"]
    original_calls = _daily_state["calls"]
    _daily_state["date"]  = _today_iso()
    _daily_state["calls"] = 9999  # supera el default de 5000

    try:
        r = client.get(
            f"/search?isrc={_ISRC_VALID}&scope=importantes",
            headers=auth_headers,
        )
    finally:
        # Restaurar estado para no contaminar tests posteriores
        _daily_state["date"]  = original_date
        _daily_state["calls"] = original_calls

    assert r.status_code == 429, (
        f"Cuota diaria superada esperado 429, obtenido {r.status_code}: {r.text}"
    )
    data = r.json()
    assert data.get("error") == "rate_limit_daily", (
        f"error='rate_limit_daily' esperado, obtenido: {data.get('error')}"
    )
    assert "message" in data, f"Campo 'message' ausente en respuesta de cuota: {data}"


# ── i) /health expone llamadas del dia (Fix 9) ───────────────────────────────

def test_health_exposes_daily_calls_counter(client):
    """i) /health incluye calls_today, calls_date y calls_limit (Fix 9)."""
    r = client.get("/health")
    assert r.status_code == 200, r.text
    data = r.json()
    assert "calls_today" in data, f"'calls_today' no encontrado en /health: {data}"
    assert "calls_date" in data, f"'calls_date' no encontrado en /health: {data}"
    assert "calls_limit" in data, f"'calls_limit' no encontrado en /health: {data}"
    assert isinstance(data["calls_today"], int)
    assert isinstance(data["calls_limit"], int)
    assert data["calls_limit"] > 0


# ── j) SoundchartsRateLimitError desde get_song_playlists → 429 (Fix A) ────────

def test_search_playlist_fetch_429_returns_429(client, auth_headers):
    """Fix A: SoundchartsRateLimitError emitida desde get_song_playlists
    (no desde search_isrc directamente) propagada hasta /search → 429 con
    {error: 'rate_limited'}.
    Verifica la ruta: get_song_playlists → search_isrc → search_single → 429.
    """
    _valid_meta = {
        "uuid": "aaaa-1111-test",
        "song_name": "Fix A Test Track",
        "credit_name": "Fix A Artist",
    }

    def _raise_from_playlists(uuid: str, platform: str, buster: str = "") -> list:
        raise SoundchartsRateLimitError("Soundcharts 429 rate-limited")

    with (
        patch("svc.soundcharts.lookup_isrc_to_uuid", return_value=_valid_meta),
        patch("svc.soundcharts.get_song_playlists", side_effect=_raise_from_playlists),
    ):
        r = client.get(
            f"/search?isrc={_ISRC_VALID}&scope=importantes",
            headers=auth_headers,
        )

    assert r.status_code == 429, (
        f"get_song_playlists 429 esperado → /search 429, obtenido {r.status_code}: {r.text}"
    )
    data = r.json()
    assert data.get("error") == "rate_limited", (
        f"error='rate_limited' esperado, obtenido: {data.get('error')}"
    )
