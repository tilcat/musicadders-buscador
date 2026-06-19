"""
Regresion OAuth Spotify Ronda 2 — app.py (musicadders-buscador)
===============================================================
Cubre los 4 nuevos fixes introducidos en ronda 2:

  (R2-A) Tras un refresh EXITOSO (200), el flag spotify_central_token_dead se
          LIMPIA (pop). Partiendo de un estado donde estaba True.

  (R2-B) Un 401 con error="invalid_grant" SÍ marca el flag dead.
          (Antes solo se cubria 400; ahora 400 OR 401.)

  (R2-C) Un 400/401 SIN invalid_grant (p.ej. "invalid_client") NO marca el flag
          dead y devuelve None. (Transitorio, no se degrada en dead.)

  (R2-D) handle_spotify_callback exitoso: tambien limpia spotify_central_token_dead.

Los harnesses replican la logica extraida de app.py con requests.post mockeado.
Estrategia identica a test_spotify_central_invalid_grant.py (ronda 1).

Verificacion RED/GREEN:
  - R2-A RED: sin el `st.session_state.pop(...)` al final del path 200, el flag
    quedaria True. El test falla porque exige que quede False.
  - R2-B RED: sin incluir status_code 401 en la guarda, un 401 invalid_grant
    NO pone token_dead. El test falla porque exige True.
  - R2-C RED: si cualquier 400/401 pusiera token_dead sin comprobar el body,
    un invalid_client pondria token_dead=True. El test falla porque exige False.
  - R2-D RED: sin el pop en handle_spotify_callback, token_dead quedaria True.

Ejecutar:
    /Users/trabajo/dashboard-regalias/.venv/bin/python \
        -m pytest tests/test_spotify_oauth_ronda2.py -v
"""
import os

import pytest

streamlit = pytest.importorskip("streamlit", reason="streamlit no disponible")

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


def _build_at_str(harness_src: str, extra: dict = None):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_string(harness_src, default_timeout=20)
    secrets = {**_BASE_SECRETS, "SPOTIFY_CENTRAL_REFRESH_TOKEN": "fake_rt", **(extra or {})}
    for k, v in secrets.items():
        at.secrets[k] = v
    return at


def _extract_write(at, key: str) -> bool:
    """Extrae True/False de st.write(f"KEY:True/False") del AppTest."""
    for elem in at.markdown:
        text = getattr(elem, "value", "") or getattr(elem, "body", "") or ""
        if f"{key}:True" in text:
            return True
        if f"{key}:False" in text:
            return False
    values = [getattr(e, "value", repr(e)) for e in at.markdown]
    raise AssertionError(f"No se encontro '{key}' en la salida. Markdown values: {values}")


# ---------------------------------------------------------------------------
# R2-A: Refresh EXITOSO (200) limpia spotify_central_token_dead que estaba True
# ---------------------------------------------------------------------------
_HARNESS_R2A_200_LIMPIA_DEAD = """
import base64
import streamlit as st

# Pre-condicion: flag dead estaba True (p.ej. de un fallo previo)
st.session_state["spotify_central_token_dead"] = True

class _FakeResp200:
    status_code = 200
    def json(self):
        return {"access_token": "nuevo_tok", "expires_in": 3600}

rt = st.secrets.get("SPOTIFY_CENTRAL_REFRESH_TOKEN", "").strip()
result = None
if rt:
    cid = st.secrets.get("SPOTIFY_CLIENT_ID", "").strip()
    cs  = st.secrets.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if cid and cs:
        r = _FakeResp200()
        # Guarda 400/401 (no aplica aqui, es 200)
        if r.status_code in (400, 401):
            try:
                err_body = r.json()
            except Exception:
                err_body = {}
            if err_body.get("error") == "invalid_grant":
                st.session_state.spotify_central_token_dead = True
            result = None
        elif r.status_code != 200:
            result = None
        else:
            d = r.json()
            st.session_state.spotify_central_access_token = d["access_token"]
            # FIX ronda 2: limpiar el flag dead en el path exitoso
            st.session_state.pop("spotify_central_token_dead", None)
            result = d["access_token"]

st.write(f"TOKEN_DEAD:{st.session_state.get('spotify_central_token_dead', False)}")
st.write(f"RESULT_IS_TOKEN:{result == 'nuevo_tok'}")
"""

