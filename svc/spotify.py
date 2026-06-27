"""
svc/spotify.py — Cliente Spotify para la cuenta central (F4: Crear playlist).

Responsabilidades:
  - Gestión del token central (refresh + acceso, caché en proceso, persistencia en fichero).
  - Token Client Credentials para búsqueda masiva (caché en proceso).
  - Resolución de ISRCs → URIs con token-bucket y política anti-penalty-box portada
    fielmente de app.py (SPOTIFY_MIN_REQ_INTERVAL / SPOTIFY_RA_ABORT_THRESHOLD /
    SPOTIFY_MAX_COOLDOWN).
  - Creación de playlist + añadir tracks por lotes de 100.
  - Helpers OAuth: HMAC state, login_url, exchange_code (con verificación de admin).
  - Persistencia del refresh_token central: svc/data/spotify_central_token.json
    (fichero server-side; NUNCA sale al browser ni a logs).

Variables de entorno requeridas:
  SPOTIFY_CLIENT_ID         — App registrada en developer.spotify.com
  SPOTIFY_CLIENT_SECRET     — Secret de la app
  SPOTIFY_CENTRAL_ADMINS    — Lista CSV de emails con permiso para el setup
  SPOTIFY_CENTRAL_EXPECTED_USER_ID — (Opcional) ID del usuario Spotify esperado
  SPOTIFY_CENTRAL_REFRESH_TOKEN    — (Semilla) Valor inicial si el fichero no existe
  APP_BASE_URL              — URL base del frontend Next (para redirect_uri)

CRÍTICO:
  - CLIENT_SECRET y los tokens de refresh/access NUNCA deben aparecer en logs.
  - El fichero spotify_central_token.json debe estar fuera del repo (gitignore).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import random
import secrets as _secrets_mod
import threading
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ── Constantes Spotify ─────────────────────────────────────────────────────────

SP_API       = "https://api.spotify.com/v1"
SP_TOKEN_URL = "https://accounts.spotify.com/api/token"
SP_AUTH_URL  = "https://accounts.spotify.com/authorize"
SP_SCOPES    = "playlist-modify-public playlist-modify-private user-read-private user-read-email"

# ── Política anti-penalty-box (portada de app.py) ─────────────────────────────

# Retry-After por encima de este umbral → penalty-box de horas → esperar en loop.
# Por debajo → esperar exacto y reintentar (máx _SP_MAX_ATTEMPTS veces).
_SP_RA_ABORT_THRESHOLD  = 120    # segundos
# Token-bucket global: intervalo mínimo entre requests (~1.67 req/s sostenido).
_SP_MIN_REQ_INTERVAL    = 0.60   # segundos entre requests
# Techo de cooldown: nunca registrar más de 2h aunque el Retry-After sea mayor.
_SP_MAX_COOLDOWN        = 7200   # segundos (2h)
# Máximo de reintentos por ISRC (errores de conexión o 5xx).
_SP_MAX_ATTEMPTS        = 3
# Sleep entre lotes de add_tracks_to_playlist para prevenir rate-limit.
_SP_BATCH_SLEEP         = 0.15   # segundos

# ── Estado de cooldown / rate-limit (module-level, proceso único) ──────────────

# _SP_COOLDOWN["until"] es el epoch float hasta el cual Spotify pausó las peticiones.
# Protegido con lock porque puede ser leído/escrito por el worker y el thread principal.
_SP_COOLDOWN: dict[str, float] = {"until": 0.0}
_SP_COOLDOWN_LOCK = threading.Lock()

# Token-bucket: timestamp del último request emitido.
_SP_LAST_REQ: dict[str, float] = {"t": 0.0}
_SP_LAST_REQ_LOCK = threading.Lock()

# ── Caché de tokens en proceso (proceso único, 1 worker Spotify) ───────────────

_CC_TOKEN: dict[str, Any] = {"token": None, "expires": 0.0}
_CC_LOCK  = threading.Lock()

_CENTRAL_AT: dict[str, Any] = {"token": None, "expires": 0.0}
_CENTRAL_AT_LOCK = threading.Lock()

# ── Flag de token central muerto (invalid_grant detectado) ────────────────────
# Se activa cuando central_refresh_access_token recibe invalid_grant.
# Se limpia cuando save_central_token persiste un token nuevo.
# has_central_token() y get_setup_status() lo consultan para fail-closed.

_CENTRAL_TOKEN_DEAD: dict[str, bool] = {"dead": False}
_CENTRAL_TOKEN_DEAD_LOCK = threading.Lock()

# ── Rutas de datos ─────────────────────────────────────────────────────────────

_SVC_DIR     = Path(__file__).parent
_DATA_DIR    = _SVC_DIR / "data"
_TOKEN_STORE = _DATA_DIR / "spotify_central_token.json"

_DATA_DIR.mkdir(parents=True, exist_ok=True)


# ── Persistencia del token central ────────────────────────────────────────────

def load_central_token() -> dict | None:
    """Lee el token central del fichero de store o del env var de semilla.

    Prioridad:
      1. svc/data/spotify_central_token.json (capturado por el flujo OAuth).
      2. SPOTIFY_CENTRAL_REFRESH_TOKEN en el entorno (semilla estática).

    NUNCA expongas el valor devuelto a logs o respuestas HTTP.
    """
    if _TOKEN_STORE.exists():
        try:
            return json.loads(_TOKEN_STORE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error("spotify: error leyendo token store: %s", e)
    # Fallback: env var de semilla
    rt = os.environ.get("SPOTIFY_CENTRAL_REFRESH_TOKEN", "").strip()
    if rt:
        return {"refresh_token": rt}
    return None


def save_central_token(data: dict) -> None:
    """Guarda el token central en disco (server-side, gitignored).

    El fichero se crea con permisos 0600 (solo el propietario del proceso).
    El directorio se crea con 0700.
    Limpia el flag _CENTRAL_TOKEN_DEAD (el token nuevo reemplaza al muerto).

    NUNCA incluyas el token en logs.
    """
    import stat as _stat
    _DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    content = json.dumps(data, indent=2).encode("utf-8")
    # Escribir con permisos restrictivos: open(O_CREAT|O_WRONLY|O_TRUNC, 0600)
    fd = os.open(str(_TOKEN_STORE), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, content)
    finally:
        os.close(fd)
    # Asegurar permisos aunque el fichero ya existiera con umask más amplio
    os.chmod(str(_TOKEN_STORE), _stat.S_IRUSR | _stat.S_IWUSR)
    # El nuevo token reemplaza al muerto: limpiar el flag
    with _CENTRAL_TOKEN_DEAD_LOCK:
        _CENTRAL_TOKEN_DEAD["dead"] = False
    logger.info("spotify: token central guardado en %s", _TOKEN_STORE)


def delete_central_token() -> None:
    """Elimina el token central del fichero y limpia la caché en memoria."""
    _TOKEN_STORE.unlink(missing_ok=True)
    with _CENTRAL_AT_LOCK:
        _CENTRAL_AT["token"] = None
        _CENTRAL_AT["expires"] = 0.0
    logger.info("spotify: token central eliminado.")


def has_central_token() -> bool:
    """True si hay un refresh_token central configurado Y el token no está muerto.

    Fail-closed: si se detectó invalid_grant (token muerto) devuelve False
    aunque el fichero siga existiendo.
    """
    with _CENTRAL_TOKEN_DEAD_LOCK:
        dead = _CENTRAL_TOKEN_DEAD["dead"]
    if dead:
        return False
    store = load_central_token()
    return bool(store and store.get("refresh_token"))


def get_setup_status() -> dict:
    """Estado de la cuenta central para el endpoint de setup.

    Devuelve {connected, account_name, expires_at, token_dead}.
    expires_at=None: los refresh_tokens de Spotify no tienen fecha fija visible
    (caducan por inactividad a los 6 meses).
    token_dead=True: se detectó invalid_grant — el admin debe reconectar.
    """
    with _CENTRAL_TOKEN_DEAD_LOCK:
        dead = _CENTRAL_TOKEN_DEAD["dead"]
    store = load_central_token()
    if not store or not store.get("refresh_token"):
        return {"connected": False, "account_name": None, "expires_at": None, "token_dead": dead}
    return {
        "connected":    not dead,
        "account_name": store.get("account_name"),
        "expires_at":   None,
        "token_dead":   dead,
    }


# ── Helpers de admin ──────────────────────────────────────────────────────────

def is_admin(email: str) -> bool:
    """True si el email está en SPOTIFY_CENTRAL_ADMINS. Fail-closed si no está configurado."""
    admins_raw = os.environ.get("SPOTIFY_CENTRAL_ADMINS", "").strip()
    if not admins_raw:
        return False
    admins = {a.strip().lower() for a in admins_raw.split(",") if a.strip()}
    return (email or "").strip().lower() in admins


# ── Helpers OAuth ─────────────────────────────────────────────────────────────

def _state_secret_key() -> bytes:
    """Clave HMAC para el state OAuth: SHA-256 del CLIENT_SECRET.

    RuntimeError si CLIENT_SECRET no está configurado.
    """
    cs = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if not cs:
        raise RuntimeError("SPOTIFY_CLIENT_SECRET no configurado: el state OAuth no puede firmarse.")
    return hashlib.sha256(cs.encode("utf-8")).digest()


def encode_oauth_state(user_email: str) -> str:
    """Genera un state firmado HMAC con {nonce, email, timestamp}.

    Formato: <base64url-payload>.<hmac-truncado-16hex>
    Válido durante 30 minutos.
    """
    payload = {
        "n": _secrets_mod.token_urlsafe(8),
        "u": user_email or "",
        "t": int(time.time()),
    }
    raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = hmac.new(_state_secret_key(), raw.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{raw}.{sig}"


def decode_oauth_state(state: str) -> dict | None:
    """Verifica la firma HMAC y devuelve el payload.

    Devuelve None si el state es inválido, la firma no coincide o ha caducado (>30 min).
    HMAC con compare_digest para evitar timing attacks.
    """
    if not state or "." not in state:
        return None
    raw, sig = state.rsplit(".", 1)
    try:
        expected = hmac.new(_state_secret_key(), raw.encode(), hashlib.sha256).hexdigest()[:16]
    except RuntimeError:
        return None
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        pad = "=" * (-len(raw) % 4)
        payload = json.loads(base64.urlsafe_b64decode(raw + pad))
    except Exception:
        return None
    if int(time.time()) - int(payload.get("t", 0)) > 1800:
        return None  # state demasiado antiguo (>30 min)
    return payload


def generate_login_url(user_email: str, redirect_uri: str) -> str | None:
    """Genera la URL de autorización OAuth de Spotify.

    redirect_uri DEBE estar registrado en developer.spotify.com.
    Devuelve None si SPOTIFY_CLIENT_ID o CLIENT_SECRET no están configurados.
    """
    cid = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    if not cid:
        return None
    try:
        state = encode_oauth_state(user_email)
    except RuntimeError:
        return None
    params = {
        "client_id":     cid,
        "response_type": "code",
        "redirect_uri":  redirect_uri,
        "scope":         SP_SCOPES,
        "state":         state,
        "show_dialog":   "true",
    }
    return f"{SP_AUTH_URL}?{urllib.parse.urlencode(params)}"


def _fetch_user_info(access_token: str) -> dict:
    """Llama GET /me con el access_token. Devuelve dict (puede estar vacío si falla)."""
    try:
        r = requests.get(
            f"{SP_API}/me",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15,
        )
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        logger.warning("spotify: error en GET /me: %s", e)
    return {}


def exchange_code(code: str, state: str, redirect_uri: str) -> dict:
    """Intercambia el ?code= de Spotify por tokens y guarda el refresh_token.

    Verifica:
      1. Firma HMAC del state (anti-CSRF).
      2. El email del state está en SPOTIFY_CENTRAL_ADMINS.
      3. Si SPOTIFY_CENTRAL_EXPECTED_USER_ID está configurado, verifica que
         la cuenta autorizada coincide.

    Devuelve:
      {ok: True, account_name: str}  — éxito
      {error: str}                    — fallo (sin details sensibles en logs)
    """
    # 1. Verificar state HMAC
    payload = decode_oauth_state(state)
    if payload is None:
        logger.warning("spotify: exchange_code con state inválido o caducado.")
        return {"error": "invalid_state"}

    # 2. Verificar admin
    admin_email = payload.get("u", "")
    if not is_admin(admin_email):
        logger.warning("spotify: exchange_code por email no admin: %s", admin_email)
        return {"error": "not_admin"}

    cid = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    cs  = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if not (cid and cs):
        return {"error": "not_configured"}

    auth = base64.b64encode(f"{cid}:{cs}".encode()).decode()
    try:
        r = requests.post(
            SP_TOKEN_URL,
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type":  "application/x-www-form-urlencoded",
            },
            data={
                "grant_type":   "authorization_code",
                "code":         code,
                "redirect_uri": redirect_uri,
            },
            timeout=20,
        )
    except requests.RequestException as e:
        logger.error("spotify: exchange_code error de red: %s", e)
        return {"error": "network_error"}

    if r.status_code != 200:
        logger.error("spotify: exchange_code HTTP %d", r.status_code)
        return {"error": "exchange_failed"}

    d = r.json()
    rt = d.get("refresh_token")
    if not rt:
        logger.error("spotify: exchange_code no devolvió refresh_token")
        return {"error": "no_refresh_token"}

    at = d.get("access_token")
    expires_in = int(d.get("expires_in", 3600))

    # 3. Obtener información del usuario
    user_info = _fetch_user_info(at) if at else {}
    user_id      = user_info.get("id", "")
    account_name = user_info.get("display_name") or user_id

    # 4. Verificar SPOTIFY_CENTRAL_EXPECTED_USER_ID (obligatorio — fail-closed)
    # Requerir siempre para evitar conectar accidentalmente la cuenta equivocada.
    expected_id = os.environ.get("SPOTIFY_CENTRAL_EXPECTED_USER_ID", "").strip()
    if not expected_id:
        logger.error(
            "spotify: SPOTIFY_CENTRAL_EXPECTED_USER_ID no configurado. "
            "Exchange bloqueado (fail-closed). Configura la variable en .env antes de "
            "hacer el setup."
        )
        return {"error": "expected_user_id_not_configured"}
    if user_id != expected_id:
        logger.error(
            "spotify: account mismatch en exchange_code. expected=%s, got=%s",
            expected_id, user_id,
        )
        return {"error": "account_mismatch"}

    # 5. Guardar en el store (sin loguear el token)
    store = {
        "refresh_token": rt,
        "account_name":  account_name,
        "account_id":    user_id,
        "captured_at":   datetime.now(timezone.utc).isoformat(),
    }
    save_central_token(store)

    # 6. Actualizar caché de access_token en memoria
    if at:
        with _CENTRAL_AT_LOCK:
            _CENTRAL_AT["token"]   = at
            _CENTRAL_AT["expires"] = time.time() + expires_in

    logger.info("spotify: cuenta central conectada: %s (%s)", account_name, user_id)
    return {"ok": True, "account_name": account_name}


# ── Client Credentials token ──────────────────────────────────────────────────

def _fetch_cc_token_raw() -> tuple[str, int] | None:
    """Obtiene un token Client Credentials nuevo de Spotify.

    Thread-safe: no usa estado compartido durante la llamada.
    Devuelve (access_token, expires_in) o None si falla.
    """
    cid = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    cs  = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if not (cid and cs):
        return None
    auth = base64.b64encode(f"{cid}:{cs}".encode()).decode()
    try:
        r = requests.post(
            SP_TOKEN_URL,
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type":  "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials"},
            timeout=15,
        )
    except requests.RequestException as e:
        logger.warning("spotify: error obteniendo CC token: %s", e)
        return None
    if r.status_code != 200:
        logger.warning("spotify: CC token HTTP %d", r.status_code)
        return None
    d = r.json()
    tok = d.get("access_token")
    if not tok:
        return None
    return (tok, int(d.get("expires_in", 3600)))


def get_cc_token() -> str | None:
    """Devuelve un Client Credentials token válido (caché en proceso).

    Thread-safe via _CC_LOCK.
    """
    with _CC_LOCK:
        token   = _CC_TOKEN["token"]
        expires = _CC_TOKEN["expires"]
    if token and time.time() < expires - 60:
        return token
    result = _fetch_cc_token_raw()
    if not result:
        return None
    tok, expires_in = result
    with _CC_LOCK:
        _CC_TOKEN["token"]   = tok
        _CC_TOKEN["expires"] = time.time() + expires_in
    return tok


# ── Token central (cuenta operadora) ──────────────────────────────────────────

def central_refresh_access_token() -> str | None:
    """Renueva el access_token central usando el refresh_token del store.

    En caso de invalid_grant: loguea el error (sin exponer el token) y devuelve None.
    Si el refresh_token se rota (Spotify lo rota en algunas circunstancias),
    actualiza el store automáticamente.
    """
    store = load_central_token()
    if not store or not store.get("refresh_token"):
        logger.warning("spotify: no hay refresh_token central configurado.")
        return None

    rt  = store["refresh_token"]
    cid = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    cs  = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if not (cid and cs):
        logger.error("spotify: SPOTIFY_CLIENT_ID/SECRET no configurados.")
        return None

    auth = base64.b64encode(f"{cid}:{cs}".encode()).decode()
    try:
        r = requests.post(
            SP_TOKEN_URL,
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type":  "application/x-www-form-urlencoded",
            },
            data={"grant_type": "refresh_token", "refresh_token": rt},
            timeout=15,
        )
    except requests.RequestException as e:
        logger.error("spotify: error renovando token central: %s", e)
        return None

    if r.status_code in (400, 401):
        try:
            err = r.json()
        except Exception:
            err = {}
        if err.get("error") == "invalid_grant":
            logger.error(
                "spotify: SPOTIFY_CENTRAL_REFRESH_TOKEN inválido (invalid_grant). "
                "El token ha caducado/revocado. Reconecta la cuenta central via /playlist/setup."
            )
            with _CENTRAL_TOKEN_DEAD_LOCK:
                _CENTRAL_TOKEN_DEAD["dead"] = True
            # Limpiar caché de access token para no usar el token ya inválido
            with _CENTRAL_AT_LOCK:
                _CENTRAL_AT["token"]   = None
                _CENTRAL_AT["expires"] = 0.0
        else:
            logger.error("spotify: error %d en renovación de token central.", r.status_code)
        return None

    if r.status_code != 200:
        logger.error("spotify: HTTP %d renovando token central.", r.status_code)
        return None

    d          = r.json()
    at         = d["access_token"]
    expires_in = int(d.get("expires_in", 3600))

    # Actualizar caché en memoria
    with _CENTRAL_AT_LOCK:
        _CENTRAL_AT["token"]   = at
        _CENTRAL_AT["expires"] = time.time() + expires_in

    # Actualizar store si el refresh_token fue rotado
    new_rt = d.get("refresh_token")
    if new_rt and new_rt != rt:
        logger.warning("spotify: Spotify rotó el refresh_token central. Actualizando store.")
        updated = dict(store)
        updated["refresh_token"] = new_rt
        save_central_token(updated)

    return at


def central_get_access_token() -> str | None:
    """Devuelve un access_token válido de la cuenta central, renovando si caducó."""
    with _CENTRAL_AT_LOCK:
        token   = _CENTRAL_AT["token"]
        expires = _CENTRAL_AT["expires"]
    if token and time.time() < expires - 60:
        return token
    return central_refresh_access_token()


# ── Helpers internos de resolución ────────────────────────────────────────────

def _parse_retry_after(ra: str | None, default: int = 5) -> int:
    """Parsea la cabecera Retry-After de Spotify (int, float o fecha HTTP RFC 7231).

    Devuelve siempre un entero ≥ 1. Si no se puede parsear, devuelve `default`.
    """
    from email.utils import parsedate_to_datetime  # stdlib, seguro importar aquí
    if not ra:
        return default
    ra = ra.strip()
    try:
        return max(1, int(float(ra)))
    except (ValueError, TypeError):
        pass
    try:
        dt = parsedate_to_datetime(ra)
        return max(1, int(dt.timestamp() - time.time()))
    except Exception:
        return default


def _sleep_interruptible(seconds: float, cancel_event: threading.Event | None) -> bool:
    """Duerme `seconds` en intervalos de 0.5s comprobando cancel_event.

    Devuelve True si completó el sleep normalmente, False si fue cancelado.
    """
    if seconds <= 0:
        return True
    deadline = time.time() + seconds
    while True:
        if cancel_event and cancel_event.is_set():
            return False
        remaining = deadline - time.time()
        if remaining <= 0:
            return True
        time.sleep(min(0.5, remaining))


def _wait_for_cooldown(cancel_event: threading.Event | None) -> bool:
    """Bloquea hasta que el cooldown module-level expire o el job sea cancelado.

    Devuelve True si expiró, False si fue cancelado.
    Duerme en intervalos de 1s para no saturar la CPU.
    """
    while True:
        if cancel_event and cancel_event.is_set():
            return False
        with _SP_COOLDOWN_LOCK:
            remaining = _SP_COOLDOWN["until"] - time.time()
        if remaining <= 0:
            return True
        time.sleep(min(1.0, remaining))


# ── Resolución de ISRCs → URIs Spotify ────────────────────────────────────────

def resolve_isrcs(
    isrcs: list[str],
    progress_cb=None,
    cooldown_cb=None,
    cancel_event: threading.Event | None = None,
) -> dict:
    """Resuelve ISRCs → URIs Spotify de forma secuencial con rate-limit.

    Portado fielmente de app.py (spotify_resolve_isrcs). Diferencias:
      - No usa ThreadPoolExecutor interno (el worker ya es un hilo).
      - Acepta cancel_event para parada limpia.
      - Acepta cooldown_cb(until_epoch: float) para notificar el DB del worker.
      - En penalty-box largo: espera en loop (interruptible) y reintenta.
        Si sigue en cooldown tras reintento, marca los restantes como error.

    progress_cb(resolved, total, not_found_count, status_text) — llamado tras cada ISRC.
    cooldown_cb(until_epoch) — llamado cuando Spotify entra en penalty-box (epoch > 0)
                               y cuando sale (epoch == 0).

    Devuelve:
      {
        uris:          list[str],        — Spotify URIs en el mismo orden
        not_found:     list[str],        — ISRCs sin URI en Spotify
        errors:        list[str],        — ISRCs que fallaron
        stopped:       bool,             — True si parada anticipada
        cooldown_until: float,           — epoch de fin de cooldown (0.0 si nada)
      }
    """
    # Gate: si ya estamos en cooldown desde uso anterior, esperar antes de empezar.
    with _SP_COOLDOWN_LOCK:
        cd_gate = _SP_COOLDOWN["until"]
    if cd_gate > time.time():
        logger.info("spotify: resolve arranca con cooldown activo, esperando…")
        if cooldown_cb:
            cooldown_cb(cd_gate)
        if not _wait_for_cooldown(cancel_event):
            return {
                "uris": [], "not_found": [], "errors": list(isrcs),
                "stopped": False, "cooldown_until": 0.0,
            }
        with _SP_COOLDOWN_LOCK:
            _SP_COOLDOWN["until"] = 0.0
        if cooldown_cb:
            cooldown_cb(0.0)

    # Obtener CC token inicial
    cc_tok = get_cc_token()
    if not cc_tok:
        logger.error("spotify: resolve_isrcs sin CC token disponible.")
        return {
            "uris": [], "not_found": [], "errors": list(isrcs),
            "stopped": True, "cooldown_until": 0.0,
        }

    # Referencia mutable para que _resolve_one pueda rotar el token
    cc_tok_ref = {"v": cc_tok}

    uris:      list[str] = []
    not_found: list[str] = []
    errors:    list[str] = []
    resolved = 0
    total    = len(isrcs)

    if progress_cb and total > 0:
        progress_cb(0, total, 0, f"Iniciando resolución de ISRCs… (0/{total})")

    def _resolve_one(isrc: str) -> tuple[str, str]:
        """(kind, value) — kind ∈ {'uri', 'notfound', 'error', 'cooldown_long'}."""
        for attempt in range(1, _SP_MAX_ATTEMPTS + 1):
            # Comprobar cancelación
            if cancel_event and cancel_event.is_set():
                return ("error", "cancelled")

            # Gate de cooldown: otro ISRC puede haberlo activado
            with _SP_COOLDOWN_LOCK:
                cd_now = _SP_COOLDOWN["until"]
            if cd_now > time.time():
                # Ya en cooldown; el bucle exterior lo manejará
                return ("cooldown_long", "cooldown_active")

            # Token-bucket global
            with _SP_LAST_REQ_LOCK:
                elapsed = time.time() - _SP_LAST_REQ["t"]
                gap     = _SP_MIN_REQ_INTERVAL - elapsed
                # Reservamos el slot ANTES de soltar el lock
                _SP_LAST_REQ["t"] = time.time() + max(gap, 0.0)
            if gap > 0:
                if not _sleep_interruptible(gap, cancel_event):
                    return ("error", "cancelled")

            # Jitter para no sincronizar peticiones consecutivas
            time.sleep(random.uniform(0.0, 0.05))

            # Petición a Spotify Search
            try:
                r = requests.get(
                    f"{SP_API}/search",
                    headers={"Authorization": f"Bearer {cc_tok_ref['v']}"},
                    params={"q": f"isrc:{isrc}", "type": "track", "limit": 1},
                    timeout=15,
                )
            except requests.RequestException as e:
                logger.warning("spotify: net error en %s (intento %d): %s", isrc, attempt, str(e)[:80])
                if attempt < _SP_MAX_ATTEMPTS:
                    _sleep_interruptible(0.3 * attempt, cancel_event)
                    continue
                return ("error", f"net:{str(e)[:50]}")

            if r.status_code == 200:
                items = (r.json().get("tracks") or {}).get("items") or []
                if items:
                    return ("uri", items[0]["uri"])
                return ("notfound", "")

            if r.status_code == 401:
                # CC token expirado — rotar
                raw = _fetch_cc_token_raw()
                if raw:
                    with _CC_LOCK:
                        _CC_TOKEN["token"]   = raw[0]
                        _CC_TOKEN["expires"] = time.time() + raw[1]
                    cc_tok_ref["v"] = raw[0]
                if attempt <= 2:
                    continue
                logger.error("spotify: 401 persistente para %s (CC token no renovable).", isrc)
                return ("error", "auth_401")

            if r.status_code == 429:
                ra        = r.headers.get("Retry-After")
                wait_secs = _parse_retry_after(ra)
                now       = time.time()
                with _SP_COOLDOWN_LOCK:
                    _SP_COOLDOWN["until"] = max(
                        _SP_COOLDOWN["until"],
                        min(now + wait_secs, now + _SP_MAX_COOLDOWN),
                    )
                if wait_secs > _SP_RA_ABORT_THRESHOLD:
                    # Pausa larga: señalizar al bucle exterior
                    logger.warning(
                        "spotify: 429 penalty-box largo (%ds) para %s. "
                        "Esperando cooldown en bucle.",
                        wait_secs, isrc,
                    )
                    return ("cooldown_long", str(wait_secs))
                # Pausa corta: esperar y reintentar
                logger.info("spotify: 429 espera %ds para %s (intento %d).", wait_secs, isrc, attempt)
                _sleep_interruptible(wait_secs, cancel_event)
                continue

            if 500 <= r.status_code < 600:
                logger.warning("spotify: HTTP %d para %s (intento %d).", r.status_code, isrc, attempt)
                if attempt < _SP_MAX_ATTEMPTS:
                    _sleep_interruptible(2.0 * attempt, cancel_event)
                    continue
                return ("error", f"http_{r.status_code}")

            logger.warning("spotify: HTTP inesperado %d para %s.", r.status_code, isrc)
            return ("error", f"http_{r.status_code}")

        return ("error", f"max_attempts_{_SP_MAX_ATTEMPTS}")

    # ── Bucle principal ────────────────────────────────────────────────────────
    for i, isrc in enumerate(isrcs):
        if cancel_event and cancel_event.is_set():
            errors.extend(isrcs[i:])
            break

        kind, value = _resolve_one(isrc)

        if kind == "cooldown_long":
            # Penalty-box largo: notificar DB, esperar, reintentar una vez
            with _SP_COOLDOWN_LOCK:
                cd = _SP_COOLDOWN["until"]
            if cooldown_cb:
                cooldown_cb(cd)

            if not _wait_for_cooldown(cancel_event):
                # Cancelado durante la espera: marcar restantes como error
                errors.extend(isrcs[i:])
                break

            # Cooldown expirado: limpiar y notificar
            with _SP_COOLDOWN_LOCK:
                _SP_COOLDOWN["until"] = 0.0
            if cooldown_cb:
                cooldown_cb(0.0)

            # Reintento del mismo ISRC
            kind, value = _resolve_one(isrc)
            if kind == "cooldown_long":
                # Sigue en penalty-box: abortamos los restantes
                logger.error("spotify: penalty-box persistente tras espera. Abortando restantes.")
                errors.extend(isrcs[i:])
                break

        if kind == "uri":
            uris.append(value)
        elif kind == "notfound":
            not_found.append(isrc)
        else:
            errors.append(isrc)

        resolved += 1
        if progress_cb:
            nf_count = len(not_found) + len(errors)
            progress_cb(
                resolved, total, nf_count,
                f"Resolviendo ISRCs ({resolved}/{total})",
            )

    return {
        "uris":          uris,
        "not_found":     not_found,
        "errors":        errors,
        "stopped":       False,
        "cooldown_until": 0.0,
    }


# ── Creación de playlist ───────────────────────────────────────────────────────

def create_playlist(name: str, description: str = "", public: bool = False) -> dict | None:
    """Crea una playlist en la cuenta central.

    Devuelve el objeto playlist de la API de Spotify o None si falla.
    Reintenta una vez si el access_token ha expirado (401).
    """
    tok = central_get_access_token()
    if not tok:
        logger.error("spotify: create_playlist sin token central disponible.")
        return None

    def _do_post(access_token: str) -> requests.Response:
        return requests.post(
            f"{SP_API}/me/playlists",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type":  "application/json",
            },
            json={"name": name, "description": description, "public": bool(public)},
            timeout=15,
        )

    try:
        r = _do_post(tok)
    except requests.RequestException as e:
        logger.error("spotify: create_playlist error de red: %s", e)
        return None

    if r.status_code == 401:
        # Token expirado: renovar y reintentar
        new_tok = central_refresh_access_token()
        if new_tok:
            try:
                r = _do_post(new_tok)
            except requests.RequestException as e:
                logger.error("spotify: create_playlist error de red (reintento): %s", e)
                return None

    if r.status_code not in (200, 201):
        logger.error("spotify: create_playlist HTTP %d: %s", r.status_code, r.text[:200])
        return None

    return r.json()


# ── Añadir tracks a la playlist ───────────────────────────────────────────────

def add_tracks_to_playlist(
    playlist_id: str,
    uris: list[str],
    progress_cb=None,
    cancel_event: threading.Event | None = None,
) -> dict:
    """Añade URIs a la playlist en lotes de 100 (límite de la API de Spotify).

    Manejo de errores por lote (sin pérdida silenciosa):
      - 401: renueva token central y reintenta una vez.
      - 429: respeta Retry-After, duerme interruptiblemente, reintenta una vez.
             Si el reintento también falla, el lote se acumula como "failed".
      - Red / 5xx: acumula como "failed" (no silencia el error con `continue`).

    Sleep de _SP_BATCH_SLEEP entre lotes para prevención proactiva de rate-limit.

    progress_cb(added, total) — llamado tras cada lote exitoso.
    Devuelve {"added": int, "failed": int}.
    """
    BATCH_SIZE = 100
    total  = len(uris)
    added  = 0
    failed = 0

    if total == 0:
        return {"added": 0, "failed": 0}

    tok = central_get_access_token()
    if not tok:
        logger.error("spotify: add_tracks_to_playlist sin token central disponible.")
        return {"added": 0, "failed": total}

    for i in range(0, total, BATCH_SIZE):
        if cancel_event and cancel_event.is_set():
            break

        # Sleep entre lotes (excepto el primero) para prevenir rate-limit
        if i > 0:
            if not _sleep_interruptible(_SP_BATCH_SLEEP, cancel_event):
                break

        batch     = uris[i : i + BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1

        def _do_add(access_token: str, items: list[str]) -> requests.Response:
            return requests.post(
                f"{SP_API}/playlists/{playlist_id}/tracks",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type":  "application/json",
                },
                json={"uris": items},
                timeout=30,
            )

        # Primera petición
        try:
            r = _do_add(tok, batch)
        except requests.RequestException as e:
            logger.error("spotify: add_tracks lote %d error de red: %s", batch_num, e)
            failed += len(batch)
            continue

        # 401: renovar token y reintentar
        if r.status_code == 401:
            new_tok = central_refresh_access_token()
            if new_tok:
                tok = new_tok
                try:
                    r = _do_add(tok, batch)
                except requests.RequestException as e:
                    logger.error(
                        "spotify: add_tracks lote %d error de red (retry 401): %s",
                        batch_num, e,
                    )
                    failed += len(batch)
                    continue
            else:
                logger.error(
                    "spotify: add_tracks lote %d 401 sin token de renovación — "
                    "token central muerto.",
                    batch_num,
                )
                failed += len(batch)
                continue

        # 429: Retry-After + 1 reintento
        if r.status_code == 429:
            ra        = r.headers.get("Retry-After")
            wait_secs = _parse_retry_after(ra, default=5)
            logger.warning(
                "spotify: add_tracks lote %d 429 rate-limit (Retry-After=%ds). "
                "Esperando y reintentando una vez.",
                batch_num, wait_secs,
            )
            if not _sleep_interruptible(wait_secs, cancel_event):
                # Cancelado durante la espera
                failed += len(batch)
                break
            try:
                r = _do_add(tok, batch)
            except requests.RequestException as e:
                logger.error(
                    "spotify: add_tracks lote %d error de red (retry 429): %s",
                    batch_num, e,
                )
                failed += len(batch)
                continue
            if r.status_code not in (200, 201):
                logger.error(
                    "spotify: add_tracks lote %d HTTP %d tras retry 429 — lote acumulado como failed.",
                    batch_num, r.status_code,
                )
                failed += len(batch)
                continue

        # Otros errores (no 200/201, no 401/429 ya manejados arriba)
        elif r.status_code not in (200, 201):
            logger.error(
                "spotify: add_tracks lote %d HTTP %d: %s",
                batch_num, r.status_code, r.text[:200],
            )
            failed += len(batch)
            continue

        added += len(batch)
        if progress_cb:
            progress_cb(added, total)

    logger.info(
        "spotify: add_tracks completado — added=%d, failed=%d, total=%d.",
        added, failed, total,
    )
    return {"added": added, "failed": failed}
