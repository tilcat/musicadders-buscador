"""Buscador ISRC público — Musicadders.

App pequeña standalone Streamlit Cloud para que cualquier trabajador de
Musicadders pueda pegar un ISRC y ver en qué playlists está, en todas las
DSPs que cubre Soundcharts.

Características:
  - Multi-usuario con bcrypt (lista en st.secrets["users"]).
  - Modo LIVE puro: cada búsqueda llama Soundcharts API directamente.
  - Cache en memoria de sesión (st.cache_data) para no duplicar llamadas.
  - Kill-switch diario configurable.
  - Branding Musicadders (gradient verde-cian + logo).
  - Sin BD persistente — funciona en Streamlit Cloud sin volumen.

Variables de entorno necesarias (Streamlit Cloud Secrets UI):
    SOUNDCHARTS_APP_ID = "..."
    SOUNDCHARTS_API_KEY = "..."
    SOUNDCHARTS_MAX_PER_DAY = "5000"   # opcional
    [users]
    "victor@musicadders.com" = "$2b$12$..."   # bcrypt hash
    "ana@musicadders.com"    = "$2b$12$..."
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import bcrypt
import pandas as pd
import requests
import streamlit as st


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
# UI principal
# ════════════════════════════════════════════════════════════════════════════
def main_view():
    user = st.session_state.user_email

    # Header
    st.markdown(
        f"""
<div class='ma-header'>
  <h1>🎵 Buscador de placements</h1>
  <div class='sub'>Hola, <b>{user}</b> · pega un ISRC y mira en qué playlists está</div>
</div>
""",
        unsafe_allow_html=True,
    )

    col_q, col_plat, col_refresh, col_logout = st.columns([4, 2, 1, 1])
    with col_q:
        isrc_input = st.text_input(
            "ISRC", placeholder="ej. ES14H2600001",
            label_visibility="collapsed",
        )
    with col_plat:
        scope = st.selectbox(
            "Plataformas",
            ["Importantes (4)", "Todas (9)"],
            label_visibility="collapsed",
        )
    with col_refresh:
        if st.button("🔄 Refrescar", width="stretch",
                     help="Ignora la cache de 1h y vuelve a consultar Soundcharts ahora mismo."):
            st.session_state.cache_buster = str(time.time())
            st.rerun()
    with col_logout:
        if st.button("Salir", width="stretch"):
            for k in list(st.session_state.keys()):
                del st.session_state[k]
            st.rerun()

    platforms = (PLATFORMS_DEFAULT if scope == "Importantes (4)"
                 else PLATFORMS_DEFAULT + PLATFORMS_EXTRA)

    # Validar ISRC
    isrc = (isrc_input or "").strip().upper()
    if not isrc:
        st.info(
            "👆 Pega un ISRC arriba. Formato típico: **ES** + 3 chars + 7 dígitos "
            "(ej. `ES14H2600001`). Si no sabes el ISRC, búscalo en el reproductor "
            "(Spotify → click derecho → 'Share' → 'Copy Song Link', luego en "
            "https://kid.tools/spotify-isrc o similares)."
        )
        return
    if not re.fullmatch(r"[A-Za-z]{2}[A-Za-z0-9]{3}\d{7}", isrc):
        st.warning(
            f"`{isrc}` no parece un ISRC válido. Formato esperado: 12 chars, "
            f"2 letras + 3 chars + 7 dígitos."
        )
        return

    # Kill-switch
    max_per_day = int(st.secrets.get("SOUNDCHARTS_MAX_PER_DAY", "5000"))
    if "calls_today" not in st.session_state:
        st.session_state.calls_today = 0
    if st.session_state.calls_today >= max_per_day:
        st.error(
            f"⚠️ Se ha alcanzado el límite de búsquedas del día ({max_per_day}). "
            f"Vuelve mañana o contacta con A&R."
        )
        return

    # Búsqueda — buster permite refresh manual sin tocar el cache global
    buster = st.session_state.get("cache_buster", "")
    t0 = time.time()
    with st.spinner(f"Buscando `{isrc}` en {len(platforms)} plataformas…"):
        try:
            res = search_isrc(isrc, platforms, buster=buster)
        except Exception as e:
            st.error(f"Error consultando Soundcharts: {e}")
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

    # Render cards por plataforma
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
                "user" if t == "Curators & Listeners" else
                ""
            )
            subs = p.get("subscriber_count")
            subs_fmt = (
                f"{subs:,}" if subs and subs >= 1000
                else (str(subs) if subs else "—")
            )
            pos = p.get("position") or "—"
            countries = p.get("country_code") or ""
            variantes = f" · {p['n_variantes']} variantes" if p.get("n_variantes", 1) > 1 else ""
            entry = (p.get("entry_date") or "")[:10]
            meta_line = (
                f"{t} · "
                f"pos #{pos} · "
                f"{subs_fmt} subs · "
                f"{countries or 'global'}"
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


# ════════════════════════════════════════════════════════════════════════════
# Entry point
# ════════════════════════════════════════════════════════════════════════════
if "user_email" not in st.session_state:
    login_view()
else:
    main_view()