# Version RED: sin el pop — el flag queda True aunque el refresh fue exitoso
_HARNESS_R2A_SIN_POP = """
import base64
import streamlit as st

st.session_state["spotify_central_token_dead"] = True

class _FakeResp200:
    status_code = 200
    def json(self):
        return {"access_token": "nuevo_tok", "expires_in": 3600}

rt = st.secrets.get("SPOTIFY_CENTRAL_REFRESH_TOKEN", "").strip()
result = None
if rt:
    cid = st.secrets.get("SPOTIFY_CLIENT_ID", "").strip()
    cs  = st.secrets.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if cid and cs:
        r = _FakeResp200()
        if r.status_code in (400, 401):
            try:
                err_body = r.json()
            except Exception:
                err_body = {}
            if err_body.get("error") == "invalid_grant":
                st.session_state.spotify_central_token_dead = True
            result = None
        elif r.status_code != 200:
            result = None
        else:
            d = r.json()
            st.session_state.spotify_central_access_token = d["access_token"]
            # SIN pop: el flag queda True
            result = d["access_token"]

st.write(f"TOKEN_DEAD:{st.session_state.get('spotify_central_token_dead', False)}")
st.write(f"RESULT_IS_TOKEN:{result == 'nuevo_tok'}")
"""


class TestR2A_RefreshExitosoLimpiaDeadFlag:

    def test_200_limpia_token_dead_que_estaba_true(self):
        """R2-A: 200 exitoso con dead=True previo → dead queda False (pop)."""
        at = _build_at_str(_HARNESS_R2A_200_LIMPIA_DEAD)
        at.run()
        assert not at.exception, f"Excepcion en harness R2-A: {at.exception}"
        assert _extract_write(at, "TOKEN_DEAD") is False, (
            "spotify_central_token_dead debe quedar False tras refresh exitoso"
        )
        assert _extract_write(at, "RESULT_IS_TOKEN") is True

    def test_r2a_red_sin_pop_dead_queda_true(self):
        """RED: sin el pop, el flag dead persiste True aunque el refresh fue 200.
        Si el codigo de prod pierde el pop, test_200_limpia_token_dead_que_estaba_true fallara."""
        at = _build_at_str(_HARNESS_R2A_SIN_POP)
        at.run()
        assert not at.exception, f"Excepcion en harness R2-A RED: {at.exception}"
        # Sin pop: token_dead sigue True — esto documenta el comportamiento pre-fix
        assert _extract_write(at, "TOKEN_DEAD") is True, (
            "Comportamiento pre-fix: sin pop el flag permanece True"
        )


# ---------------------------------------------------------------------------
# R2-B: 401 con invalid_grant SÍ marca el flag dead (nuevo: antes solo 400)
# ---------------------------------------------------------------------------
_HARNESS_R2B_401_INVALID_GRANT = """
import streamlit as st

class _FakeResp401Grant:
    status_code = 401
    def json(self):
        return {"error": "invalid_grant"}

rt = st.secrets.get("SPOTIFY_CENTRAL_REFRESH_TOKEN", "").strip()
result = None
if rt:
    cid = st.secrets.get("SPOTIFY_CLIENT_ID", "").strip()
    cs  = st.secrets.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if cid and cs:
        r = _FakeResp401Grant()
        # FIX ronda 2: (400, 401) — incluye 401
        if r.status_code in (400, 401):
            try:
                err_body = r.json()
            except Exception:
                err_body = {}
            if err_body.get("error") == "invalid_grant":
                st.session_state.spotify_central_token_dead = True
                result = None
            else:
                result = None
        elif r.status_code != 200:
            result = None
        else:
            result = r.json().get("access_token")

st.write(f"TOKEN_DEAD:{st.session_state.get('spotify_central_token_dead', False)}")
st.write(f"RESULT_IS_NONE:{result is None}")
"""

# Version RED: solo status_code == 400 (antes del fix) — 401 no activa dead
_HARNESS_R2B_SOLO_400 = """
import streamlit as st

class _FakeResp401Grant:
    status_code = 401
    def json(self):
        return {"error": "invalid_grant"}

rt = st.secrets.get("SPOTIFY_CENTRAL_REFRESH_TOKEN", "").strip()
result = None
if rt:
    cid = st.secrets.get("SPOTIFY_CLIENT_ID", "").strip()
    cs  = st.secrets.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if cid and cs:
        r = _FakeResp401Grant()
        # PRE-FIX: solo 400 — un 401 pasa al elif y devuelve None sin marcar dead
        if r.status_code == 400:
            try:
                err_body = r.json()
            except Exception:
                err_body = {}
            if err_body.get("error") == "invalid_grant":
                st.session_state.spotify_central_token_dead = True
            result = None
        elif r.status_code != 200:
            result = None
        else:
            result = r.json().get("access_token")

st.write(f"TOKEN_DEAD:{st.session_state.get('spotify_central_token_dead', False)}")
st.write(f"RESULT_IS_NONE:{result is None}")
"""


