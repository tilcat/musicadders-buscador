"""
svc/soundcharts.py — Cliente Soundcharts desacoplado de Streamlit.

Extrae de app.py las funciones de acceso a la API Soundcharts
(search_isrc, lookup_isrc_to_uuid, get_song_playlists, _dedupe_playlists,
_is_official_type) y parse_isrcs_from_excel, eliminando toda dependencia
de st.cache_data / st.secrets.

Cache en proceso: dict con TTL de 3600 s (igual que el @st.cache_data original).
Credenciales: variables de entorno SOUNDCHARTS_APP_ID y SOUNDCHARTS_API_KEY.

Política 429: si Soundcharts devuelve 429, lanza RuntimeError("Soundcharts 429
rate-limited") — NO se trata como not-found; el caller (jobs.py) lo registra
como error y para el job si procede.
"""

from __future__ import annotations

import io
import logging
import os
import re
import threading
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ── Configuración ─────────────────────────────────────────────────────────────

SC_BASE = "https://customer.api.soundcharts.com"
PLATFORMS_DEFAULT = ["spotify", "apple-music", "amazon", "deezer"]
PLATFORMS_EXTRA = ["youtube", "soundcloud", "tidal", "audiomack", "pandora"]

# TTL del cache en proceso (segundos). Igual que el @st.cache_data original.
_CACHE_TTL = 3600


def _sc_headers() -> dict:
    """Cabeceras de autenticación Soundcharts desde variables de entorno.

    Falla con KeyError claro si las variables no están configuradas —
    preferible a aceptar 401 silencioso y perder llamadas de la cuota.
    """
    app_id = os.environ.get("SOUNDCHARTS_APP_ID", "").strip()
    api_key = os.environ.get("SOUNDCHARTS_API_KEY", "").strip()
    if not app_id or not api_key:
        raise EnvironmentError(
            "SOUNDCHARTS_APP_ID y SOUNDCHARTS_API_KEY deben estar definidas en el entorno."
        )
    return {
        "x-app-id": app_id,
        "x-api-key": api_key,
        "accept": "application/json",
    }


# ── Cache en proceso con TTL ──────────────────────────────────────────────────
#
# Sustituye @st.cache_data(ttl=3600). Estructura:
#   _CACHE[clave] = (timestamp_epoch, valor)
# Thread-safe: el GIL garantiza operaciones de diccionario atómicas para las
# lecturas y escrituras individuales de clave. No usamos lock porque el coste
# de una doble escritura en caso de race es nulo (mismo valor, TTL igual).

_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_LOCK = threading.Lock()


def _cache_get(key: str) -> Any:
    """Devuelve el valor cacheado o _MISS si no existe o ha expirado."""
    entry = _CACHE.get(key)
    if entry is None:
        return _MISS
    ts, val = entry
    if time.time() - ts > _CACHE_TTL:
        with _CACHE_LOCK:
            _CACHE.pop(key, None)
        return _MISS
    return val


def _cache_set(key: str, val: Any) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), val)


class _MissType:
    """Centinela de cache miss (evita confusión con None como valor cacheado)."""
    pass


_MISS = _MissType()


def cache_clear() -> None:
    """Vacía el cache en proceso. Equivale a st.cache_data.clear()."""
    with _CACHE_LOCK:
        _CACHE.clear()


# ── Helpers internos ──────────────────────────────────────────────────────────

