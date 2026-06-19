"""Buscador ISRC público — Musicadders.

3 modos:
  1. Búsqueda individual: pega 1 ISRC → placements en vivo.
  2. Procesado batch: sube Excel con hasta 500 ISRCs → tabla unificada.
  3. Crear playlist Spotify usando una cuenta central configurada por el admin.

Variables de entorno necesarias (Streamlit Cloud Secrets UI):
    SOUNDCHARTS_APP_ID = "..."
    SOUNDCHARTS_API_KEY = "..."
    SOUNDCHARTS_MAX_PER_DAY = "5000"   # opcional
    SPOTIFY_CLIENT_ID = "..."          # solo si quieren crear playlists
    SPOTIFY_CLIENT_SECRET = "..."
    APP_BASE_URL = "https://musicadders-isrc.streamlit.app"  # exacto sin trailing slash

    [users]
    "victor@musicadders.com" = "$2b$12$..."
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import io
import json
import logging
import os
import random
import re
import secrets as _secrets_mod
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path

import bcrypt
import pandas as pd
import requests
import streamlit as st

import fuga_client
from cards import _build_card_html


# ════════════════════════════════════════════════════════════════════════════
# CONSTANTES
# ════════════════════════════════════════════════════════════════════════════
MAX_BATCH_ISRCS = 500
SPOTIFY_SCOPES = "playlist-modify-public playlist-modify-private user-read-private user-read-email"
# SPOTIFY_CENTRAL_MODE = True  # legacy flag, modo central es ahora obligatorio


def _is_admin(user_email: str) -> bool:
    """True si el email está en la lista SPOTIFY_CENTRAL_ADMINS de Secrets.
    Si la lista no existe o está vacía, NADIE es admin (fail-closed)."""
    admins = st.secrets.get("SPOTIFY_CENTRAL_ADMINS", [])
    if isinstance(admins, str):
        # Soporte de string CSV como fallback
        admins = [a.strip().lower() for a in admins.split(",") if a.strip()]
    elif isinstance(admins, (list, tuple)):
        admins = [str(a).strip().lower() for a in admins if str(a).strip()]
    else:
        admins = []
    return user_email.strip().lower() in admins


# ════════════════════════════════════════════════════════════════════════════
# CONFIG + BRANDING
# ════════════════════════════════════════════════════════════════════════════
st.set_page_config(
    page_title="Musicadders · Buscador de placements",
    page_icon="🎵",
    layout="wide",
    initial_sidebar_state="collapsed",
)

BRAND_CSS = """
<style>
    /* Reset Streamlit defaults para look más limpio */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    .stDeployButton {display: none;}

    /* Gradient header */
    .ma-header {
        background: linear-gradient(135deg, #1ED760 0%, #06B6D4 100%);
        padding: 2rem 2.5rem;
        border-radius: 16px;
        margin-bottom: 2rem;
        color: white;
        box-shadow: 0 4px 16px rgba(30,215,96,0.15);
    }
    .ma-header h1 {
        margin: 0;
        font-size: 1.8rem;
        font-weight: 700;
        letter-spacing: -0.02em;
        color: white;
    }
    .ma-header .sub {
        color: rgba(255,255,255,0.85);
        font-size: 0.95rem;
        margin-top: 0.25rem;
    }

    /* Cards de playlists */
    .ma-pl-card {
        background: white;
        border: 1px solid #e5e7eb;
        border-left: 4px solid #1ED760;
        padding: 0.9rem 1.1rem;
        border-radius: 8px;
        margin: 0.5rem 0;
    }
    .ma-pl-card.algorithmic { border-left-color: #06B6D4; }
    .ma-pl-card.charts { border-left-color: #f59e0b; }
    .ma-pl-card.user { border-left-color: #9ca3af; }
    .ma-pl-card .pl-name { font-weight: 600; font-size: 1rem; color: #111827; }
    .ma-pl-card .pl-meta { color: #6b7280; font-size: 0.85rem; margin-top: 0.2rem; }

    /* Login box */
    .ma-login {
        max-width: 420px;
        margin: 4rem auto;
        padding: 2.5rem;
        background: white;
        border-radius: 16px;
        box-shadow: 0 8px 32px rgba(0,0,0,0.08);
        text-align: center;
    }
</style>
"""
st.markdown(BRAND_CSS, unsafe_allow_html=True)


# ════════════════════════════════════════════════════════════════════════════
# AUTH
# ════════════════════════════════════════════════════════════════════════════
def _verify_user(email: str, password: str) -> bool:
    """Verifica email + password contra hashes bcrypt en st.secrets['users']."""
    try:
        users = st.secrets.get("users", {})
    except Exception:
        users = {}
    email_norm = (email or "").strip().lower()
    hashed = users.get(email_norm) or users.get(email)
    if not hashed or not password:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def login_view():
    """Pantalla de login. Bloquea el resto hasta que el user esté autenticado."""
    col_a, col_b, col_c = st.columns([1, 2, 1])
    with col_b:
        logo_path = Path(__file__).parent / "assets" / "logo_negro.png"
        if logo_path.exists():
            st.image(str(logo_path), width=180)
        st.markdown(
            "<h2 style='text-align:center;color:#111827;margin-top:1.5rem;'>"
            "Buscador de placements</h2>"
            "<p style='text-align:center;color:#6b7280;margin-bottom:2rem;'>"
            "Pega un ISRC y ve en qué playlists está, en todas las DSPs.</p>",
            unsafe_allow_html=True,
        )
        with st.form("login"):
            email = st.text_input("Email", placeholder="tunombre@musicadders.com")
            password = st.text_input("Contraseña", type="password")
            submitted = st.form_submit_button("Entrar", width="stretch", type="primary")
            if submitted:
                if _verify_user(email, password):
                    st.session_state.user_email = email.strip().lower()
                    st.session_state.login_at = datetime.now(timezone.utc).isoformat()
                    st.rerun()
                else:
                    st.error("Email o contraseña incorrectos.")


# ════════════════════════════════════════════════════════════════════════════
# SOUNDCHARTS CLIENT — versión live, in-memory cache
# ════════════════════════════════════════════════════════════════════════════
SC_BASE = "https://customer.api.soundcharts.com"
PLATFORMS_DEFAULT = ["spotify", "apple-music", "amazon", "deezer"]
PLATFORMS_EXTRA = ["youtube", "soundcloud", "tidal", "audiomack", "pandora"]


def _sc_headers() -> dict:
    return {
        "x-app-id": st.secrets["SOUNDCHARTS_APP_ID"],
        "x-api-key": st.secrets["SOUNDCHARTS_API_KEY"],
        "accept": "application/json",
    }


def _is_official_type(t: str) -> bool:
    """Categorías que cuentan como 'oficial' (curado por la DSP):
    Editorial + Editorial Personalized 'Algotorial' + Algorithmic + Charts.
    Excluye This is..., Major label, Radios, Curators & Listeners."""
    if not t:
        return False
    tl = str(t).lower()
    if ("curators" in tl or tl.strip() == "radios"
        or "this is" in tl or "major" in tl):
        return False
    return (
        "editorial" in tl or "algotorial" in tl
        or "chart" in tl or "algorithmic" in tl
    )


def _dedupe_playlists(rows: list[dict]) -> list[dict]:
    """Agrupa entradas que solo se distinguen por playlist_uuid pero comparten
    plataforma+nombre+tipo (caso típico de Amazon que devuelve la misma playlist
    varias veces). Concatena países."""
    if not rows:
        return rows
    groups: dict[tuple, dict] = {}
    for r in rows:
        key = (r["platform"], r.get("playlist_name") or "", r.get("playlist_type") or "")
        if key not in groups:
            groups[key] = {
                **r,
                "countries": set([r.get("country_code")] if r.get("country_code") else []),
                "n_variantes": 1,
            }
        else:
            g = groups[key]
            if r.get("country_code"):
                g["countries"].add(r["country_code"])
            if r.get("position") is not None:
                cur = g.get("position")
                if cur is None or r["position"] < cur:
                    g["position"] = r["position"]
            if (r.get("subscriber_count") or 0) > (g.get("subscriber_count") or 0):
                g["subscriber_count"] = r["subscriber_count"]
                g["image_url"] = r.get("image_url")
            g["n_variantes"] += 1
    # Formatear países como string
    out = []
    for g in groups.values():
        g["country_code"] = ", ".join(sorted(c for c in g["countries"] if c))
        del g["countries"]
        out.append(g)
    return out


@st.cache_data(ttl=3600, show_spinner=False)
def lookup_isrc_to_uuid(isrc: str, _buster: str = "") -> dict | None:
    """ISRC → UUID Soundcharts. Cacheado 1h.
    `_buster` es un cache-buster: si cambia, fuerza nueva llamada (refresh manual)."""
    isrc = isrc.strip().upper()
    r = requests.get(f"{SC_BASE}/api/v2/song/by-isrc/{isrc}",
                     headers=_sc_headers(), timeout=15)
    if r.status_code != 200:
        return None
    obj = (r.json() or {}).get("object") or {}
    return {
        "uuid": obj.get("uuid"),
        "song_name": obj.get("name"),
        "credit_name": obj.get("creditName"),
        "release_date": obj.get("releaseDate"),
    }


@st.cache_data(ttl=3600, show_spinner=False)
def get_song_playlists(uuid: str, platform: str, _buster: str = "") -> list[dict]:
    """Playlists actuales de un song en una plataforma. Pagina hasta el total.
    `_buster` ídem: si cambia, fuerza re-fetch."""
    out: list[dict] = []
    offset = 0
    while True:
        r = requests.get(
            f"{SC_BASE}/api/v2.20/song/{uuid}/playlist/current/{platform}",
            headers=_sc_headers(),
            params={"limit": 100, "offset": offset, "currentOnly": 1,
                    "sortBy": "subscriberCount", "sortOrder": "desc"},
            timeout=20,
        )
        if r.status_code != 200:
            break
        d = r.json() or {}
        items = d.get("items") or []
        for it in items:
            pl = it.get("playlist") or {}
            out.append({
                "platform": platform,
                "playlist_uuid": pl.get("uuid"),
                "playlist_id": pl.get("identifier"),
                "playlist_name": pl.get("name"),
                "playlist_type": pl.get("type"),
                "country_code": pl.get("countryCode") or "",
                "subscriber_count": pl.get("latestSubscriberCount"),
                "image_url": pl.get("imageUrl"),
                "position": it.get("position"),
                "peak_position": it.get("peakPosition"),
                "entry_date": it.get("entryDate"),
            })
        page = d.get("page") or {}
        total = page.get("total") or 0
        offset += len(items)
        if not items or offset >= total or offset >= 500:
            break
    return out


def search_isrc(isrc: str, platforms: list[str], buster: str = "") -> dict:
    """Búsqueda completa de un ISRC. Si `buster` cambia respecto a llamadas
    previas, ignora cache y vuelve a llamar a Soundcharts."""
    meta = lookup_isrc_to_uuid(isrc, _buster=buster)
    if not meta or not meta.get("uuid"):
        return {"meta": None, "playlists": [], "calls_used": 1}
    uuid = meta["uuid"]
    all_pls: list[dict] = []
    calls = 1
    for plat in platforms:
        pls = get_song_playlists(uuid, plat, _buster=buster)
        all_pls.extend(pls)
        calls += max(1, (len(pls) // 100) + 1)
    return {"meta": meta, "playlists": _dedupe_playlists(all_pls), "calls_used": calls}


# ════════════════════════════════════════════════════════════════════════════
# SPOTIFY OAUTH POR USUARIO
# ════════════════════════════════════════════════════════════════════════════
SP_TOKEN_URL = "https://accounts.spotify.com/api/token"
SP_AUTH_URL = "https://accounts.spotify.com/authorize"
SP_API = "https://api.spotify.com/v1"


def _record_sp_error(context: str, r) -> None:
    """Guarda el status + cuerpo reales de un fallo de Spotify para diagnóstico.
    Se muestra al admin en la UI en lugar del genérico 'error desconocido'."""
    try:
        body = (r.text or "")[:400]
    except Exception:
        body = "?"
    detalle = f"{context}: HTTP {getattr(r, 'status_code', '?')} — {body}"
    st.session_state["sp_last_error"] = detalle
    logging.error("Spotify error · %s", detalle[:350])


def _app_base_url() -> str:
    """Base URL exacta de la app (para construir el redirect URI Spotify)."""
    return str(st.secrets.get("APP_BASE_URL", "https://musicadders-isrc.streamlit.app")).rstrip("/")


def _state_secret_key() -> bytes:
    """Clave HMAC para firmar el `state` OAuth. Deriva de CLIENT_SECRET de Spotify
    (ya gestionado en Streamlit Secrets), así no requiere config adicional."""
    cs = st.secrets.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if not cs:
        raise RuntimeError(
            "SPOTIFY_CLIENT_SECRET no configurado: el state OAuth no puede firmarse de forma segura"
        )
    return hashlib.sha256(cs.encode("utf-8")).digest()


def _encode_oauth_state(user_email: str) -> str:
    """Codifica {nonce, email} en un state firmado HMAC. Permite recuperar el
    email tras la redirección OAuth aunque Streamlit pierda la sesión."""
    payload = {"n": _secrets_mod.token_urlsafe(8), "u": user_email or "",
               "t": int(time.time())}
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = hmac.new(_state_secret_key(), raw.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{raw}.{sig}"


def _decode_oauth_state(state: str) -> dict | None:
    """Verifica firma HMAC y devuelve el payload. None si inválido o caducado (>30 min)."""
    if not state or "." not in state:
        return None
    raw, sig = state.rsplit(".", 1)
    try:
        expected = hmac.new(_state_secret_key(), raw.encode(), hashlib.sha256).hexdigest()[:16]
    except RuntimeError:
        return None  # secret no configurado: fallo controlado (state inválido)
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        pad = "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(raw + pad))
    except Exception:
        return None
    if int(time.time()) - int(payload.get("t", 0)) > 1800:
        return None  # state demasiado antiguo
    return payload


def spotify_login_url() -> str | None:
    """Genera la URL de autorización Spotify para el user actual."""
    cid = st.secrets.get("SPOTIFY_CLIENT_ID", "").strip()
    if not cid:
        return None
    try:
        state = _encode_oauth_state(st.session_state.get("user_email", ""))
    except RuntimeError:
        return None  # secret no configurado: fallo controlado (igual que sin CLIENT_ID)
    st.session_state.spotify_oauth_state = state
    params = {
        "client_id": cid,
        "response_type": "code",
        "redirect_uri": _app_base_url() + "/",
        "scope": SPOTIFY_SCOPES,
        "state": state,
        "show_dialog": "true",
    }
    return f"{SP_AUTH_URL}?{urllib.parse.urlencode(params)}"


def spotify_exchange_code(code: str) -> dict | None:
    """Intercambia el ?code= por access_token + refresh_token."""
    cid = st.secrets.get("SPOTIFY_CLIENT_ID", "").strip()
    cs = st.secrets.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if not (cid and cs):
        return None
    auth = base64.b64encode(f"{cid}:{cs}".encode()).decode()
    r = requests.post(
        SP_TOKEN_URL,
        headers={"Authorization": f"Basic {auth}",
                 "Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": _app_base_url() + "/",
        },
        timeout=20,
    )
    if r.status_code != 200:
        return None
    return r.json()


def spotify_refresh_access_token() -> str | None:
    """Renueva el access_token del user actual usando su refresh_token."""
    rt = st.session_state.get("spotify_refresh_token")
    if not rt:
        return None
    cid = st.secrets.get("SPOTIFY_CLIENT_ID", "").strip()
    cs = st.secrets.get("SPOTIFY_CLIENT_SECRET", "").strip()
    auth = base64.b64encode(f"{cid}:{cs}".encode()).decode()
    r = requests.post(
        SP_TOKEN_URL,
        headers={"Authorization": f"Basic {auth}",
                 "Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "refresh_token", "refresh_token": rt},
        timeout=15,
    )
    if r.status_code != 200:
        return None
    d = r.json()
    st.session_state.spotify_access_token = d["access_token"]
    st.session_state.spotify_token_expires = time.time() + int(d.get("expires_in", 3600))
    return d["access_token"]


def spotify_get_token() -> str | None:
    """Devuelve un access_token válido del user actual, renovando si caducó."""
    at = st.session_state.get("spotify_access_token")
    exp = st.session_state.get("spotify_token_expires", 0)
    if at and time.time() < exp - 60:
        return at
    return spotify_refresh_access_token()


def spotify_user_id() -> str | None:
    if st.session_state.get("spotify_user_id"):
        return st.session_state.spotify_user_id
    tok = spotify_get_token()
    if not tok:
        return None
    r = requests.get(f"{SP_API}/me", headers={"Authorization": f"Bearer {tok}"}, timeout=15)
    if r.status_code != 200:
        return None
    me = r.json()
    st.session_state.spotify_user_id = me.get("id")
    st.session_state.spotify_display_name = me.get("display_name") or me.get("id")
    return st.session_state.spotify_user_id


def _fetch_cc_token_raw() -> tuple[str, int] | None:
    """Obtiene un Client Credentials token de Spotify sin tocar st.session_state.
    Seguro para llamar desde worker threads.
    Devuelve (access_token, expires_in) o None si falla."""
    cid = st.secrets.get("SPOTIFY_CLIENT_ID", "").strip()
    cs = st.secrets.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if not (cid and cs):
        return None
    auth = base64.b64encode(f"{cid}:{cs}".encode()).decode()
    r = requests.post(
        SP_TOKEN_URL,
        headers={"Authorization": f"Basic {auth}",
                 "Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "client_credentials"},
        timeout=15,
    )
    if r.status_code != 200:
        return None
    d = r.json()
    tok = d.get("access_token")
    if not tok:
        return None
    return (tok, int(d.get("expires_in", 3600)))


def spotify_client_credentials_token() -> str | None:
    """Token a nivel de app (Client Credentials). Independiente del user OAuth.
    Útil para Search masivo: tiene su propio bucket de rate limit.
    Solo llamar desde el hilo principal (usa st.session_state como caché)."""
    tok = st.session_state.get("sp_cc_token")
    exp = st.session_state.get("sp_cc_token_exp", 0)
    if tok and time.time() < exp - 60:
        return tok
    result = _fetch_cc_token_raw()
    if not result:
        return None
    new_tok, expires_in = result
    st.session_state.sp_cc_token = new_tok
    st.session_state.sp_cc_token_exp = time.time() + expires_in
    return new_tok


def central_refresh_access_token() -> str | None:
    """Renueva el access_token de la cuenta central usando el refresh_token
    guardado en Streamlit Secrets como SPOTIFY_CENTRAL_REFRESH_TOKEN.
    Devuelve el access_token o None si no hay refresh_token configurado
    o el refresh falla."""
    rt = st.secrets.get("SPOTIFY_CENTRAL_REFRESH_TOKEN", "").strip()
    if not rt:
        return None
    cid = st.secrets.get("SPOTIFY_CLIENT_ID", "").strip()
    cs = st.secrets.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if not (cid and cs):
        return None
    auth = base64.b64encode(f"{cid}:{cs}".encode()).decode()
    try:
        r = requests.post(
            SP_TOKEN_URL,
            headers={"Authorization": f"Basic {auth}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "refresh_token", "refresh_token": rt},
            timeout=15,
        )
    except requests.exceptions.RequestException:
        return None
    if r.status_code in (400, 401):
        try:
            err_body = r.json()
        except Exception:
            err_body = {}
        if err_body.get("error") == "invalid_grant":
            st.session_state.spotify_central_token_dead = True
            logging.error(
                "SPOTIFY_CENTRAL_REFRESH_TOKEN inválido (invalid_grant): "
                "el token ha caducado/revocado (política Spotify: 6 meses) "
                "O SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET han cambiado. "
                "El admin debe verificar AMBOS en Streamlit Secrets y reconectar "
                "la cuenta central via la pestaña Setup."
            )
            return None
        # 400/401 sin invalid_grant → transitorio (credenciales incorrectas u otro error)
        _record_sp_error("refresh token central", r)
        return None
    if r.status_code != 200:
        _record_sp_error("refresh token central", r)
        return None
    d = r.json()
    st.session_state.spotify_central_access_token = d["access_token"]
    st.session_state.spotify_central_token_expires = time.time() + int(d.get("expires_in", 3600))
    new_rt = d.get("refresh_token")
    if new_rt and new_rt != rt:
        # Spotify ha rotado el refresh_token. Aviso al admin.
        if _is_admin(st.session_state.get("user_email", "")):
            st.warning(
                f"⚠️ Spotify rotó el refresh_token central. Actualiza en Streamlit Secrets:\n\n"
                f"`SPOTIFY_CENTRAL_REFRESH_TOKEN = \"{new_rt}\"`\n\n"
                "El token actual sigue activo pero puede caducar pronto."
            )
        logging.warning("Spotify rotated central refresh_token. Admin must update Streamlit Secrets.")
    # Aviso proactivo de caducidad próxima (política 6 meses).
    # Solo si SPOTIFY_CENTRAL_REFRESH_TOKEN_ISSUED existe, es parseable y queda 0 < días <= 14.
    # Flag de sesión _token_expiry_warned evita emitir el warning más de una vez por sesión.
    _issued_raw = str(st.secrets.get("SPOTIFY_CENTRAL_REFRESH_TOKEN_ISSUED", "")).strip()
    if (
        _issued_raw
        and _is_admin(st.session_state.get("user_email", ""))
        and not st.session_state.get("_token_expiry_warned")
    ):
        try:
            _issued_date = date.fromisoformat(_issued_raw)
            _days_left = 180 - (date.today() - _issued_date).days
            if 0 < _days_left <= 14:
                st.warning(
                    f"El refresh token de Spotify caduca en {_days_left} dia(s) "
                    "(política Spotify: 6 meses). Ve a la pestaña Setup, reconecta la cuenta "
                    "central y actualiza SPOTIFY_CENTRAL_REFRESH_TOKEN en Streamlit Secrets."
                )
                st.session_state["_token_expiry_warned"] = True
        except Exception:
            pass  # secret mal formado → silencioso
    st.session_state.pop("spotify_central_token_dead", None)
    return d["access_token"]


def central_get_access_token() -> str | None:
    """Devuelve un access_token válido de la cuenta central, renovando si caducó."""
    at = st.session_state.get("spotify_central_access_token")
    exp = st.session_state.get("spotify_central_token_expires", 0)
    if at and time.time() < exp - 60:
        return at
    return central_refresh_access_token()


def central_user_info() -> dict | None:
    """Llama /me con el token central y cachea {id, display_name, email}.
    Devuelve dict o None si no hay token central."""
    if st.session_state.get("spotify_central_user_id"):
        return {
            "id": st.session_state.spotify_central_user_id,
            "display_name": st.session_state.get("spotify_central_display_name", ""),
            "email": st.session_state.get("spotify_central_email", ""),
        }
    tok = central_get_access_token()
    if not tok:
        return None
    r = requests.get(f"{SP_API}/me", headers={"Authorization": f"Bearer {tok}"}, timeout=15)
    if r.status_code != 200:
        _record_sp_error("verificar cuenta central (GET /me)", r)
        return None
    me = r.json()
    expected_id = str(st.secrets.get("SPOTIFY_CENTRAL_EXPECTED_USER_ID", "")).strip()
    if expected_id and me.get("id") != expected_id:
        # Cuenta central sustituida en Secrets sin permiso. Abortar.
        logging.error(f"CENTRAL ACCOUNT MISMATCH: expected={expected_id}, got={me.get('id')}")
        st.session_state["sp_last_error"] = (
            f"cuenta central distinta de la esperada: SPOTIFY_CENTRAL_EXPECTED_USER_ID="
            f"{expected_id} pero el token es de {me.get('id')}")
        return None
    st.session_state.spotify_central_user_id = me.get("id")
    st.session_state.spotify_central_display_name = me.get("display_name") or me.get("id")
    st.session_state.spotify_central_email = me.get("email", "")
    return {
        "id": st.session_state.spotify_central_user_id,
        "display_name": st.session_state.spotify_central_display_name,
        "email": st.session_state.spotify_central_email,
    }


def spotify_find_uri_by_isrc(isrc: str) -> str | None:
    tok = spotify_get_token()
    if not tok:
        return None
    r = requests.get(
        f"{SP_API}/search",
        headers={"Authorization": f"Bearer {tok}"},
        params={"q": f"isrc:{isrc}", "type": "track", "limit": 1},
        timeout=15,
    )
    if r.status_code != 200:
        return None
    items = (r.json().get("tracks") or {}).get("items") or []
    return items[0]["uri"] if items else None


def spotify_resolve_isrcs(isrcs: list[str], progress_cb=None,
                           max_workers: int = 6) -> dict:
    """Resuelve ISRCs → URIs Spotify en paralelo, preservando orden.

    Política conservadora anti-penalty-box:
    - 6 workers paralelos máximo (≈30 req/s pico, ≈360/min teórico).
    - Throttle proactivo per-worker (200ms ± jitter 50ms) para mantener
      el ritmo sostenido bajo el umbral observado de Spotify Dev Mode.
    - Retry-After respetado hasta 60s (Spotify a veces pide esperas largas
      y capear bajo ese valor activa penalty box).
    - Cooldown global compartido en 429 + reintentos limitados.
    """
    tok = spotify_client_credentials_token()
    if not tok:
        return {"uris": [], "not_found": [], "errors": [(i, "no CC token") for i in isrcs],
                "stopped": True, "reason": "No se pudo obtener Client Credentials token."}

    # Sesión POR HILO (no compartida). requests.Session no es thread-safe; al
    # compartir una sola entre 6 workers, reutilizar del pool una conexión que
    # Spotify ya cerró (keep-alive ocioso) provoca RemoteDisconnected
    # ("Remote end closed connection without response"). Cada hilo con su propia
    # sesión + Retry a nivel de conexión (reintenta en conexión nueva).
    _retry = requests.adapters.Retry(
        total=3, connect=3, read=2, backoff_factor=0.4,
        status_forcelist=(502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    _tls = threading.local()

    def _session() -> requests.Session:
        s = getattr(_tls, "sess", None)
        if s is None:
            s = requests.Session()
            s.mount("https://", requests.adapters.HTTPAdapter(
                pool_connections=2, pool_maxsize=4, max_retries=_retry))
            _tls.sess = s
        return s

    lock = threading.Lock()
    tok_ref = {"v": tok}
    # Si Spotify nos manda un 429 con Retry-After largo, pausamos todos
    # los hilos en lugar de que cada uno espere de forma independiente.
    cooldown_until = {"t": 0.0}
    # Diagnóstico: loguea el motivo REAL de los primeros errores de resolución
    # (status de Spotify) para distinguir 429/penalty-box de 403/otros.
    _err_diag = {"n": 0}

    def _log_err(isrc: str, reason: str) -> None:
        with lock:
            if _err_diag["n"] < 8:
                _err_diag["n"] += 1
                logging.warning("Spotify resolve · %s → %s", isrc, reason)

    def _resolve_one(isrc: str) -> tuple[str, str, str | None]:
        """(isrc, kind, value). kind ∈ {'uri','notfound','error'}."""
        attempts = 0
        # Throttle proactivo inicial con jitter para evitar burst sincronizado
        # entre workers cuando arrancan a la vez. Sin esto, los N workers
        # disparan a la vez al inicio y Spotify ve un pico de N req simultáneas.
        time.sleep(random.uniform(0.0, 0.2))
        while attempts < 4:
            attempts += 1
            # Throttle proactivo per-request: ritmo sostenido seguro
            # 6 workers × 1 req cada 200ms ≈ 30 req/s pico ≈ 360/min teórico
            time.sleep(0.2 + random.uniform(-0.05, 0.05))
            # Respeta cooldown global si está activo
            wait_global = cooldown_until["t"] - time.time()
            if wait_global > 0:
                time.sleep(min(wait_global, 60))
            with lock:
                cur_tok = tok_ref["v"]
            try:
                r = _session().get(
                    f"{SP_API}/search",
                    headers={"Authorization": f"Bearer {cur_tok}"},
                    params={"q": f"isrc:{isrc}", "type": "track", "limit": 1},
                    timeout=15,
                )
            except requests.RequestException as e:
                # Caída de conexión (RemoteDisconnected, etc.): transitoria →
                # reintenta en conexión nueva en vez de rendirse al primer fallo.
                _log_err(isrc, f"net: {str(e)[:80]}")
                if attempts <= 3:
                    try:
                        _tls.sess = None  # fuerza sesión/conexión nueva en el reintento
                    except Exception:
                        pass
                    time.sleep(0.3 * attempts)
                    continue
                return (isrc, "error", f"net: {str(e)[:50]}")

            if r.status_code == 200:
                items = (r.json().get("tracks") or {}).get("items") or []
                return (isrc, "uri", items[0]["uri"]) if items else (isrc, "notfound", None)

            if r.status_code == 401:
                # CC token caducó: renovar (un solo hilo a la vez).
                # Usamos _fetch_cc_token_raw() (sin st.session_state) porque
                # estamos en un worker thread donde session_state no es seguro.
                with lock:
                    if tok_ref["v"] == cur_tok:
                        raw = _fetch_cc_token_raw()
                        if raw:
                            tok_ref["v"] = raw[0]
                if attempts <= 2:
                    continue
                _log_err(isrc, "auth 401 (CC token no renovable; revisa CLIENT_ID/SECRET)")
                return (isrc, "error", "auth 401")

            if r.status_code == 429:
                ra = r.headers.get("Retry-After")
                try:
                    # Cap a 60s: si Spotify pide más, ya estamos cerca de penalty
                    # box y conviene esperar lo que diga (mejor que reintentar pronto
                    # y empeorar). Cap superior evita esperas absurdas si la API
                    # devuelve un número raro.
                    wait = min(int(ra), 60) if ra else 5
                except ValueError:
                    wait = 5
                # Cooldown global: el resto de hilos lo respeta y no martillean Spotify
                cooldown_until["t"] = max(cooldown_until["t"], time.time() + wait)
                # Backoff exponencial añadido en reintentos sucesivos para no
                # martillear si el cooldown global no alcanza
                time.sleep(wait + (2 ** attempts) * 0.1)
                continue

            if 500 <= r.status_code < 600 and attempts <= 2:
                time.sleep(2 * attempts)
                continue

            _log_err(isrc, f"http {r.status_code}: {(r.text or '')[:180]}")
            return (isrc, "error", f"http {r.status_code}")

        _log_err(isrc, "rate-limited 429 (agotados 4 intentos con backoff)")
        return (isrc, "error", "rate-limited (4 intentos)")

    results: dict[str, tuple[str, str, str | None]] = {}
    completed = 0
    ok = 0
    nf = 0
    err = 0
    total = len(isrcs)
    last_update = 0.0

    # Aviso inicial antes de bloquear en el pool: si todos los workers caen
    # en throttle/cooldown desde el primer instante (ej. penalty box activo),
    # al menos el usuario ve el batch arrancado en vez de un spinner mudo.
    if progress_cb:
        progress_cb(0, total, f"esperando respuestas… (0/{total})")

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_resolve_one, i): i for i in isrcs}
        for fut in as_completed(futures):
            isrc = futures[fut]
            try:
                res = fut.result()
            except Exception as e:
                res = (isrc, "error", f"exc: {str(e)[:50]}")
            results[isrc] = res
            completed += 1
            kind = res[1]
            if kind == "uri":
                ok += 1
            elif kind == "notfound":
                nf += 1
            else:
                err += 1
            # Throttle UI updates: cada 25 ISRCs o cada 0.5s
            now = time.time()
            if progress_cb and (completed % 25 == 0 or completed == total
                                or now - last_update > 0.5):
                progress_cb(completed, total, f"✅ {ok:,}  ❌ {nf:,}  ⚠️ {err:,}")
                last_update = now

    # Reordenar respetando el orden original
    uris: list[str] = []
    not_found: list[str] = []
    errors: list[tuple[str, str]] = []
    for isrc in isrcs:
        kind, val = results[isrc][1], results[isrc][2]
        if kind == "uri":
            uris.append(val)
        elif kind == "notfound":
            not_found.append(isrc)
        else:
            errors.append((isrc, val or "?"))

    return {"uris": uris, "not_found": not_found, "errors": errors,
            "stopped": False, "reason": ""}


def spotify_create_playlist(name: str, description: str = "", public: bool = False) -> dict | None:
    tok = central_get_access_token()
    if not tok:
        return None
    r = requests.post(
        f"{SP_API}/me/playlists",
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
        json={"name": name, "description": description, "public": bool(public)},
        timeout=15,
    )
    if r.status_code == 401:
        new_tok = central_refresh_access_token()
        if new_tok:
            tok = new_tok
            r = requests.post(
                f"{SP_API}/me/playlists",
                headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                json={"name": name, "description": description, "public": bool(public)},
                timeout=15,
            )
    if r.status_code not in (200, 201):
        _record_sp_error("crear playlist (POST /me/playlists)", r)
        return None
    return r.json()


def handle_spotify_callback():
    """Procesa el callback OAuth de Spotify. Valida HMAC del state para CSRF.
    Si la sesión se perdió durante el round-trip (cookies cross-site en Streamlit Cloud),
    restaura user_email desde el state firmado SOLO si el email es de un admin autorizado
    (`_is_admin()`); en caso contrario, aborta y exige re-login. Limpia query params al final."""
    qp = st.query_params
    code = qp.get("code")
    state = qp.get("state")
    if not code:
        return

    # Validación de state: preferimos verificar HMAC; el viejo formato (token plano
    # en sesión) sigue funcionando por compatibilidad si la sesión sobrevivió.
    payload = _decode_oauth_state(state) if state else None
    expected_state = st.session_state.get("spotify_oauth_state")
    state_ok = (payload is not None) or (expected_state and state == expected_state)
    if not state_ok:
        st.error("OAuth Spotify: state inválido o caducado — vuelve a conectar.")
        st.query_params.clear()
        return

    session_email = st.session_state.get("user_email", "")

    # Si la sesión se perdió en el round-trip OAuth (cookies cross-site Streamlit Cloud),
    # permitir restaurar SOLO si el email del state HMAC es de un admin autorizado.
    # Esto limita el bypass: un atacante solo podría impersonar admins (no users genéricos),
    # y necesita firma HMAC válida (requiere conocer SPOTIFY_CLIENT_SECRET).
    if not session_email and payload and payload.get("u"):
        candidate_email = payload["u"].strip().lower()
        if _is_admin(candidate_email):
            st.session_state.user_email = candidate_email
            st.session_state.login_at = datetime.now(timezone.utc).isoformat()
            session_email = candidate_email

    if not session_email:
        st.error(
            "🔒 Tu sesión expiró durante la autorización Spotify. "
            "Vuelve a iniciar sesión y reintenta el setup."
        )
        st.query_params.clear()
        st.session_state.pop("spotify_oauth_state", None)
        st.stop()

    # Defensa adicional: si hay sesión Y payload, ambos emails deben coincidir.
    if payload and payload.get("u") and payload["u"].strip().lower() != session_email.strip().lower():
        st.error("🔒 Inconsistencia OAuth: el state no corresponde a tu sesión.")
        st.query_params.clear()
        st.session_state.pop("spotify_oauth_state", None)
        st.stop()

    data = spotify_exchange_code(code)
    if data:
        st.session_state.spotify_refresh_token = data.get("refresh_token")
        st.session_state.spotify_access_token = data["access_token"]
        st.session_state.spotify_token_expires = time.time() + int(data.get("expires_in", 3600))
        st.session_state.pop("spotify_central_token_dead", None)
        st.success("✅ Spotify conectado correctamente.")
    else:
        st.error("No se pudo intercambiar el code Spotify. Revisa CLIENT_ID/SECRET en Secrets.")
    st.query_params.clear()


# ════════════════════════════════════════════════════════════════════════════
# BATCH SEARCH
# ════════════════════════════════════════════════════════════════════════════
def parse_isrcs_from_excel(file) -> list[str]:
    """Lee xlsx/csv y devuelve lista de ISRCs únicos, validados."""
    name = (file.name or "").lower()
    if name.endswith(".csv"):
        df = pd.read_csv(file)
    else:
        df = pd.read_excel(file)
    isrc_col = None
    for c in df.columns:
        cn = str(c).strip().lower().replace(" ", "").replace("_", "")
        if cn in ("isrc", "filtrarisrc"):
            isrc_col = c
            break
    if not isrc_col:
        raise ValueError(
            f"No encontré columna ISRC. Columnas: {', '.join(str(c) for c in df.columns)}. "
            "Renombra una a 'ISRC' y vuelve a subir."
        )
    isrcs_raw = df[isrc_col].astype(str).str.strip().str.upper()
    isrcs = sorted({i for i in isrcs_raw
                    if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3}\d{7}", i)})
    return isrcs


def batch_search(isrcs: list[str], platforms: list[str], buster: str = "",
                 progress_cb=None) -> dict:
    """Procesa una lista de ISRCs. Devuelve dict con resumen + playlists agregadas."""
    out_meta = {}
    all_pls = []
    calls = 0
    not_found = []
    for i, isrc in enumerate(isrcs):
        if progress_cb:
            progress_cb(i + 1, len(isrcs), isrc)
        try:
            res = search_isrc(isrc, platforms, buster=buster)
        except Exception as e:
            not_found.append((isrc, f"error: {str(e)[:80]}"))
            continue
        calls += res.get("calls_used", 0)
        if not res.get("meta"):
            not_found.append((isrc, "no en Soundcharts"))
            continue
        out_meta[isrc] = res["meta"]
        for p in res.get("playlists", []):
            p2 = dict(p)
            p2["isrc"] = isrc
            p2["song_name"] = res["meta"].get("song_name") or ""
            p2["credit_name"] = res["meta"].get("credit_name") or ""
            all_pls.append(p2)
    return {
        "meta": out_meta,
        "playlists": all_pls,
        "calls_used": calls,
        "not_found": not_found,
    }


# ════════════════════════════════════════════════════════════════════════════
# UI principal
# ════════════════════════════════════════════════════════════════════════════
def render_playlist_cards(pls_view: list[dict]):
    """Renderiza las cards agrupadas por plataforma."""
    by_plat: dict[str, list[dict]] = {}
    for p in pls_view:
        by_plat.setdefault(p["platform"], []).append(p)
    PLAT_ICONS = {
        "spotify": "🎧", "apple-music": "🍎", "amazon": "🛒", "deezer": "🎵",
        "youtube": "📺", "soundcloud": "☁️", "tidal": "🌊",
        "audiomack": "🎶", "pandora": "📻",
    }
    for plat, items in by_plat.items():
        icon = PLAT_ICONS.get(plat, "🎶")
        st.markdown(f"#### {icon} {plat.title()} · {len(items)} playlists")
        for p in items:
            st.markdown(_build_card_html(p), unsafe_allow_html=True)


PLATFORM_SCOPE_OPTIONS = [
    "Importantes (4)",
    "Todas (9)",
    "spotify",
    "apple-music",
    "amazon",
    "deezer",
    "youtube",
    "soundcloud",
    "tidal",
    "audiomack",
    "pandora",
]


def _platforms_for_scope(scope: str) -> list[str]:
    if scope.startswith("Importantes"):
        return PLATFORMS_DEFAULT
    if scope.startswith("Todas"):
        return PLATFORMS_DEFAULT + PLATFORMS_EXTRA
    # Caso individual: el propio nombre es el platform code
    return [scope]


def tab_individual():
    """Tab 1 — búsqueda de un solo ISRC."""
    col_q, col_plat, col_refresh = st.columns([4, 2, 1])
    with col_q:
        isrc_input = st.text_input("ISRC", placeholder="ej. ES14H2600001",
                                    label_visibility="collapsed")
    with col_plat:
        scope = st.selectbox("Plataformas", PLATFORM_SCOPE_OPTIONS,
                              label_visibility="collapsed", key="indiv_scope")
    with col_refresh:
        if st.button("🔄 Refrescar", width="stretch",
                     help="Ignora cache y consulta Soundcharts ahora."):
            st.cache_data.clear()
            st.rerun()

    platforms = _platforms_for_scope(scope)
    isrc = (isrc_input or "").strip().upper()
    if not isrc:
        st.info("👆 Pega un ISRC arriba (formato 12 chars, ej. `ES14H2600001`).")
        return
    if not re.fullmatch(r"[A-Za-z]{2}[A-Za-z0-9]{3}\d{7}", isrc):
        st.warning(f"`{isrc}` no parece un ISRC válido.")
        return

    max_per_day = int(st.secrets.get("SOUNDCHARTS_MAX_PER_DAY", "5000"))
    if "calls_today" not in st.session_state:
        st.session_state.calls_today = 0
    if st.session_state.calls_today >= max_per_day:
        st.error(f"⚠️ Límite de búsquedas del día ({max_per_day}) alcanzado.")
        return

    buster = st.session_state.get("cache_buster", "")
    t0 = time.time()
    with st.spinner(f"Buscando `{isrc}` en {len(platforms)} plataformas…"):
        try:
            res = search_isrc(isrc, platforms, buster=buster)
        except Exception as e:
            st.error(f"Error: {e}")
            return
    st.session_state.calls_today += res["calls_used"]
    ms = int((time.time() - t0) * 1000)

    meta = res["meta"]
    if not meta:
        st.error(f"❌ Soundcharts no encuentra el ISRC `{isrc}`. Posibles causas:")
        st.markdown(
            "- ISRC mal escrito (verifica letra-letra).\n"
            "- Track aún no indexado por Soundcharts (recién publicado, esperar 24-48h).\n"
            "- ISRC válido pero no distribuido en DSPs aún."
        )
        return

    # Info del track
    st.markdown(f"### {meta.get('song_name') or '—'}")
    bits = []
    if meta.get("credit_name"): bits.append(f"**{meta['credit_name']}**")
    if meta.get("release_date"): bits.append(f"📅 {meta['release_date'][:10]}")
    bits.append(f"🆔 ISRC `{isrc}`")
    st.markdown(" · ".join(bits))

    # KPIs
    pls = res["playlists"]
    n_total = len(pls)
    n_official = sum(1 for p in pls if _is_official_type(p.get("playlist_type")))
    n_user = sum(1 for p in pls if p.get("playlist_type") == "Curators & Listeners")
    n_platforms_with_data = len({p["platform"] for p in pls})
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Total playlists", n_total)
    k2.metric("✨ Oficiales / Algorítmicas", n_official,
              help="Editorial + Algotorial + Algorithmic + Charts")
    k3.metric("👥 User-created", n_user,
              help="Curators & Listeners — no cuentan como editoriales")
    k4.metric("DSPs con datos", f"{n_platforms_with_data} / {len(platforms)}")
    st.caption(f"⏱ {ms} ms · {res['calls_used']} llamadas API consumidas")

    if not pls:
        st.warning(
            "Sin placements actuales en las plataformas consultadas. Cambia "
            "a 'Todas (9)' para ampliar la búsqueda."
        )
        return

    # Filtros
    f1, f2 = st.columns([2, 2])
    with f1:
        all_types = sorted({p.get("playlist_type") or "(sin tipo)" for p in pls})
        types_sel = st.multiselect(
            "Filtrar por tipo",
            all_types,
            default=[t for t in all_types if t != "Curators & Listeners"],
        )
    with f2:
        min_subs = st.number_input("Mínimo subscribers", min_value=0, value=0, step=1000)

    pls_view = [
        p for p in pls
        if (p.get("playlist_type") or "(sin tipo)") in types_sel
        and (p.get("subscriber_count") or 0) >= min_subs
    ]
    pls_view.sort(
        key=lambda p: (p["platform"], -(p.get("subscriber_count") or 0)),
    )

    st.markdown(f"##### {len(pls_view)} playlists tras filtros")
    if not pls_view:
        st.info("Ningún resultado con esos filtros.")
        return
    render_playlist_cards(pls_view)


def tab_batch():
    """Tab 2 — procesado batch de Excel con hasta 500 ISRCs."""
    st.markdown(
        "Sube un Excel/CSV con una columna **`ISRC`**. La app busca cada uno en "
        "Soundcharts y te muestra una tabla unificada con todas las playlists."
    )
    col_up, col_plat = st.columns([3, 1])
    with col_up:
        uploaded = st.file_uploader("Excel con ISRCs", type=["xlsx", "xls", "csv"],
                                     key="batch_upload", label_visibility="collapsed")
    with col_plat:
        scope = st.selectbox("Plataformas", PLATFORM_SCOPE_OPTIONS,
                              key="batch_scope", label_visibility="collapsed")

    if not uploaded:
        st.info(f"Sin Excel subido. Máximo {MAX_BATCH_ISRCS} ISRCs por batch.")
        return

    try:
        isrcs = parse_isrcs_from_excel(uploaded)
    except Exception as e:
        st.error(str(e))
        return

    if not isrcs:
        st.error("Ningún ISRC válido detectado en el Excel.")
        return
    if len(isrcs) > MAX_BATCH_ISRCS:
        st.warning(
            f"Detectados {len(isrcs)} ISRCs pero el máximo permitido es "
            f"{MAX_BATCH_ISRCS}. Se procesarán solo los primeros {MAX_BATCH_ISRCS}."
        )
        isrcs = isrcs[:MAX_BATCH_ISRCS]

    platforms = _platforms_for_scope(scope)
    est_calls = len(isrcs) * (len(platforms) + 1)
    max_per_day = int(st.secrets.get("SOUNDCHARTS_MAX_PER_DAY", "5000"))
    consumed = st.session_state.get("calls_today", 0)

    c1, c2, c3 = st.columns(3)
    c1.metric("ISRCs detectados", f"{len(isrcs)}")
    c2.metric("Llamadas API estimadas", f"~{est_calls:,}",
              help="Aproximación. La cifra real puede bajar si los ISRCs ya estaban en cache.")
    c3.metric("Consumido hoy", f"{consumed} / {max_per_day}")

    if consumed + est_calls > max_per_day:
        st.warning(
            f"⚠️ Procesar este batch puede exceder el límite diario "
            f"({consumed} + {est_calls} > {max_per_day}). Se cortará a mitad."
        )

    if st.button("🚀 Procesar batch", type="primary"):
        buster = st.session_state.get("cache_buster", "")
        prog = st.progress(0.0, text="Empezando…")
        def _cb(i, total, isrc):
            prog.progress(i / max(total, 1), text=f"{i}/{total} — {isrc}")
        res = batch_search(isrcs, platforms, buster=buster, progress_cb=_cb)
        prog.empty()
        st.session_state.calls_today = consumed + res["calls_used"]
        st.session_state.batch_result = res
        st.session_state.batch_isrcs = isrcs
        st.success(f"✅ Procesado: {len(res['meta'])} ISRCs resueltos, "
                   f"{len(res['playlists'])} placements, {res['calls_used']} llamadas API.")

    # Si hay un resultado guardado (de este procesado o de uno previo), mostrarlo
    if st.session_state.get("batch_result"):
        st.divider()
        show_batch_result()


def show_batch_result():
    """Muestra el resultado del último batch (si existe)."""
    res = st.session_state.get("batch_result")
    isrcs = st.session_state.get("batch_isrcs", [])
    if not res:
        return
    pls = res["playlists"]
    n_found = len(res["meta"])
    n_not_found = len(res["not_found"])

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("ISRCs procesados", len(isrcs))
    k2.metric("Resueltos", f"{n_found} / {len(isrcs)}")
    k3.metric("Total playlists", len(pls))
    k4.metric("Llamadas API", res["calls_used"])

    if n_not_found:
        with st.expander(f"⚠️ {n_not_found} ISRCs sin resultado"):
            for isrc, motivo in res["not_found"][:50]:
                st.text(f"{isrc} — {motivo}")

    if not pls:
        st.info("Sin placements en el lote.")
        return

    # Filtros
    f1, f2 = st.columns(2)
    with f1:
        all_types = sorted({p.get("playlist_type") or "(sin tipo)" for p in pls})
        types_sel = st.multiselect(
            "Filtrar por tipo", all_types,
            default=[t for t in all_types if t != "Curators & Listeners"],
            key="batch_types",
        )
    with f2:
        min_subs = st.number_input("Mínimo subscribers", min_value=0, value=0,
                                    step=1000, key="batch_subs")

    pls_view = [
        p for p in pls
        if (p.get("playlist_type") or "(sin tipo)") in types_sel
        and (p.get("subscriber_count") or 0) >= min_subs
    ]
    st.caption(f"Mostrando {len(pls_view):,} / {len(pls):,} placements")

    # Tabla descargable
    df_view = pd.DataFrame(pls_view)
    show_cols = [c for c in [
        "isrc", "song_name", "credit_name", "platform", "playlist_name",
        "playlist_type", "country_code", "subscriber_count", "position",
        "entry_date",
    ] if c in df_view.columns]
    st.dataframe(
        df_view[show_cols].rename(columns={
            "isrc": "ISRC", "song_name": "Canción", "credit_name": "Artista",
            "platform": "Plataforma", "playlist_name": "Playlist",
            "playlist_type": "Tipo", "country_code": "Países",
            "subscriber_count": "Subscribers", "position": "Pos",
            "entry_date": "Entró",
        }),
        width="stretch", hide_index=True, height=500,
    )

    # Descargas: CSV + PDF
    col_dl1, col_dl2 = st.columns(2)
    with col_dl1:
        csv = df_view.to_csv(index=False).encode("utf-8")
        st.download_button(
            "📥 Descargar CSV (todos los placements, sin filtrar)",
            data=csv,
            file_name=f"placements_batch_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
            width="stretch",
        )
    with col_dl2:
        # PDF: solo editoriales, agrupado por canción
        pdf_btn = st.button(
            "📄 Generar PDF (solo editoriales)",
            help="Genera reporte PDF con logo Musicadders, agrupado por canción, "
                 "incluyendo portada de cada playlist. Tarda 10-30s si hay muchas playlists.",
            width="stretch",
        )

    if pdf_btn:
        try:
            from pdf_report import generate_pdf
        except Exception as e:
            st.error(f"Error cargando módulo PDF: {e}")
            return
        with st.spinner("Generando PDF (descargando portadas)…"):
            try:
                pdf_bytes = generate_pdf(pls, res.get("meta") or {})
            except Exception as e:
                st.error(f"Error generando PDF: {e}")
                return
        st.session_state.last_pdf = pdf_bytes

    if st.session_state.get("last_pdf"):
        st.download_button(
            "⬇️ Descargar PDF generado",
            data=st.session_state.last_pdf,
            file_name=f"placements_editoriales_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
            mime="application/pdf",
            width="stretch",
        )

    st.info(
        "💡 Si quieres crear una playlist en Spotify con estos ISRCs, ve a la "
        "pestaña **🎵 Crear playlist Spotify**."
    )

def tab_playlist():
    """Tab 3 — crear playlist Spotify con los ISRCs del batch (o pegados a mano)."""
    cid = st.secrets.get("SPOTIFY_CLIENT_ID", "").strip()
    if not cid:
        st.error(
            "⚠️ Funcionalidad no configurada. Falta `SPOTIFY_CLIENT_ID` y "
            "`SPOTIFY_CLIENT_SECRET` en Streamlit Cloud Secrets, además de "
            "registrar la URL `https://musicadders-isrc.streamlit.app/` como "
            "Redirect URI en developer.spotify.com."
        )
        return

    _tab_playlist_central()


def _tab_playlist_central():
    """Modo central: todas las playlists se crean en la cuenta Spotify configurada."""
    has_central_token = bool(
        str(st.secrets.get("SPOTIFY_CENTRAL_REFRESH_TOKEN", "") or "").strip()
    )

    if not has_central_token:
        # --- PANTALLA DE SETUP ---
        if not _is_admin(st.session_state.get("user_email", "")):
            st.error(
                "🔒 La cuenta central Spotify no está configurada. "
                "Esta configuración solo puede realizarla un administrador. "
                "Contacta a Victor (victor.gimenez@musicadders.com) para que regenere el token."
            )
            return

        st.markdown("### 🔧 Configurar cuenta central Spotify")
        st.markdown(
            "Esta app crea playlists en una cuenta Spotify central única. "
            "Para activarla, conecta esa cuenta una sola vez: se generará un "
            "`refresh_token` que pegarás en Streamlit Secrets."
        )

        expected_id = str(st.secrets.get("SPOTIFY_CENTRAL_EXPECTED_USER_ID", "")).strip()
        if not expected_id:
            st.error(
                "🔒 Falta configurar `SPOTIFY_CENTRAL_EXPECTED_USER_ID` en Streamlit Secrets. "
                "Es obligatorio antes de capturar el refresh_token. "
                "Añade el Spotify user_id de la cuenta central esperada (lo encuentras en https://open.spotify.com/account) "
                "y reinicia la app."
            )
            return

        # Si el callback OAuth ya se procesó en esta sesión, mostramos el token capturado.
        captured_rt = st.session_state.get("spotify_refresh_token")
        if captured_rt:
            # Intentar mostrar a quién pertenece el token (usando helpers per-user
            # que ya tienen el access_token en sesión)
            uid = spotify_user_id()
            display = st.session_state.get("spotify_display_name") or uid or "—"
            email = ""
            if uid:
                r = requests.get(
                    f"{SP_API}/me",
                    headers={"Authorization": f"Bearer {st.session_state.get('spotify_access_token', '')}"},
                    timeout=15,
                )
                if r.status_code == 200:
                    email = r.json().get("email", "")

            authorized_id = uid or ""
            expected_id = str(st.secrets.get("SPOTIFY_CENTRAL_EXPECTED_USER_ID", "")).strip()
            if expected_id and authorized_id != expected_id:
                st.error(
                    f"❌ Cuenta autorizada incorrecta. Se esperaba `{expected_id}`, autorizaste `{authorized_id}`. "
                    "Revoca el acceso en https://www.spotify.com/account/apps y reintenta con la cuenta correcta."
                )
                for k in ("spotify_refresh_token", "spotify_access_token", "spotify_token_expires",
                          "spotify_user_id", "spotify_display_name"):
                    st.session_state.pop(k, None)
                return

            st.success(f"✅ OAuth completado con cuenta: {display}" + (f" ({email})" if email else ""))
            st.markdown("**Copia este refresh_token exactamente:**")
            st.code(captured_rt, language=None)
            if not expected_id:
                st.caption(
                    f'⚠️ Primera configuración: añade también `SPOTIFY_CENTRAL_EXPECTED_USER_ID = "{authorized_id}"` '
                    "a Secrets para que futuros setups se validen automáticamente."
                )
            st.markdown(
                "**Próximos pasos:**\n"
                "1. Entra a https://share.streamlit.io\n"
                "2. Tu app → Settings → Secrets\n"
                "3. Añade: `SPOTIFY_CENTRAL_REFRESH_TOKEN = \"valor_pegado\"`\n"
                f"4. Añade también: `SPOTIFY_CENTRAL_REFRESH_TOKEN_ISSUED = \"{date.today().isoformat()}\"` "
                "— esto activa los avisos de caducidad automáticos (política Spotify: 6 meses).\n"
                "5. La app reinicia automáticamente en ~60s\n"
                "6. Vuelve a este tab y verás el encabezado verde con el nombre de la cuenta Spotify central"
            )
            # Estado terminal: no continuar al flujo de creación
            return

        # Aún no hay OAuth completado: mostrar botón de conexión
        url = spotify_login_url()
        if url:
            st.link_button("Conectar cuenta central Spotify", url, type="primary")
            with st.expander("¿No se abre? Copia este link y pégalo en el navegador"):
                st.code(url, language=None)
        else:
            st.error("No se pudo generar la URL OAuth. Verifica SPOTIFY_CLIENT_ID en Secrets.")
        return

    # --- FLUJO NORMAL: cuenta central configurada ---
    info = central_user_info()
    if info:
        display_name = info.get("display_name") or info.get("id") or "cuenta central"
        st.success(
            f"✅ Cuenta central: **{display_name}** — las playlists se crearán aquí "
            f"y serán accesibles para todo el equipo."
        )
    else:
        if _is_admin(st.session_state.get("user_email", "")):
            st.warning(
                "No se pudo verificar la cuenta central. "
                "El token puede haber expirado, ser inválido o que SPOTIFY_CLIENT_ID/SPOTIFY_CLIENT_SECRET "
                "hayan cambiado. Ve a la pestaña Setup, reconecta la cuenta central y actualiza "
                "SPOTIFY_CENTRAL_REFRESH_TOKEN en Streamlit Secrets."
            )
            _det = st.session_state.get("sp_last_error")
            if _det:
                st.caption(f"🔎 Detalle Spotify: {_det}")
        else:
            st.warning(
                "El servicio de Spotify no está disponible en este momento. "
                "Contacta al administrador (victor.gimenez@musicadders.com)."
            )
        return

    # Fuente de ISRCs
    st.markdown("##### Fuente de ISRCs")
    source = st.radio(
        "Origen",
        ["Subir Excel", "Usar batch reciente", "Pegar lista manual"],
        horizontal=True, label_visibility="collapsed",
    )
    if source == "Usar batch reciente":
        batch_isrcs = st.session_state.get("batch_isrcs", [])
        if not batch_isrcs:
            st.info(
                "No hay batch reciente. Procesa primero un Excel en la pestaña "
                "📊 Procesar Excel, o usa 'Subir Excel' / 'Pegar lista manual'."
            )
            return
        meta = (st.session_state.get("batch_result") or {}).get("meta") or {}
        isrcs = [i for i in batch_isrcs if i in meta]
        st.caption(f"Usando {len(isrcs)} ISRCs del último batch (los que Soundcharts resolvió).")
    elif source == "Subir Excel":
        uploaded = st.file_uploader(
            "Excel/CSV con columna ISRC",
            type=["xlsx", "xls", "csv"],
            key="playlist_upload",
        )
        if not uploaded:
            st.info(
                "Sube un Excel/CSV con una columna `ISRC`. Sin límite de cantidad. "
                "No consume llamadas Soundcharts: solo se resuelve contra Spotify."
            )
            return
        try:
            isrcs = parse_isrcs_from_excel(uploaded)
        except Exception as e:
            st.error(str(e))
            return
        st.caption(f"{len(isrcs)} ISRCs válidos detectados en el archivo.")
    else:
        text = st.text_area(
            "Pega ISRCs (uno por línea o separados por coma/espacio):",
            placeholder="ES14H2600001\nES64E2605990\n...",
            height=160,
        )
        raw = re.split(r"[,\s]+", (text or "").upper())
        isrcs = [i for i in raw if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3}\d{7}", i)]
        st.caption(f"{len(isrcs)} ISRCs válidos detectados.")

    if not isrcs:
        return

    st.markdown("##### Detalles de la playlist")
    col_n, col_p = st.columns([3, 1])
    with col_n:
        default_name = f"Musicadders selección · {datetime.now().strftime('%Y-%m-%d')}"
        pl_name = st.text_input("Nombre", value=default_name)
        pl_desc = st.text_input("Descripción (opcional)",
                                 value=f"Creada desde el buscador Musicadders · {len(isrcs)} ISRCs")
    with col_p:
        pl_public = st.checkbox("Pública", value=False,
                                help="Si NO la marcas, será privada en la cuenta Spotify central.")
        create_btn = st.button("🎵 Crear playlist", type="primary", width="stretch")

    if not create_btn:
        return
    if not pl_name.strip():
        st.error("Pon un nombre a la playlist.")
        return

    # Resolución de ISRCs server-side (Client Credentials)
    prog_bar = st.progress(0.0, text="Resolviendo ISRCs en Spotify...")

    def _progress_cb(done, total, summary):
        prog_bar.progress(done / max(total, 1), text=f"{done}/{total} — {summary}")

    with st.spinner("Resolviendo ISRCs en Spotify..."):
        resolve_result = spotify_resolve_isrcs(isrcs, progress_cb=_progress_cb)

    prog_bar.empty()

    uris = resolve_result.get("uris", [])
    not_found = resolve_result.get("not_found", [])
    errors = resolve_result.get("errors", [])

    c1, c2, c3 = st.columns(3)
    c1.metric("Encontrados en Spotify", len(uris))
    c2.metric("No en Spotify", len(not_found))
    c3.metric("Errores", len(errors))

    if resolve_result.get("stopped"):
        st.error(f"Resolución abortada: {resolve_result.get('reason', '')}")
        return

    if not uris:
        st.warning("Ningún ISRC resolvió a un track Spotify. Revisa que los ISRCs estén distribuidos.")
        return

    # Creación de playlist server-side (token central)
    with st.spinner("Creando playlist en la cuenta central..."):
        pl = spotify_create_playlist(pl_name.strip(), pl_desc.strip(), pl_public)

    if pl is None:
        _is_adm = _is_admin(st.session_state.get("user_email", ""))
        _msg_contact = (
            "El servicio de Spotify necesita reconexión. "
            "Contacta al administrador (victor.gimenez@musicadders.com)."
        )
        _msg_dead = (
            "El refresh token de Spotify ha caducado (política Spotify: 6 meses). "
            "Ve a la pestaña Setup, reconecta la cuenta central Spotify y pega el nuevo "
            "SPOTIFY_CENTRAL_REFRESH_TOKEN en Streamlit Secrets (share.streamlit.io → Settings → Secrets). "
            "Verifica también que SPOTIFY_CLIENT_ID y SPOTIFY_CLIENT_SECRET no hayan cambiado."
        )
        if st.session_state.get("spotify_central_token_dead"):
            st.error(_msg_dead if _is_adm else _msg_contact)
        else:
            tok_check = central_get_access_token()
            # central_get_access_token() puede marcar el token muerto si el refresh
            # devuelve invalid_grant justo durante este diagnóstico → re-leer el flag
            # para mostrar el mensaje preciso en lugar del 401 genérico.
            if st.session_state.get("spotify_central_token_dead"):
                st.error(_msg_dead if _is_adm else _msg_contact)
            elif not tok_check:
                if _is_adm:
                    st.error(
                        "Error 401: el token central ha expirado o es inválido. "
                        "Regenera SPOTIFY_CENTRAL_REFRESH_TOKEN en Streamlit Secrets. "
                        "Verifica también SPOTIFY_CLIENT_ID y SPOTIFY_CLIENT_SECRET."
                    )
                else:
                    st.error(_msg_contact)
            else:
                _det = st.session_state.get("sp_last_error", "")
                if _det:
                    st.error(f"No se pudo crear la playlist. Spotify respondió → {_det}")
                else:
                    st.error(
                        "No se pudo crear la playlist (error desconocido). "
                        "Puede ser un 403 (permisos) o un problema temporal. Inténtalo de nuevo."
                    )
        return

    playlist_id = pl.get("id")
    if not playlist_id:
        st.error("Spotify devolvió respuesta inesperada al crear la playlist.")
        return

    # Añadir tracks por chunks de 100
    total_uris = uris[:10000]  # límite Spotify
    chunks_total = (len(total_uris) + 99) // 100
    add_prog = st.progress(0.0, text="Añadiendo tracks...")
    added = 0
    sess = requests.Session()

    tok_add = central_get_access_token()
    if not tok_add:
        st.error("Token central no disponible para añadir tracks.")
        return

    _fatal_error = None
    for chunk_idx, i in enumerate(range(0, len(total_uris), 100)):
        chunk = total_uris[i:i + 100]
        attempts = 0
        while True:
            attempts += 1
            try:
                r = sess.post(
                    f"{SP_API}/playlists/{playlist_id}/items",
                    headers={"Authorization": f"Bearer {tok_add}", "Content-Type": "application/json"},
                    json={"uris": chunk},
                    timeout=20,
                )
            except requests.exceptions.RequestException as exc:
                _fatal_error = (
                    f"Error de red al añadir canciones en chunk {chunk_idx}/{chunks_total}: "
                    f"{exc.__class__.__name__}. La playlist quedó incompleta."
                )
                break
            if r.status_code in (200, 201):
                added += len(chunk)
                break
            if r.status_code == 401 and attempts == 1:
                new_tok = central_refresh_access_token()
                if not new_tok:
                    _is_adm_add = _is_admin(st.session_state.get("user_email", ""))
                    if st.session_state.get("spotify_central_token_dead"):
                        if _is_adm_add:
                            _fatal_error = (
                                "El refresh token de Spotify ha caducado (política Spotify: 6 meses). "
                                "Ve a la pestaña Setup, reconecta la cuenta central y pega el nuevo "
                                "SPOTIFY_CENTRAL_REFRESH_TOKEN en Streamlit Secrets. "
                                "Verifica también SPOTIFY_CLIENT_ID y SPOTIFY_CLIENT_SECRET."
                            )
                        else:
                            _fatal_error = (
                                "El servicio de Spotify necesita reconexión. "
                                "Contacta al administrador (victor.gimenez@musicadders.com)."
                            )
                    else:
                        if _is_adm_add:
                            _fatal_error = (
                                "401 — token central expiró sin posibilidad de renovación. "
                                "Regenera SPOTIFY_CENTRAL_REFRESH_TOKEN en Streamlit Secrets."
                            )
                        else:
                            _fatal_error = (
                                "El servicio de Spotify necesita reconexión. "
                                "Contacta al administrador (victor.gimenez@musicadders.com)."
                            )
                    break
                tok_add = new_tok
                continue
            if r.status_code == 401:
                _fatal_error = f"401 persistente en chunk {chunk_idx}/{chunks_total} tras refresh."
                break
            if r.status_code == 403:
                _fatal_error = "403 — la cuenta central no tiene permisos suficientes para añadir tracks."
                break
            if r.status_code == 429 and attempts <= 3:
                ra = r.headers.get("Retry-After")
                try:
                    wait = min(int(ra), 30) if ra else 5
                except ValueError:
                    wait = 5
                time.sleep(wait)
                continue
            if r.status_code == 429:
                _fatal_error = f"429 persistente en chunk {chunk_idx}/{chunks_total} tras {attempts} intentos."
                break
            # Cualquier otro código (5xx, etc.)
            _fatal_error = (
                f"add-tracks falló HTTP {r.status_code} en chunk {chunk_idx}/{chunks_total} "
                f"tras {attempts} intentos."
            )
            break
        add_prog.progress((chunk_idx + 1) / chunks_total,
                          text=f"Añadidos {added:,} / {len(total_uris):,} tracks")
        if _fatal_error:
            add_prog.empty()
            pl_url = (pl.get("external_urls") or {}).get("spotify") or ""
            link_md = f" [Abrir en Spotify]({pl_url})" if pl_url else ""
            st.warning(
                f"⚠️ Playlist creada PARCIAL: se añadieron {added:,} de {len(total_uris):,} tracks. "
                f"Causa: {_fatal_error}.{link_md}"
            )
            return

    add_prog.empty()

    pl_url = (pl.get("external_urls") or {}).get("spotify") or ""
    st.success(f"Playlist creada con {added:,} tracks.")
    if pl_url:
        st.link_button("Abrir en Spotify", pl_url, type="primary")
    st.caption(f"La playlist está en la cuenta Spotify central del equipo (**{display_name}**), no en tu cuenta personal.")

def tab_fuga():
    """Tab Catálogo FUGA: busca ISRCs por rango de fechas de lanzamiento
    paginando FUGA en orden descendente. Sin cache: cada búsqueda consulta en vivo."""
    st.markdown("### 📁 Catálogo FUGA — buscar por fecha de lanzamiento")
    st.caption(
        "Busca productos por `consumer_release_date`. La consulta es en vivo: "
        "para rangos cortos (días/semanas) tarda segundos; para rangos largos "
        "(meses/año) hasta 1-2 min."
    )

    creds_ok = bool(st.secrets.get("FUGA_USER", "")) and bool(st.secrets.get("FUGA_PASS", ""))
    if not creds_ok:
        st.error(
            "⚠️ Falta configurar `FUGA_USER` y `FUGA_PASS` en Streamlit Secrets. "
            "Pide ayuda al admin."
        )
        return

    st.markdown("#### Rango de fechas de lanzamiento")
    today = datetime.now().date()
    col_d1, col_d2, col_act = st.columns([2, 2, 1])
    with col_d1:
        default_from = today.replace(month=max(1, today.month - 1))
        date_from = st.date_input("Desde", value=default_from, key="fuga_date_from")
    with col_d2:
        date_to = st.date_input("Hasta", value=today, key="fuga_date_to")
    with col_act:
        st.write("")
        run = st.button("🔍 Buscar", type="primary", width="stretch")

    # Ejecutar búsqueda solo cuando se pulsa el botón. El resultado se persiste
    # en session_state para que los reruns provocados por los filtros NO disparen
    # nuevas llamadas a FUGA ni hagan que la tabla desaparezca.
    if run:
        if date_from > date_to:
            st.error("La fecha 'Desde' es posterior a 'Hasta'.")
            return

        prog = st.progress(0.0, text="Conectando con FUGA…")

        def _cb(page, in_range, msg):
            approx = min(0.95, (page + 1) / 80.0)
            prog.progress(approx, text=msg)

        with st.spinner("Buscando en FUGA…"):
            rows, err = fuga_client.find_isrcs_in_date_range(date_from, date_to, progress_cb=_cb)
        prog.empty()

        st.session_state["fuga_last_result"] = {
            "rows": rows,
            "err": err,
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
        }

    # Recuperar el último resultado (puede venir de este rerun o de uno previo).
    last = st.session_state.get("fuga_last_result")
    if not last:
        return  # aún no se ha pulsado Buscar nunca

    if last.get("err"):
        st.error(last["err"])
        return

    rows = last.get("rows") or []
    last_from = last.get("date_from") or ""
    last_to = last.get("date_to") or ""

    if not rows:
        st.warning(f"No se encontraron tracks lanzados entre {last_from} y {last_to}.")
        return

    df = pd.DataFrame(rows)
    n_releases = df["product_name"].nunique() if "product_name" in df.columns else 0
    st.success(
        f"Encontrados **{len(rows):,} ISRCs únicos** en **{n_releases:,} releases** "
        f"lanzados entre {last_from} y {last_to}."
    )

    # Filtros de búsqueda libre encima de la tabla
    st.markdown("#### 🔎 Filtrar resultados")
    f_col1, f_col2, f_col3 = st.columns(3)
    with f_col1:
        q_artist = st.text_input("Artista contiene", value="", key="fuga_q_artist",
                                  placeholder="ej. Pure Negga")
    with f_col2:
        q_label = st.text_input("Sello contiene", value="", key="fuga_q_label",
                                 placeholder="ej. Rapport")
    with f_col3:
        q_release = st.text_input("Release contiene", value="", key="fuga_q_release",
                                   placeholder="ej. Bora Bora")

    df_view = df.copy()
    if q_artist.strip():
        df_view = df_view[df_view["artist_name"].fillna("").str.contains(
            q_artist.strip(), case=False, regex=False)]
    if q_label.strip():
        df_view = df_view[df_view["label"].fillna("").str.contains(
            q_label.strip(), case=False, regex=False)]
    if q_release.strip():
        df_view = df_view[df_view["product_name"].fillna("").str.contains(
            q_release.strip(), case=False, regex=False)]

    st.caption(f"Mostrando {len(df_view):,} de {len(df):,} ISRCs.")

    st.dataframe(
        df_view.rename(columns={
            "isrc": "ISRC", "product_name": "Release", "artist_name": "Artista",
            "label": "Sello", "release_date": "Fecha lanzamiento",
        }),
        use_container_width=True, hide_index=True, height=400,
    )

    # Descargas: Excel completo + Excel solo ISRC + CSV
    st.markdown("#### 📥 Descargar")
    st.caption("Las descargas respetan los filtros aplicados arriba.")
    col_x1, col_x2, col_x3 = st.columns(3)
    import io as _io

    # Renombrar columnas para todas las descargas
    df_export = df_view.rename(columns={
        "isrc": "ISRC", "product_name": "Release", "artist_name": "Artista",
        "label": "Sello", "release_date": "Fecha lanzamiento",
    })

    with col_x1:
        # Excel con TODAS las columnas (metadata completa)
        buf_full = _io.BytesIO()
        df_export.to_excel(buf_full, index=False, engine="openpyxl")
        buf_full.seek(0)
        st.download_button(
            "📊 Excel completo (todos los datos)",
            data=buf_full.getvalue(),
            file_name=f"fuga_catalogo_{last_from}_a_{last_to}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    with col_x2:
        # Excel con solo ISRC, listo para subir a "Procesar Excel" o "Crear playlist"
        buf_only = _io.BytesIO()
        df_export[["ISRC"]].to_excel(buf_only, index=False, engine="openpyxl")
        buf_only.seek(0)
        st.download_button(
            "🎯 Excel solo ISRC (para subir)",
            data=buf_only.getvalue(),
            file_name=f"fuga_isrcs_{last_from}_a_{last_to}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )
    with col_x3:
        csv = df_export.to_csv(index=False).encode("utf-8")
        st.download_button(
            "📄 CSV completo",
            data=csv,
            file_name=f"fuga_catalogo_{last_from}_a_{last_to}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    st.info(
        "💡 Para usar estos ISRCs en otras pestañas: descarga el **Excel solo ISRC** "
        "y súbelo en **📊 Procesar Excel** o **🎵 Crear playlist Spotify** > Subir Excel."
    )


def tab_admin():
    """Tab de utilidades admin: generador de credencial bcrypt para añadir users.
    Solo visible si el user logueado es admin (`_is_admin()`)."""
    st.markdown("### 🔧 Utilidades de administración")
    st.caption(
        "Solo accesible para administradores. Permite generar un hash bcrypt "
        "para añadir un nuevo user del equipo a Streamlit Secrets."
    )

    st.markdown("#### Generar credencial para nuevo user")
    with st.form("admin_new_user", clear_on_submit=False):
        new_email = st.text_input(
            "Email del nuevo user",
            placeholder="nuevo.usuario@musicadders.com",
            help="Email con el que entrará a la app.",
        )
        new_password = st.text_input(
            "Contraseña",
            type="password",
            help="Contraseña que tendrá ese user. Mínimo 12 caracteres recomendado.",
        )
        submitted = st.form_submit_button("🔐 Generar credencial", type="primary")

    if not submitted:
        return

    email_clean = (new_email or "").strip().lower()
    # Validación estricta del email para evitar TOML injection en la línea generada.
    if not re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", email_clean):
        st.error("Email inválido. Usa el formato estándar `usuario@dominio.com`.")
        return
    if not new_password or len(new_password) < 8:
        st.error("Contraseña demasiado corta. Mínimo 8 caracteres.")
        return

    # Generar hash con bcrypt (cost 12, igual que el login).
    hashed = bcrypt.hashpw(new_password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode()

    # Defensa adicional: rechazar hash con caracteres incompatibles con TOML basic string.
    if '"' in hashed or "\\" in hashed:
        st.error("Hash bcrypt inesperado. Reintenta.")
        return

    st.success("✅ Credencial generada. Cópiala a Streamlit Secrets.")
    st.markdown("**Pega esta línea dentro del bloque `[users]` de Secrets:**")
    toml_line = f'"{email_clean}" = "{hashed}"'
    st.code(toml_line, language="toml")

    st.markdown("**Próximos pasos:**")
    st.markdown(
        f"""
1. Entra a https://share.streamlit.io → tu app → Settings → Secrets.
2. En el bloque `[users]`, añade la línea de arriba.
3. La app reinicia automáticamente en ~60s.
4. Pásale a **{email_clean}** su email + la contraseña que has puesto, por canal privado.
5. Ese user ya puede hacer login y crear playlists con la cuenta central.

Nota: este hash NO se guarda en ningún log ni en session_state — solo se muestra en pantalla.
"""
    )


def main_view():
    user = st.session_state.user_email

    # Header con logout
    col_h, col_logout = st.columns([5, 1])
    with col_h:
        st.markdown(
            f"""
<div class='ma-header'>
  <h1>🎵 Buscador de placements</h1>
  <div class='sub'>Hola, <b>{html.escape(user)}</b></div>
</div>
""",
            unsafe_allow_html=True,
        )
    with col_logout:
        st.write("")
        if st.button("Salir", width="stretch"):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.rerun()

    tab_titles = ["🔍 Buscar 1 ISRC", "📊 Procesar Excel (max 500)", "🎵 Crear playlist Spotify", "📁 Catálogo FUGA"]
    is_admin_user = _is_admin(st.session_state.get("user_email", ""))
    if is_admin_user:
        tab_titles.append("🔧 Admin")

    tabs = st.tabs(tab_titles)
    with tabs[0]:
        tab_individual()
    with tabs[1]:
        tab_batch()
    with tabs[2]:
        tab_playlist()
    with tabs[3]:
        tab_fuga()
    if is_admin_user:
        with tabs[4]:
            tab_admin()


# ════════════════════════════════════════════════════════════════════════════
# Entry point
# ════════════════════════════════════════════════════════════════════════════
# Procesar callback Spotify ANTES de decidir login vs main.
# Si la sesión se perdió durante el round-trip OAuth y el state HMAC corresponde a un admin
# autorizado, se restaura identidad — si no, se aborta con error.
handle_spotify_callback()

if "user_email" not in st.session_state:
    login_view()
else:
    main_view()