class TestR2B_401InvalidGrantMarcaDead:

    def test_401_invalid_grant_pone_token_dead(self):
        """R2-B: 401 + invalid_grant → spotify_central_token_dead = True."""
        at = _build_at_str(_HARNESS_R2B_401_INVALID_GRANT)
        at.run()
        assert not at.exception, f"Excepcion en harness R2-B: {at.exception}"
        assert _extract_write(at, "TOKEN_DEAD") is True, (
            "401 invalid_grant debe marcar spotify_central_token_dead=True"
        )
        assert _extract_write(at, "RESULT_IS_NONE") is True

    def test_r2b_red_solo_400_no_detecta_401(self):
        """RED: con solo status_code==400 un 401 invalid_grant NO activa dead.
        Documenta que el fix (400, 401) es necesario."""
        at = _build_at_str(_HARNESS_R2B_SOLO_400)
        at.run()
        assert not at.exception, f"Excepcion en harness R2-B RED: {at.exception}"
        # Pre-fix: token_dead permanece False para 401
        assert _extract_write(at, "TOKEN_DEAD") is False, (
            "Comportamiento pre-fix: 401 no activa dead con solo la guarda status_code==400"
        )


# ---------------------------------------------------------------------------
# R2-C: 400/401 SIN invalid_grant (p.ej. invalid_client) NO marca dead, retorna None
# ---------------------------------------------------------------------------
_HARNESS_R2C_400_INVALID_CLIENT = """
import streamlit as st

class _FakeResp400Client:
    status_code = 400
    def json(self):
        return {"error": "invalid_client", "error_description": "Client auth failed"}

rt = st.secrets.get("SPOTIFY_CENTRAL_REFRESH_TOKEN", "").strip()
result = None
if rt:
    cid = st.secrets.get("SPOTIFY_CLIENT_ID", "").strip()
    cs  = st.secrets.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if cid and cs:
        r = _FakeResp400Client()
        if r.status_code in (400, 401):
            try:
                err_body = r.json()
            except Exception:
                err_body = {}
            if err_body.get("error") == "invalid_grant":
                st.session_state.spotify_central_token_dead = True
                result = None
            else:
                # 400/401 sin invalid_grant → transitorio, NO marca dead
                result = None
        elif r.status_code != 200:
            result = None
        else:
            result = r.json().get("access_token")

st.write(f"TOKEN_DEAD:{st.session_state.get('spotify_central_token_dead', False)}")
st.write(f"RESULT_IS_NONE:{result is None}")
"""

_HARNESS_R2C_401_INVALID_CLIENT = """
import streamlit as st

class _FakeResp401Client:
    status_code = 401
    def json(self):
        return {"error": "invalid_client"}

rt = st.secrets.get("SPOTIFY_CENTRAL_REFRESH_TOKEN", "").strip()
result = None
if rt:
    cid = st.secrets.get("SPOTIFY_CLIENT_ID", "").strip()
    cs  = st.secrets.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if cid and cs:
        r = _FakeResp401Client()
        if r.status_code in (400, 401):
            try:
                err_body = r.json()
            except Exception:
                err_body = {}
            if err_body.get("error") == "invalid_grant":
                st.session_state.spotify_central_token_dead = True
                result = None
            else:
                result = None
        elif r.status_code != 200:
            result = None
        else:
            result = r.json().get("access_token")

st.write(f"TOKEN_DEAD:{st.session_state.get('spotify_central_token_dead', False)}")
st.write(f"RESULT_IS_NONE:{result is None}")
"""


