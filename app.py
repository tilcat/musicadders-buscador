"""Buscador ISRC público — Musicadders.

3 modos:
  1. Búsqueda individual: pega 1 ISRC → placements en vivo.
  2. Procesado batch: sube Excel con hasta 500 ISRCs → tabla unificada.
  3. Crear playlist Spotify: cada usuario conecta su Spotify (OAuth) y
     crea playlist con los ISRCs encontrados.

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
import io
import os
import re
import secrets as _secrets_mod
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import bcrypt
import pandas as pd
import requests
import streamlit as st


# ════════════════════════════════════════════════════════════════════════════
# CONSTANTES
# ════════════════════════════════════════════════════════════════════════════
MAX_BATCH_ISRCS = 500
SPOTIFY_SCOPES = "playlist-modify-public playlist-modify-private user-read-private"


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


def _app_base_url() -> str:
    """Base URL exacta de la app (para construir el redirect URI Spotify)."""
    return str(st.secrets.get("APP_BASE_URL", "https://musicadders-isrc.streamlit.app")).rstrip("/")


def spotify_login_url() -> str | None:
    """Genera la URL de autorización Spotify para el user actual."""
    cid = st.secrets.get("SPOTIFY_CLIENT_ID", "").strip()
    if not cid:
        return None
    state = _secrets_mod.token_urlsafe(16)
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


def spotify_create_playlist(name: str, description: str = "", public: bool = False) -> dict | None:
    tok = spotify_get_token()
    uid = spotify_user_id()
    if not (tok and uid):
        return None
    r = requests.post(
        f"{SP_API}/users/{uid}/playlists",
        headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
        json={"name": name, "description": description, "public": bool(public)},
        timeout=15,
    )
    if r.status_code != 201:
        return None
    return r.json()


def spotify_add_tracks(playlist_id: str, uris: list[str]) -> int:
    tok = spotify_get_token()
    if not tok:
        return 0
    added = 0
    for i in range(0, len(uris), 100):
        chunk = uris[i:i+100]
        r = requests.post(
            f"{SP_API}/playlists/{playlist_id}/tracks",
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
            json={"uris": chunk},
            timeout=20,
        )
        if r.status_code in (200, 201):
            added += len(chunk)
    return added


def handle_spotify_callback():
    """Si la URL trae ?code=...&state=..., intercambia y guarda el refresh_token.
    Limpia los query params después para que el reload no re-intercambie."""
    qp = st.query_params
    code = qp.get("code")
    state = qp.get("state")
    if not code:
        return
    expected_state = st.session_state.get("spotify_oauth_state")
    if expected_state and state != expected_state:
        st.error("OAuth Spotify: state mismatch — vuelve a conectar.")
        st.query_params.clear()
        return
    data = spotify_exchange_code(code)
    if data:
        st.session_state.spotify_refresh_token = data.get("refresh_token")
        st.session_state.spotify_access_token = data["access_token"]
        st.session_state.spotify_token_expires = time.time() + int(data.get("expires_in", 3600))
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
            t = p.get("playlist_type") or ""
            css_class = (
                "algorithmic" if "algorithmic" in t.lower() or "algotorial" in t.lower() else
                "charts" if "chart" in t.lower() else
                "user" if t == "Curators & Listeners" else ""
            )
            subs = p.get("subscriber_count")
            subs_fmt = f"{subs:,}" if subs and subs >= 1000 else (str(subs) if subs else "—")
            pos = p.get("position") or "—"
            countries = p.get("country_code") or ""
            variantes = f" · {p['n_variantes']} variantes" if p.get("n_variantes", 1) > 1 else ""
            entry = (p.get("entry_date") or "")[:10]
            meta_line = (
                f"{t} · pos #{pos} · {subs_fmt} subs · {countries or 'global'}"
                f"{variantes}"
                f"{' · entró ' + entry if entry else ''}"
            )
            st.markdown(
                f"<div class='ma-pl-card {css_class}'>"
                f"<div class='pl-name'>{p.get('playlist_name','?')}</div>"
                f"<div class='pl-meta'>{meta_line}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )


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
            st.session_state.cache_buster = str(time.time())
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

    # Conexión Spotify del usuario
    if not st.session_state.get("spotify_refresh_token"):
        url = spotify_login_url()
        st.markdown(
            "Para crear playlists necesitas conectar tu cuenta Spotify (1 sola vez):"
        )
        # st.link_button abre en la misma pestaña (preferible para OAuth):
        # mantiene la sesión y al volver con ?code= se procesa automáticamente.
        st.link_button("🎵 Conectar mi cuenta Spotify", url, type="primary")
        st.caption(
            "Te llevará a Spotify para autorizar. Tras autorizar volverás aquí y verás "
            "confirmación. Si tu navegador tiene bloqueador (uBlock, Brave Shields, "
            "AdBlock…) que bloquea accounts.spotify.com, **whitelistéalo** o usa "
            "modo incógnito **abriendo esta URL ahí**: " + (st.secrets.get("APP_BASE_URL", "") or "")
        )
        with st.expander("¿No se abre? Copia este link y pégalo en el navegador"):
            st.code(url, language=None)
        return

    # Ya conectado
    uid = spotify_user_id()
    if not uid:
        st.warning("Token Spotify caducado. Vuelve a conectar.")
        if st.button("🔁 Reconectar Spotify"):
            for k in ("spotify_refresh_token", "spotify_access_token",
                      "spotify_token_expires", "spotify_user_id", "spotify_display_name"):
                st.session_state.pop(k, None)
            st.rerun()
        return

    display = st.session_state.get("spotify_display_name") or uid
    col_s1, col_s2 = st.columns([4, 1])
    with col_s1:
        st.success(f"✅ Spotify conectado: **{display}**")
    with col_s2:
        if st.button("🔌 Desconectar"):
            for k in ("spotify_refresh_token", "spotify_access_token",
                      "spotify_token_expires", "spotify_user_id", "spotify_display_name"):
                st.session_state.pop(k, None)
            st.rerun()

    # Fuente de ISRCs: batch reciente, subir Excel o pegar a mano
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
        # Filtrar a los que SÍ se resolvieron en Soundcharts
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
                                help="Si NO la marcas, será privada en tu cuenta Spotify.")
        create_btn = st.button("🎵 Crear playlist", type="primary", width="stretch")

    if not create_btn:
        return
    if not pl_name.strip():
        st.error("Pon un nombre a la playlist.")
        return

    # Resolver ISRCs a URIs Spotify
    prog = st.progress(0.0, text="Resolviendo ISRCs en Spotify…")
    uris = []
    missing = []
    for i, isrc in enumerate(isrcs):
        prog.progress((i + 1) / len(isrcs), text=f"{i+1}/{len(isrcs)} — {isrc}")
        uri = spotify_find_uri_by_isrc(isrc)
        if uri:
            uris.append(uri)
        else:
            missing.append(isrc)
    prog.empty()

    if not uris:
        st.error("Ningún ISRC resolvió a un track Spotify. Nada que crear.")
        return

    with st.spinner(f"Creando playlist con {len(uris)} tracks…"):
        pl = spotify_create_playlist(pl_name.strip(), pl_desc.strip(), public=pl_public)
    if not pl:
        st.error("No se pudo crear la playlist. ¿Token Spotify expirado?")
        return

    with st.spinner("Añadiendo tracks…"):
        added = spotify_add_tracks(pl["id"], uris)

    url = (pl.get("external_urls") or {}).get("spotify")
    st.success(
        f"✅ Playlist creada con **{added}** tracks de {len(isrcs)} ISRCs. "
        f"{len(missing)} no se encontraron en Spotify."
    )
    if url:
        st.markdown(f"### 🔗 [Abrir la playlist en Spotify]({url})")
    if missing:
        with st.expander(f"⚠️ {len(missing)} ISRCs no encontrados en Spotify"):
            st.text("\n".join(missing))


def main_view():
    user = st.session_state.user_email

    # Procesar callback Spotify si la URL trae ?code=
    handle_spotify_callback()

    # Header con logout
    col_h, col_logout = st.columns([5, 1])
    with col_h:
        st.markdown(
            f"""
<div class='ma-header'>
  <h1>🎵 Buscador de placements</h1>
  <div class='sub'>Hola, <b>{user}</b></div>
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

    tab1, tab2, tab3 = st.tabs([
        "🔍 Buscar 1 ISRC",
        f"📊 Procesar Excel (max {MAX_BATCH_ISRCS})",
        "🎵 Crear playlist Spotify",
    ])
    with tab1:
        tab_individual()
    with tab2:
        tab_batch()
    with tab3:
        tab_playlist()


# ════════════════════════════════════════════════════════════════════════════
# Entry point
# ════════════════════════════════════════════════════════════════════════════
if "user_email" not in st.session_state:
    login_view()
else:
    main_view()
