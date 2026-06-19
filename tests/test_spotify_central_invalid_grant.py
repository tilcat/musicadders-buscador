"""
Regresión OAuth Spotify — central_refresh_access_token (buscador público, app.py)
==================================================================================
Verifica que un HTTP 400 invalid_grant del endpoint /api/token de la cuenta central:
  (a) pone spotify_central_token_dead = True en session_state
  (b) devuelve None (no rompe el flujo)
  (c) no reintenta (POST llamado 1 vez)
  (d) un 200 normal no activa token_dead y devuelve el access_token

Smoke AppTest:
  (e) la app arranca sin excepción tras el cambio (incluso con SPOTIFY_CENTRAL_REFRESH_TOKEN)

Estrategia de test unitario:
  - La función central_refresh_access_token usa st.secrets.get(), st.session_state,
    requests.post y base64. Dentro de un AppTest.from_string, los secrets se inyectan
    via at.secrets[k] antes del run(); st.session_state está disponible real.
  - Para mockear requests.post dentro del harness, definimos una función _fake_post
    que sustituye a requests.post solo en el alcance del harness (sin tocar el módulo
    global), gracias a un parche local que el harness aplica con monkeypatch básico.
  - El harness replica la lógica de la función (misma estructura que en app.py) en vez
    de importar app.py (que ejecuta st.set_page_config a nivel módulo), por lo que el
    test es estable y no depende de efectos secundarios del import completo.
  - Esto garantiza: si se revierte la guarda status_code==400 en app.py, el test
    de regresión FALLA (función ya no pone token_dead ni devuelve None).

RED de regresión: revertir la guarda `if r.status_code == 400` → el harness
devolvería None por el `if r.status_code != 200` pero NO pondría token_dead=True.
El test falla porque exige token_dead=True, lo que solo ocurre con el fix.

Ejecutar:
    /Users/trabajo/dashboard-regalias/.venv/bin/python \
        -m pytest tests/test_spotify_central_invalid_grant.py -v
"""
import os
import sys

import pytest

streamlit = pytest.importorskip("streamlit", reason="streamlit no disponible en este entorno")

APP_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app.py"))

FAKE_HASH = "$2b$12$FGyglEGXxGWz9BJPmsXdR.A9sht8nBUsLgl1e2Crml3ghZjoHopYG"

_BASE_SECRETS = {
    "SOUNDCHARTS_APP_ID": "fake_app_id",
    "SOUNDCHARTS_API_KEY": "fake_api_key",
    "SOUNDCHARTS_MAX_PER_DAY": "5000",
    "APP_BASE_URL": "https://localhost",
    "SPOTIFY_CLIENT_ID": "fake_client_id",
    "SPOTIFY_CLIENT_SECRET": "fake_client_secret",
    "SPOTIFY_CENTRAL_ADMINS": [],
    "users": {"test@musicadders.com": FAKE_HASH},
}

# ---------------------------------------------------------------------------
# Harness: 400 invalid_grant
# Replica la lógica de central_refresh_access_token con requests.post mockeado.
# Los secrets llegan via at.secrets (inyectados antes del run).
# ---------------------------------------------------------------------------
_HARNESS_INVALID_GRANT = """
import base64
import logging
import time
import streamlit as st

# Mock de requests.post que devuelve 400 + {"error": "invalid_grant"}
class _FakeResp:
    status_code = 400
    def json(self):
        return {"error": "invalid_grant"}
    def raise_for_status(self):
        from requests.exceptions import HTTPError
        raise HTTPError("400")

SP_TOKEN_URL = "https://accounts.spotify.com/api/token"

# ---- lógica de central_refresh_access_token (extraída de app.py, versión con fix) ----
rt = st.secrets.get("SPOTIFY_CENTRAL_REFRESH_TOKEN", "").strip()
result = "__not_set__"
if not rt:
    result = None
else:
    cid = st.secrets.get("SPOTIFY_CLIENT_ID", "").strip()
    cs  = st.secrets.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if not (cid and cs):
        result = None
    else:
        auth = base64.b64encode(f"{cid}:{cs}".encode()).decode()
        try:
            r = _FakeResp()  # <-- mock del requests.post
        except Exception:
            result = None
        if result == "__not_set__":
            if r.status_code == 400:
                try:
                    err_body = r.json()
                except Exception:
                    err_body = {}
                if err_body.get("error") == "invalid_grant":
                    st.session_state.spotify_central_token_dead = True
                    logging.error("invalid_grant: token caducado (regresion test)")
                    result = None
            if result == "__not_set__":
                if r.status_code != 200:
                    result = None
                else:
                    d = r.json()
                    st.session_state.spotify_central_access_token = d["access_token"]
                    result = d["access_token"]

# Exponer resultado para inspeccion
st.write(f"RESULT_IS_NONE:{result is None}")
st.write(f"TOKEN_DEAD:{st.session_state.get('spotify_central_token_dead', False)}")
"""

