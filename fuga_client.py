"""Cliente FUGA API mínimo para Musicadders Buscador.

Estrategia: paginar el catálogo FUGA ordenado descendente por
`consumer_release_date` y parar tan pronto como salimos del rango pedido.
Para rangos típicos (30-90 días sobre catálogo de 55k+ productos)
visitamos 10-30 páginas (~5-30s), sin necesidad de cache a disco.

Login simple sin CSRF (probado contra https://fugamusic.com/api/v2/).
"""
import time
from datetime import date, datetime

import requests
import streamlit as st


FUGA_BASE = "https://fugamusic.com/api/v2"
FUGA_PAGE_SIZE = 100  # max permitido por API
FUGA_MAX_PAGES = 600  # cap defensivo: cubre catálogo completo (55k+)
FUGA_REQUEST_TIMEOUT = 30
FUGA_INTER_PAGE_DELAY = 0.05  # 50ms entre páginas: ~20 req/s, bajo el 40 req/s de FUGA
FUGA_SESSION_TTL_SECONDS = 25 * 60

_COOKIES_KEY = "fuga_cookies"
_COOKIES_TS_KEY = "fuga_cookies_ts"


def _get_credentials() -> tuple[str, str] | None:
    user = (st.secrets.get("FUGA_USER", "") or "").strip()
    pwd = (st.secrets.get("FUGA_PASS", "") or "").strip()
    if not (user and pwd):
        return None
    return (user, pwd)


def _login_fresh() -> dict | None:
    """Hace login en FUGA y devuelve un dict de cookies serializable, o None."""
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
    """Construye una requests.Session aplicándole las cookies."""
    s = requests.Session()
    for k, v in cookies.items():
        s.cookies.set(k, v)
    return s


def _get_fresh_session() -> requests.Session | None:
    """Devuelve una sesión FUGA válida.

    Guarda en session_state solo dict de cookies (serializable). La Session
    se construye on-demand para evitar problemas de hot-reload con sockets
    muertos.
    """
    cookies = st.session_state.get(_COOKIES_KEY)
    ts = st.session_state.get(_COOKIES_TS_KEY, 0)
    if cookies and (time.time() - ts) < FUGA_SESSION_TTL_SECONDS:
        return _build_session_from_cookies(cookies)
    fresh = _login_fresh()
    if fresh is None:
        return None
    st.session_state[_COOKIES_KEY] = fresh
    st.session_state[_COOKIES_TS_KEY] = time.time()
    return _build_session_from_cookies(fresh)


def _request_with_retry(sess: requests.Session, url: str,
                        params: dict | None = None,
                        max_retries: int = 4) -> requests.Response | None:
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
            st.session_state[_COOKIES_KEY] = fresh
            st.session_state[_COOKIES_TS_KEY] = time.time()
            sess.cookies.clear()
            for k, v in fresh.items():
                sess.cookies.set(k, v)
            continue
        if r.status_code == 429:
            ra = r.headers.get("Retry-After")
            try:
                wait = min(int(ra), 60) if ra else 5
            except ValueError:
                wait = 5
            time.sleep(wait + (2 ** attempt) * 0.1)
            continue
        if 500 <= r.status_code < 600 and attempt < max_retries - 1:
            time.sleep(2 ** attempt)
            continue
        return r
    return None


def _parse_iso_date(value) -> date | None:
    """Parsea 'YYYY-MM-DD' o 'YYYY-MM-DDTHH:MM:SS' a date. None si inválido."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).split("T")[0]).date()
    except (ValueError, TypeError):
        return None


def _extract_product_rows(products: list[dict]) -> list[dict]:
    """Aplana products → filas {isrc, product_name, artist_name, label,
    release_date} dedup por ISRC."""
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
                "isrc": isrc,
                "product_name": p.get("name") or "",
                "artist_name": a.get("display_artist") or artist_name,
                "label": label_name,
                "release_date": (p.get("consumer_release_date") or "")[:10],
            })
    return rows


def find_isrcs_in_date_range(
    date_from: date,
    date_to: date,
    progress_cb=None,
) -> tuple[list[dict] | None, str | None]:
    """Busca ISRCs cuyo product tenga consumer_release_date en [date_from, date_to].

    Pagina FUGA en orden descendente por consumer_release_date. Para cada
    producto: salta los futuros (date > date_to), incluye los del rango,
    y para cuando aparece uno anterior a date_from (resto del catálogo está
    fuera del rango).

    Returns: (lista de dicts, error_msg). En caso de error, lista es None.
    """
    if date_from > date_to:
        return None, "La fecha 'Desde' es posterior a 'Hasta'."

    sess = _get_fresh_session()
    if sess is None:
        return None, ("No se pudo autenticar contra FUGA. Verifica "
                      "FUGA_USER/FUGA_PASS en Streamlit Secrets.")

    all_in_range: list[dict] = []
    stopped_early = False
    page = 0

    for page in range(FUGA_MAX_PAGES):
        if progress_cb:
            progress_cb(page, len(all_in_range),
                        f"página {page + 1} · {len(all_in_range)} en rango")

        r = _request_with_retry(sess, f"{FUGA_BASE}/products", params={
            "page": page,
            "page_size": FUGA_PAGE_SIZE,
            "order_by": "consumer_release_date",
            "order_dir": "desc",
        })
        if r is None:
            return None, f"Sin respuesta de FUGA en página {page + 1}."
        if r.status_code != 200:
            return None, f"FUGA respondió HTTP {r.status_code} en página {page + 1}."

        body = r.json()
        items = body.get("product") or []
        if not items:
            break

        for p in items:
            d = _parse_iso_date(p.get("consumer_release_date"))
            if d is None:
                continue
            if d > date_to:
                continue  # producto futuro, todavía no en rango
            if d < date_from:
                stopped_early = True
                break  # ya estamos fuera del rango por la izquierda
            all_in_range.append(p)
        if stopped_early:
            break

        # Throttle entre páginas para respetar 40 req/s de FUGA
        time.sleep(FUGA_INTER_PAGE_DELAY)

    if progress_cb:
        progress_cb(page, len(all_in_range), "extrayendo ISRCs…")

    rows = _extract_product_rows(all_in_range)
    return rows, None