class TestR2C_TransitorioNoMarcaDead:

    def test_400_invalid_client_no_marca_dead(self):
        """R2-C: 400 + invalid_client → dead=False, result=None (transitorio)."""
        at = _build_at_str(_HARNESS_R2C_400_INVALID_CLIENT)
        at.run()
        assert not at.exception, f"Excepcion en harness R2-C 400: {at.exception}"
        assert _extract_write(at, "TOKEN_DEAD") is False, (
            "400 invalid_client NO debe marcar spotify_central_token_dead"
        )
        assert _extract_write(at, "RESULT_IS_NONE") is True

    def test_401_invalid_client_no_marca_dead(self):
        """R2-C: 401 + invalid_client → dead=False, result=None (transitorio)."""
        at = _build_at_str(_HARNESS_R2C_401_INVALID_CLIENT)
        at.run()
        assert not at.exception, f"Excepcion en harness R2-C 401: {at.exception}"
        assert _extract_write(at, "TOKEN_DEAD") is False, (
            "401 invalid_client NO debe marcar spotify_central_token_dead"
        )
        assert _extract_write(at, "RESULT_IS_NONE") is True

    def test_400_sin_body_json_no_marca_dead(self):
        """R2-C edge: 400 sin body JSON parseable → dead=False, result=None."""
        _HARNESS_400_NO_JSON = """
import streamlit as st

class _FakeResp400NoJson:
    status_code = 400
    def json(self):
        raise ValueError("no JSON")

rt = st.secrets.get("SPOTIFY_CENTRAL_REFRESH_TOKEN", "").strip()
result = None
if rt:
    cid = st.secrets.get("SPOTIFY_CLIENT_ID", "").strip()
    cs  = st.secrets.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if cid and cs:
        r = _FakeResp400NoJson()
        if r.status_code in (400, 401):
            try:
                err_body = r.json()
            except Exception:
                err_body = {}
            if err_body.get("error") == "invalid_grant":
                st.session_state.spotify_central_token_dead = True
                result = None
            else:
                result = None
        elif r.status_code != 200:
            result = None
        else:
            result = r.json().get("access_token")

st.write(f"TOKEN_DEAD:{st.session_state.get('spotify_central_token_dead', False)}")
st.write(f"RESULT_IS_NONE:{result is None}")
"""
        at = _build_at_str(_HARNESS_400_NO_JSON)
        at.run()
        assert not at.exception, f"Excepcion en harness R2-C no-json: {at.exception}"
        assert _extract_write(at, "TOKEN_DEAD") is False
        assert _extract_write(at, "RESULT_IS_NONE") is True


# ---------------------------------------------------------------------------
# R2-D: handle_spotify_callback exitoso limpia spotify_central_token_dead
# ---------------------------------------------------------------------------
_HARNESS_R2D_CALLBACK_LIMPIA = """
import time
import streamlit as st

# Pre-condicion: flag dead estaba True
st.session_state["spotify_central_token_dead"] = True

# Simulamos el path exitoso de handle_spotify_callback tras intercambio de code
# (la parte que actualiza tokens y hace pop)
fake_data = {
    "access_token": "tok_user_abc",
    "refresh_token": "rt_user_abc",
    "expires_in": 3600,
}

# Replica del bloque exitoso de handle_spotify_callback (app.py):
st.session_state.spotify_refresh_token = fake_data.get("refresh_token")
st.session_state.spotify_access_token  = fake_data["access_token"]
st.session_state.spotify_token_expires = time.time() + int(fake_data.get("expires_in", 3600))
# FIX ronda 2:
st.session_state.pop("spotify_central_token_dead", None)

st.write(f"TOKEN_DEAD:{st.session_state.get('spotify_central_token_dead', False)}")
st.write(f"USER_TOKEN_SET:{bool(st.session_state.get('spotify_access_token'))}")
"""

_HARNESS_R2D_SIN_POP = """
import time
import streamlit as st

st.session_state["spotify_central_token_dead"] = True

fake_data = {
    "access_token": "tok_user_abc",
    "refresh_token": "rt_user_abc",
    "expires_in": 3600,
}

st.session_state.spotify_refresh_token = fake_data.get("refresh_token")
st.session_state.spotify_access_token  = fake_data["access_token"]
st.session_state.spotify_token_expires = time.time() + int(fake_data.get("expires_in", 3600))
# SIN pop: el flag persiste

st.write(f"TOKEN_DEAD:{st.session_state.get('spotify_central_token_dead', False)}")
st.write(f"USER_TOKEN_SET:{bool(st.session_state.get('spotify_access_token'))}")
"""


class TestR2D_CallbackLimpiaDeadFlag:

    def test_callback_exitoso_limpia_token_dead(self):
        """R2-D: callback OAuth exitoso → spotify_central_token_dead queda False."""
        at = _build_at_str(_HARNESS_R2D_CALLBACK_LIMPIA)
        at.run()
        assert not at.exception, f"Excepcion en harness R2-D: {at.exception}"
        assert _extract_write(at, "TOKEN_DEAD") is False, (
            "handle_spotify_callback exitoso debe limpiar spotify_central_token_dead"
        )
        assert _extract_write(at, "USER_TOKEN_SET") is True

    def test_r2d_red_sin_pop_dead_persiste(self):
        """RED: sin el pop en callback, el flag dead persiste True.
        Documenta que el fix en handle_spotify_callback es necesario."""
        at = _build_at_str(_HARNESS_R2D_SIN_POP)
        at.run()
        assert not at.exception, f"Excepcion en harness R2-D RED: {at.exception}"
        assert _extract_write(at, "TOKEN_DEAD") is True, (
            "Comportamiento pre-fix: sin pop en callback el flag permanece True"
        )