def _is_official_type(t: str) -> bool:
    """Categorías que cuentan como 'oficial' (curado por la DSP).

    Incluye: Editorial, Editorial Personalized (Algotorial), Algorithmic, Charts.
    Excluye: This is…, Major label, Radios, Curators & Listeners.
    """
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
    plataforma + nombre + tipo (caso típico de Amazon). Concatena países."""
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
    out = []
    for g in groups.values():
        g["country_code"] = ", ".join(sorted(c for c in g["countries"] if c))
        del g["countries"]
        out.append(g)
    return out


# ── Funciones principales ─────────────────────────────────────────────────────

def lookup_isrc_to_uuid(isrc: str, buster: str = "") -> dict | None:
    """ISRC → metadatos Soundcharts (uuid, song_name, credit_name, release_date).

    Cacheado en proceso con TTL de 3600 s. Si `buster` cambia, fuerza re-fetch
    (equivalente al parámetro _buster de la versión Streamlit).

    Lanza RuntimeError si Soundcharts devuelve 429 (no trata como not-found).
    Devuelve None si el ISRC no existe en Soundcharts (4xx distinto de 429).
    """
    isrc = isrc.strip().upper()
    cache_key = f"isrc:{isrc}:{buster}"
    cached = _cache_get(cache_key)
    if not isinstance(cached, _MissType):
        return cached

    r = requests.get(
        f"{SC_BASE}/api/v2/song/by-isrc/{isrc}",
        headers=_sc_headers(),
        timeout=15,
    )
    if r.status_code == 429:
        raise RuntimeError("Soundcharts 429 rate-limited")
    if r.status_code != 200:
        _cache_set(cache_key, None)
        return None

    obj = (r.json() or {}).get("object") or {}
    resultado = {
        "uuid": obj.get("uuid"),
        "song_name": obj.get("name"),
        "credit_name": obj.get("creditName"),
        "release_date": obj.get("releaseDate"),
    }
    _cache_set(cache_key, resultado)
    return resultado


def get_song_playlists(uuid: str, platform: str, buster: str = "") -> list[dict]:
    """Playlists actuales de un song en una plataforma. Pagina hasta el total.

    Cacheado en proceso con TTL de 3600 s.
    Lanza RuntimeError si Soundcharts devuelve 429.
    Devuelve lista vacía si no hay playlists o el endpoint falla.
    """
    cache_key = f"pls:{uuid}:{platform}:{buster}"
    cached = _cache_get(cache_key)
    if not isinstance(cached, _MissType):
        return cached

    out: list[dict] = []
    offset = 0
    while True:
        r = requests.get(
            f"{SC_BASE}/api/v2.20/song/{uuid}/playlist/current/{platform}",
            headers=_sc_headers(),
            params={
                "limit": 100,
                "offset": offset,
                "currentOnly": 1,
                "sortBy": "subscriberCount",
                "sortOrder": "desc",
            },
            timeout=20,
        )
        if r.status_code == 429:
            raise RuntimeError("Soundcharts 429 rate-limited")
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

    _cache_set(cache_key, out)
    return out


def search_isrc(isrc: str, platforms: list[str], buster: str = "") -> dict:
    """Búsqueda completa de un ISRC en las plataformas indicadas.

    Devuelve:
      {
        "meta": dict | None,      # metadatos del track (uuid, nombre, artista…)
        "playlists": list[dict],  # playlists deduplicadas de todas las plataformas
        "calls_used": int,        # estimación de llamadas API consumidas
      }

    Lanza RuntimeError("Soundcharts 429 rate-limited") si la API responde 429.
    Si el ISRC no existe, devuelve meta=None y playlists=[].
    """
    meta = lookup_isrc_to_uuid(isrc, buster=buster)
    if not meta or not meta.get("uuid"):
        return {"meta": None, "playlists": [], "calls_used": 1}

    uuid = meta["uuid"]
    all_pls: list[dict] = []
    calls = 1  # la llamada de lookup
    for plat in platforms:
        pls = get_song_playlists(uuid, plat, buster=buster)
        all_pls.extend(pls)
        # Estimación: 1 llamada por cada 100 playlists + 1 inicial
        calls += max(1, (len(pls) // 100) + 1)

    return {
        "meta": meta,
        "playlists": _dedupe_playlists(all_pls),
        "calls_used": calls,
    }


def parse_isrcs_from_excel(file_bytes: bytes, filename: str = "") -> list[str]:
    """Lee xlsx/csv desde bytes y devuelve lista de ISRCs únicos y validados.

    A diferencia de la versión Streamlit (que recibía un objeto UploadedFile),
    aquí recibe bytes crudos + nombre de fichero para poder llamarlo desde
    un worker thread sin dependencias de Streamlit.

    Lanza ValueError si no encuentra columna ISRC o el fichero es inválido.
    """
    import pandas as pd

    buf = io.BytesIO(file_bytes)
    name_lower = (filename or "").lower()

    if name_lower.endswith(".csv"):
        df = pd.read_csv(buf)
    else:
        df = pd.read_excel(buf)

    # Buscar columna ISRC de forma flexible (igual que la versión original)
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
    isrcs = sorted({
        i for i in isrcs_raw
        if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3}\d{7}", i)
    })
    return isrcs