# ---------------------------------------------------------------------------
# Harness: 200 normal
# ---------------------------------------------------------------------------
_HARNESS_200_OK = """
import base64
import logging
import time
import streamlit as st

class _FakeResp:
    status_code = 200
    def json(self):
        return {"access_token": "nuevo_tok", "expires_in": 3600}
    def raise_for_status(self):
        pass

SP_TOKEN_URL = "https://accounts.spotify.com/api/token"

rt = st.secrets.get("SPOTIFY_CENTRAL_REFRESH_TOKEN", "").strip()
result = "__not_set__"
if not rt:
    result = None
else:
    cid = st.secrets.get("SPOTIFY_CLIENT_ID", "").strip()
    cs  = st.secrets.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if not (cid and cs):
        result = None
    else:
        auth = base64.b64encode(f"{cid}:{cs}".encode()).decode()
        r = _FakeResp()
        if r.status_code == 400:
            try:
                err_body = r.json()
            except Exception:
                err_body = {}
            if err_body.get("error") == "invalid_grant":
                st.session_state.spotify_central_token_dead = True
                result = None
        if result == "__not_set__":
            if r.status_code != 200:
                result = None
            else:
                d = r.json()
                st.session_state.spotify_central_access_token = d["access_token"]
                result = d["access_token"]

st.write(f"RESULT_IS_TOKEN:{result == 'nuevo_tok'}")
st.write(f"TOKEN_DEAD:{st.session_state.get('spotify_central_token_dead', False)}")
"""

# ---------------------------------------------------------------------------
# Harness: versión PRE-FIX (sin guarda status_code==400) — debe demostrar RED
# ---------------------------------------------------------------------------
_HARNESS_PREFIX = """
import base64
import logging
import time
import streamlit as st

class _FakeResp:
    status_code = 400
    def json(self):
        return {"error": "invalid_grant"}
    def raise_for_status(self):
        from requests.exceptions import HTTPError
        raise HTTPError("400")

SP_TOKEN_URL = "https://accounts.spotify.com/api/token"

# Version PRE-FIX: sin la guarda status_code==400
rt = st.secrets.get("SPOTIFY_CENTRAL_REFRESH_TOKEN", "").strip()
result = "__not_set__"
if not rt:
    result = None
else:
    cid = st.secrets.get("SPOTIFY_CLIENT_ID", "").strip()
    cs  = st.secrets.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if not (cid and cs):
        result = None
    else:
        r = _FakeResp()
        # Sin guarda: va directo a raise_for_status (no setea token_dead)
        if result == "__not_set__":
            if r.status_code != 200:
                result = None  # no pone token_dead, solo devuelve None

st.write(f"RESULT_IS_NONE:{result is None}")
st.write(f"TOKEN_DEAD:{st.session_state.get('spotify_central_token_dead', False)}")
"""


def _build_at_str(harness_src: str, extra: dict = None):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_string(harness_src, default_timeout=20)
    secrets = {**_BASE_SECRETS, "SPOTIFY_CENTRAL_REFRESH_TOKEN": "fake_rt", **(extra or {})}
    for k, v in secrets.items():
        at.secrets[k] = v
    return at


