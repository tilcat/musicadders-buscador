"""svc/fuga.py — Cliente FUGA API para el microservicio svc/.

Port de fuga_client.py (raíz del repo) con las siguientes diferencias:
- Sin dependencia de Streamlit (st.secrets, st.session_state).
- Credenciales desde os.environ: FUGA_USER / FUGA_PASS.
- Estado de sesión (cookies) en memoria de proceso, thread-safe con Lock.
- API pública idéntica: find_isrcs_in_date_range.
- Añade parámetro cancel_event (threading.Event) para parada limpia del job.
- Añade has_credentials() para comprobación previa en el endpoint.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import date, datetime

import requests

logger = logging.getLogger(__name__)

FUGA_BASE = "https://fugamusic.com/api/v2"
FUGA_PAGE_SIZE = 100        # max permitido por API
FUGA_MAX_PAGES = 600        # cap defensivo: cubre catálogo completo (55k+)
FUGA_REQUEST_TIMEOUT = 30   # segundos por petición HTTP
FUGA_INTER_PAGE_DELAY = 0.05  # 50 ms entre páginas (~20 req/s, bajo el límite de FUGA)
FUGA_SESSION_TTL_SECONDS = 25 * 60

# ── Estado de sesión (reemplaza st.session_state) ─────────────────────────────
# Las cookies se cachean en proceso para no re-autenticar en cada página.
# Un solo lock protege la lectura/escritura del dict.
_session_lock = threading.Lock()
_session_state: dict = {"cookies": None, "ts": 0.0}


# ── Credenciales ──────────────────────────────────────────────────────────────

def _get_credentials() -> tuple[str, str] | None:
    """Lee FUGA_USER / FUGA_PASS del entorno. None si alguno falta."""
    user = (os.environ.get("FUGA_USER") or "").strip()
    pwd  = (os.environ.get("FUGA_PASS") or "").strip()
    if not (user and pwd):
        return None
    return (user, pwd)


def has_credentials() -> bool:
    """True si FUGA_USER y FUGA_PASS están en el entorno."""
    return _get_credentials() is not None


# ── Login / sesión ────────────────────────────────────────────────────────────

def _login_fresh() -> dict | None:
    """Hace login en FUGA y devuelve dict de cookies, o None si falla."""
    creds = _get_credentials()
    if not creds:
        return None
    user, pwd = creds
    try:
        r = requests.post(
            f"{FUGA_BASE}/login/",
            json={"name": user, "password": pwd},
            timeout=FUGA_REQUEST_TIMEOUT,
        )
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    return dict(r.cookies)


def _build_session_from_cookies(cookies: dict) -> requests.Session:
    s = requests.Session()
    for k, v in cookies.items():
        s.cookies.set(k, v)
    return s


def _get_fresh_session() -> requests.Session | None:
    """Devuelve una sesión FUGA válida (reutiliza cookies si no expiró el TTL).

    Thread-safe: lee las cookies bajo lock y las escribe bajo lock.
    La Session se construye fuera del lock para no bloquear durante la
    construcción del objeto.
    """
    with _session_lock:
        cookies = _session_state["cookies"]
        ts      = _session_state["ts"]

    if cookies and (time.time() - ts) < FUGA_SESSION_TTL_SECONDS:
        return _build_session_from_cookies(cookies)

    fresh = _login_fresh()
    if fresh is None:
        return None

    with _session_lock:
        _session_state["cookies"] = fresh
        _session_state["ts"]      = time.time()

    return _build_session_from_cookies(fresh)


# ── Petición con reintentos ───────────────────────────────────────────────────

def _request_with_retry(
    sess: requests.Session,
    url: str,
    params: dict | None = None,
    max_retries: int = 4,
) -> requests.Response | None:
    """GET con re-login en 401 + backoff exponencial en 429/5xx + RequestException."""
    for attempt in range(max_retries):
        try:
            r = sess.get(url, params=params, timeout=FUGA_REQUEST_TIMEOUT)
        except requests.RequestException:
            time.sleep(0.5 + attempt * 0.5)
            continue

        if r.status_code == 401 and attempt < max_retries - 1:
            fresh = _login_fresh()
            if fresh is None:
                return None
            with _session_lock:
                _session_state["cookies"] = fresh
                _session_state["ts"]      = time.time()
            sess.cookies.clear()
            for k, v in fresh.items():
                sess.cookies.set(k, v)
            continue

        if r.status_code == 429:
            ra = r.headers.get("Retry-After")
            try:
                wait = min(int(ra), 60) if ra else 5
            except (ValueError, TypeError):
                wait = 5
            time.sleep(wait + (2 ** attempt) * 0.1)
            continue

        if 500 <= r.status_code < 600 and attempt < max_retries - 1:
            time.sleep(2 ** attempt)
            continue

        return r

    return None


# ── Parseo de productos ───────────────────────────────────────────────────────

def _parse_iso_date(value) -> date | None:
    """Parsea 'YYYY-MM-DD' o 'YYYY-MM-DDTHH:MM:SS' a date. None si inválido."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).split("T")[0]).date()
    except (ValueError, TypeError):
        return None