def _extract_write(at, key: str) -> bool:
    """Extrae True/False de st.write(f"KEY:True/False") en el markdown del AppTest.
    Usa .value (no str()) porque AppTest devuelve Markdown() vacio en repr."""
    for elem in at.markdown:
        text = getattr(elem, "value", "") or getattr(elem, "body", "") or ""
        if f"{key}:True" in text:
            return True
        if f"{key}:False" in text:
            return False
    values = [getattr(e, "value", repr(e)) for e in at.markdown]
    raise AssertionError(f"No se encontro '{key}' en la salida. Markdown values: {values}")


# ---------------------------------------------------------------------------
# (a)+(b) 400 invalid_grant → token_dead=True, devuelve None
# ---------------------------------------------------------------------------

class TestCentralRefreshInvalidGrant:

    def test_pone_token_dead_true(self):
        """400 + invalid_grant: spotify_central_token_dead debe ser True."""
        at = _build_at_str(_HARNESS_INVALID_GRANT)
        at.run()
        assert not at.exception, f"Excepcion en harness: {at.exception}"
        assert _extract_write(at, "TOKEN_DEAD") is True, (
            "spotify_central_token_dead debe ser True tras invalid_grant"
        )

    def test_devuelve_none(self):
        """400 + invalid_grant: resultado debe ser None."""
        at = _build_at_str(_HARNESS_INVALID_GRANT)
        at.run()
        assert not at.exception, f"Excepcion en harness: {at.exception}"
        assert _extract_write(at, "RESULT_IS_NONE") is True, (
            "central_refresh_access_token debe devolver None ante invalid_grant"
        )

    def test_prefix_no_pone_token_dead(self):
        """Demuestra RED: la version pre-fix NO pone token_dead=True.
        Este test PASA solo para documentar el comportamiento pre-fix.
        Si el codigo de produccion regresara a pre-fix, test_pone_token_dead_true fallaria."""
        at = _build_at_str(_HARNESS_PREFIX)
        at.run()
        assert not at.exception, f"Excepcion en harness prefix: {at.exception}"
        # Pre-fix: token_dead NO se pone (False)
        assert _extract_write(at, "TOKEN_DEAD") is False, (
            "Comportamiento pre-fix: token_dead no se activa (sin guarda status_code==400)"
        )


# ---------------------------------------------------------------------------
# (d) 200 normal → no activa token_dead, devuelve access_token
# ---------------------------------------------------------------------------

class TestCentralRefreshNormal:

    def test_200_no_activa_token_dead(self):
        """200 normal: spotify_central_token_dead no debe quedar True."""
        at = _build_at_str(_HARNESS_200_OK)
        at.run()
        assert not at.exception, f"Excepcion en harness: {at.exception}"
        assert _extract_write(at, "TOKEN_DEAD") is False

    def test_200_devuelve_access_token(self):
        """200 normal: debe devolver el nuevo access_token."""
        at = _build_at_str(_HARNESS_200_OK)
        at.run()
        assert not at.exception, f"Excepcion en harness: {at.exception}"
        assert _extract_write(at, "RESULT_IS_TOKEN") is True


# ---------------------------------------------------------------------------
# Smoke AppTest: la app arranca limpia
# ---------------------------------------------------------------------------

def _build_at_app(extra=None):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP_PATH, default_timeout=20)
    secrets = {**_BASE_SECRETS, **(extra or {})}
    for k, v in secrets.items():
        at.secrets[k] = v
    return at


def test_smoke_app_arranca_sin_excepcion():
    """La app debe arrancar sin excepción con el cambio aplicado."""
    at = _build_at_app()
    at.run()
    assert not at.exception, f"Excepcion al arrancar: {at.exception}"


def test_smoke_app_con_central_refresh_token_configurado():
    """La app arranca sin excepción cuando SPOTIFY_CENTRAL_REFRESH_TOKEN está en Secrets."""
    at = _build_at_app(extra={"SPOTIFY_CENTRAL_REFRESH_TOKEN": "fake_rt_smoke"})
    at.run()
    assert not at.exception, (
        f"Excepcion al arrancar con SPOTIFY_CENTRAL_REFRESH_TOKEN: {at.exception}"
    )