def _extract_product_rows(products: list[dict]) -> list[dict]:
    """Aplana products → filas {isrc, product_name, artist_name, label, release_date}.

    Deduplicación por ISRC (un producto puede tener varios assets con el mismo ISRC).
    """
    seen: set[str] = set()
    rows: list[dict] = []
    for p in products:
        assets = p.get("assets") or []
        label_name = ""
        label = p.get("label")
        if isinstance(label, dict):
            label_name = label.get("name") or ""
        elif isinstance(label, str):
            label_name = label
        artist_name = p.get("display_artist") or p.get("artist_name") or ""
        for a in assets:
            if not isinstance(a, dict):
                continue
            isrc = (a.get("isrc") or "").strip().upper()
            if not isrc or isrc in seen:
                continue
            seen.add(isrc)
            rows.append({
                "isrc":         isrc,
                "product_name": p.get("name") or "",
                "artist_name":  a.get("display_artist") or artist_name,
                "label":        label_name,
                "release_date": (p.get("consumer_release_date") or "")[:10],
            })
    return rows


# ── Función principal ─────────────────────────────────────────────────────────

def find_isrcs_in_date_range(
    date_from: date,
    date_to: date,
    progress_cb=None,
    cancel_event: threading.Event | None = None,
) -> tuple[list[dict] | None, str | None]:
    """Busca ISRCs cuyo producto tenga consumer_release_date en [date_from, date_to].

    Pagina FUGA en orden descendente por consumer_release_date. Salta productos
    futuros (> date_to), incluye los del rango, y para cuando aparece uno anterior
    a date_from (el resto del catálogo está fuera del rango).

    Args:
        date_from:     Límite inferior del rango (inclusive).
        date_to:       Límite superior del rango (inclusive).
        progress_cb:   Callable(page, releases_in_range, msg). Llamado antes de
                       cada fetch y una vez al final al extraer ISRCs.
        cancel_event:  threading.Event que el worker puede activar para parar el
                       bucle entre páginas. Si se activa, se devuelve el resultado
                       parcial acumulado hasta ese punto.

    Returns:
        (lista de dicts, error_msg). En caso de error la lista es None.
    """
    if date_from > date_to:
        return None, "La fecha 'Desde' es posterior a 'Hasta'."

    sess = _get_fresh_session()
    if sess is None:
        return None, "No se pudo autenticar contra FUGA. Verifica FUGA_USER/FUGA_PASS."

    all_in_range: list[dict] = []
    stopped_early = False
    page = 0

    for page in range(FUGA_MAX_PAGES):
        if progress_cb:
            progress_cb(
                page,
                len(all_in_range),
                f"página {page + 1} · {len(all_in_range)} releases en rango",
            )

        # Comprobar cancelación entre páginas
        if cancel_event and cancel_event.is_set():
            break

        r = _request_with_retry(sess, f"{FUGA_BASE}/products", params={
            "page":      page,
            "page_size": FUGA_PAGE_SIZE,
            "order_by":  "consumer_release_date",
            "order_dir": "desc",
        })
        if r is None:
            return None, f"Sin respuesta de FUGA en página {page + 1}."
        if r.status_code != 200:
            return None, f"FUGA respondió HTTP {r.status_code} en página {page + 1}."

        body  = r.json()
        items = body.get("product") or []
        if not items:
            break

        for p in items:
            d = _parse_iso_date(p.get("consumer_release_date"))
            if d is None:
                continue
            if d > date_to:
                continue    # producto futuro, todavía no en rango
            if d < date_from:
                stopped_early = True
                break       # ya estamos fuera del rango por la izquierda
            all_in_range.append(p)

        if stopped_early:
            break

        # Throttle entre páginas para respetar 40 req/s de FUGA
        time.sleep(FUGA_INTER_PAGE_DELAY)

    if progress_cb:
        progress_cb(page, len(all_in_range), "extrayendo ISRCs…")

    rows = _extract_product_rows(all_in_range)
    return rows, None
