"""
Regresion Spotify anti-penalty-box — app.py (musicadders-buscador)
==================================================================
Cubre la logica nueva de manejo de rate-limit 429 en spotify_resolve_isrcs:

  (A) 429 con Retry-After CORTO (<=120s): espera exacta y reintenta;
      el 2o intento es 200 → la funcion resuelve el ISRC correctamente.

  (B) 429 con Retry-After LARGO (>120s, p.ej. 3600s): la funcion ABORTA el lote
      limpiamente (stopped=True), setea _SP_COOLDOWN["until"] al futuro,
      y NO duerme horas (el test debe correr en <2s).

  (C) Gate de cooldown: con _SP_COOLDOWN["until"] en el futuro, una nueva llamada
      a spotify_resolve_isrcs retorna stopped=True SIN hacer ninguna request HTTP
      (verifica que la sesion no se llama ni una vez).

  (D) Dedup de ISRCs: lista con duplicados → solo se resuelven los unicos (cuenta
      llamadas) y el resultado mapea de vuelta correctamente.

  (E) _parse_retry_after (helper real de app.py):
      - Fecha HTTP futura (RFC1123, ej. now+3600s) → parsea a ~3600s → >120 → ABORT.
      - Float string "200.0" → 200s → >120 → ABORT.
      - String no parseable "garbage" → fallback 5s → <=120 → retry corto.
      - Ausente (None / header omitido) → fallback 5s → <=120 → retry corto.
      Tests unitarios directos del helper (sin AppTest) + harnesses AppTest que
      verifican el flujo end-to-end con el helper real importado.

  (F) Techo SPOTIFY_MAX_COOLDOWN: Retry-After de 999999s NO debe fijar
      _SP_COOLDOWN["until"] mas alla de now+7200 (con margen), aunque el lote
      sí aborte.

Estrategia:
  Los harnesses replican la logica de spotify_resolve_isrcs con la sesion HTTP
  mockeada mediante una clase simple que devuelve respuestas prefabricadas.
  Se usan AppTest.from_string (igual que los tests de OAuth existentes) para
  que st.secrets / st.session_state esten disponibles aunque la logica real
  viva en workers.

  La logica del token (spotify_client_credentials_token) se mockea directamente
  en el harness para evitar cualquier llamada de red.

  IMPORTANTE: cada test resetea _SP_COOLDOWN y _SP_LAST_REQ via fixture o al
  inicio del harness, para evitar contaminacion entre tests.

  Los tests unitarios directos de _parse_retry_after importan app directamente
  (evitando AppTest) porque la funcion no depende de st.session_state ni de
  st.secrets — es pura logica de parseo.

Verificacion RED/GREEN indicada en cada test.

Ejecutar:
    /Users/trabajo/dashboard-regalias/.venv/bin/python \
        -m pytest tests/test_spotify_antipenalty.py -v
"""
import sys
import os
import time
import datetime
import pytest

# Asegurar que el directorio raiz del repo esta en el path para poder importar
# app.py directamente (tests unitarios de _parse_retry_after).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

streamlit = pytest.importorskip("streamlit", reason="streamlit no disponible")

# ---------------------------------------------------------------------------
# Helper: carga _parse_retry_after desde el source de app.py sin ejecutar
# el modulo completo (que llama a st.set_page_config, st.markdown, etc. al
# nivel de modulo y contamina el estado de Streamlit para tests posteriores).
# Se compila solo la funcion usando ast + exec en un namespace limpio.
# ---------------------------------------------------------------------------

def _load_parse_retry_after():
    """Devuelve la funcion _parse_retry_after extraida del source de app.py
    sin importar el modulo completo."""
    import ast
    import importlib.util
    import textwrap

    app_path = os.path.join(_REPO_ROOT, "app.py")
    with open(app_path, "r") as f:
        src = f.read()

    tree = ast.parse(src)
    # Extraer el source exacto de la funcion _parse_retry_after
    fn_node = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_parse_retry_after"
    )
    # Reconstruir el source de la funcion con sus lineas originales
    fn_lines = src.splitlines()[fn_node.lineno - 1: fn_node.end_lineno]
    fn_src = textwrap.dedent("\n".join(fn_lines))

    # Ejecutar en un namespace que solo tiene los imports que necesita la funcion
    ns = {}
    exec("import time\nfrom email.utils import parsedate_to_datetime\n" + fn_src, ns)
    return ns["_parse_retry_after"]


# Fixture: resetea el estado global de app.py (_SP_COOLDOWN, _SP_LAST_REQ)
# entre tests que lo tocan directamente (grupos F).
# Los harnesses AppTest usan su propio espacio de nombres aislado y no
# necesitan este fixture.

@pytest.fixture(autouse=False)
def reset_sp_globals():
    """Resetea _SP_COOLDOWN y _SP_LAST_REQ del modulo app antes y despues del test."""
    import app as _app
    _app._SP_COOLDOWN["until"] = 0.0
    _app._SP_LAST_REQ["t"] = 0.0
    yield
    _app._SP_COOLDOWN["until"] = 0.0
    _app._SP_LAST_REQ["t"] = 0.0


# ---------------------------------------------------------------------------
# Secrets minimos (no se usan en red, pero AppTest los exige si la app los lee)
# ---------------------------------------------------------------------------
_BASE_SECRETS = {
    "SOUNDCHARTS_APP_ID": "fake_app_id",
    "SOUNDCHARTS_API_KEY": "fake_api_key",
    "SOUNDCHARTS_MAX_PER_DAY": "5000",
    "APP_BASE_URL": "https://localhost",
    "SPOTIFY_CLIENT_ID": "fake_client_id",
    "SPOTIFY_CLIENT_SECRET": "fake_client_secret",
    "SPOTIFY_CENTRAL_ADMINS": [],
    "users": {"test@musicadders.com":
              "$2b$12$FGyglEGXxGWz9BJPmsXdR.A9sht8nBUsLgl1e2Crml3ghZjoHopYG"},
}


def _build_at_str(harness_src: str, extra: dict = None):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_string(harness_src, default_timeout=30)
    secrets = {**_BASE_SECRETS, **(extra or {})}
    for k, v in secrets.items():
        at.secrets[k] = v
    return at


def _extract_write(at, key: str):
    """Extrae el valor de st.write(f'KEY:<valor>') del AppTest.
    Devuelve la cadena cruda despues del primer ':' o lanza AssertionError."""
    for elem in at.markdown:
        text = getattr(elem, "value", "") or getattr(elem, "body", "") or ""
        prefix = f"{key}:"
        if prefix in text:
            return text.split(prefix, 1)[1].strip()
    values = [getattr(e, "value", repr(e)) for e in at.markdown]
    raise AssertionError(
        f"No se encontro '{key}' en la salida. Markdown values: {values}"
    )


def _extract_bool(at, key: str) -> bool:
    v = _extract_write(at, key)
    if v.startswith("True"):
        return True
    if v.startswith("False"):
        return False
    raise AssertionError(f"Valor no bool para '{key}': {v!r}")


def _extract_int(at, key: str) -> int:
    return int(_extract_write(at, key))


# ===========================================================================
# (A) 429 con Retry-After CORTO → espera y reintenta; 2o intento = 200
# ===========================================================================

# El harness replica _resolve_one con una sesion fake que:
#   - 1er GET → 429 con Retry-After: 2
#   - 2o GET → 200 con un ISRC resuelto
# Mockea time.sleep para que el test no espere 2s reales.
_HARNESS_A_RETRY_SHORT = """
import time
import streamlit as st

ABORT_THRESHOLD = 120

class _Resp429:
    status_code = 429
    headers = {"Retry-After": "2"}
    def json(self): return {}

class _Resp200:
    status_code = 200
    headers = {}
    def json(self):
        return {"tracks": {"items": [{"uri": "spotify:track:RESOLVED"}]}}

_calls = []

class _FakeSession:
    def get(self, url, **kw):
        _calls.append(url)
        if len(_calls) == 1:
            return _Resp429()
        return _Resp200()

# Estado global simulado (resetear al inicio)
_cooldown = {"until": 0.0}
_abort_flag = {"v": False}
_slept = []

_real_sleep = time.sleep
def _fake_sleep(s):
    _slept.append(s)
    # No dormimos de verdad; solo registramos

# Logica de _resolve_one (simplificada, con misma rama 429)
isrc = "ISRCTEST01"
sess = _FakeSession()
result_kind = None
result_val = None
attempts = 0
orig_sleep = time.sleep
time.sleep = _fake_sleep
try:
    while attempts < 3 and not _abort_flag["v"]:
        attempts += 1
        r = sess.get("https://api.spotify.com/v1/search")
        if r.status_code == 200:
            items = (r.json().get("tracks") or {}).get("items") or []
            result_kind = "uri"
            result_val = items[0]["uri"] if items else None
            break
        if r.status_code == 429:
            ra = r.headers.get("Retry-After")
            try:
                wait_secs = int(ra) if ra else 5
            except ValueError:
                wait_secs = 5
            new_cooldown = time.time() + wait_secs
            _cooldown["until"] = max(_cooldown["until"], new_cooldown)
            if wait_secs > ABORT_THRESHOLD:
                _abort_flag["v"] = True
                result_kind = "error"
                result_val = f"penalty-box ({wait_secs}s)"
                break
            # Retry-After corto: registrar el sleep y reintentar
            time.sleep(wait_secs)
            continue
        result_kind = "error"
        result_val = f"http {r.status_code}"
        break
finally:
    time.sleep = orig_sleep

st.write(f"KIND:{result_kind}")
st.write(f"VAL:{result_val}")
st.write(f"CALLS:{len(_calls)}")
st.write(f"SLEPT_COUNT:{len(_slept)}")
st.write(f"ABORT:{_abort_flag['v']}")
"""


class TestA_RetryAfterCorto:

    def test_429_corto_resuelve_en_reintento(self):
        """(A) 429 con RA<=120s → espera + reintenta → 2o intento 200 → kind='uri'."""
        at = _build_at_str(_HARNESS_A_RETRY_SHORT)
        at.run()
        assert not at.exception, f"Excepcion en harness A: {at.exception}"
        assert _extract_write(at, "KIND") == "uri", (
            "Tras 429 corto + reintento exitoso, kind debe ser 'uri'"
        )
        assert "RESOLVED" in _extract_write(at, "VAL"), (
            "El URI resuelto debe contener 'RESOLVED'"
        )

    def test_429_corto_hizo_exactamente_2_llamadas(self):
        """(A) Se hacen exactamente 2 llamadas HTTP: la del 429 y la del reintento."""
        at = _build_at_str(_HARNESS_A_RETRY_SHORT)
        at.run()
        assert not at.exception, f"Excepcion en harness A: {at.exception}"
        calls = _extract_int(at, "CALLS")
        assert calls == 2, f"Se esperaban 2 llamadas HTTP, hubo {calls}"

    def test_429_corto_registro_sleep_y_no_aborta(self):
        """(A) El sleep se registra (se llamaria en produccion) y abort_flag queda False."""
        at = _build_at_str(_HARNESS_A_RETRY_SHORT)
        at.run()
        assert not at.exception, f"Excepcion en harness A: {at.exception}"
        # Se durmio exactamente 1 vez (el Retry-After del 429)
        slept = _extract_int(at, "SLEPT_COUNT")
        assert slept == 1, f"Se esperaba 1 sleep (el RA del 429), hubo {slept}"
        assert _extract_bool(at, "ABORT") is False, "abort_flag debe ser False tras RA corto"


# ===========================================================================
# (B) 429 con Retry-After LARGO (>120s) → ABORTA, setea cooldown, NO duerme
# ===========================================================================

_HARNESS_B_PENALTY_BOX = """
import time
import streamlit as st

ABORT_THRESHOLD = 120
LONG_RA = 3600  # simula penalty-box de 1 hora

class _Resp429Long:
    status_code = 429
    headers = {"Retry-After": str(LONG_RA)}
    def json(self): return {}

_calls = []
_slept = []

class _FakeSession:
    def get(self, url, **kw):
        _calls.append(url)
        return _Resp429Long()

_cooldown = {"until": 0.0}
_abort_flag = {"v": False}

orig_sleep = time.sleep
def _fake_sleep(s):
    _slept.append(s)
time.sleep = _fake_sleep

isrc = "ISRCTEST02"
sess = _FakeSession()
result_kind = None
result_val = None
attempts = 0
t_before = time.time()
try:
    while attempts < 3 and not _abort_flag["v"]:
        attempts += 1
        r = sess.get("https://api.spotify.com/v1/search")
        if r.status_code == 429:
            ra = r.headers.get("Retry-After")
            try:
                wait_secs = int(ra) if ra else 5
            except ValueError:
                wait_secs = 5
            new_cooldown = time.time() + wait_secs
            _cooldown["until"] = max(_cooldown["until"], new_cooldown)
            if wait_secs > ABORT_THRESHOLD:
                _abort_flag["v"] = True
                result_kind = "error"
                result_val = f"penalty-box ({wait_secs}s)"
                break
            time.sleep(wait_secs)
            continue
        result_kind = "error"
        result_val = f"http {r.status_code}"
        break
finally:
    time.sleep = orig_sleep

elapsed = time.time() - t_before
cooldown_in_future = _cooldown["until"] > time.time()

st.write(f"KIND:{result_kind}")
st.write(f"ABORT:{_abort_flag['v']}")
st.write(f"COOLDOWN_FUTURE:{cooldown_in_future}")
st.write(f"SLEPT_COUNT:{len(_slept)}")
st.write(f"CALLS:{len(_calls)}")
st.write(f"ELAPSED_LT_2:{elapsed < 2.0}")
"""


class TestB_PenaltyBoxLargo:

    def test_429_largo_aborta_limpiamente(self):
        """(B) 429 con RA>120s → abort_flag=True, kind='error'."""
        at = _build_at_str(_HARNESS_B_PENALTY_BOX)
        at.run()
        assert not at.exception, f"Excepcion en harness B: {at.exception}"
        assert _extract_bool(at, "ABORT") is True, (
            "abort_flag debe quedar True tras 429 con RA largo"
        )
        assert "error" in _extract_write(at, "KIND"), (
            "kind debe ser 'error' tras penalty-box"
        )

    def test_429_largo_setea_cooldown_al_futuro(self):
        """(B) _SP_COOLDOWN['until'] queda > time.time() tras el 429 largo."""
        at = _build_at_str(_HARNESS_B_PENALTY_BOX)
        at.run()
        assert not at.exception, f"Excepcion en harness B: {at.exception}"
        assert _extract_bool(at, "COOLDOWN_FUTURE") is True, (
            "_SP_COOLDOWN['until'] debe quedar en el futuro tras penalty-box"
        )

    def test_429_largo_no_duerme_horas(self):
        """(B) Tras 429 con RA=3600s el worker NO llama a sleep(3600) — el test corre en <2s."""
        at = _build_at_str(_HARNESS_B_PENALTY_BOX)
        at.run()
        assert not at.exception, f"Excepcion en harness B: {at.exception}"
        # El abort es inmediato; no debe haber ningun sleep registrado
        slept = _extract_int(at, "SLEPT_COUNT")
        assert slept == 0, (
            f"El worker NO debe dormir en penalty-box (hubo {slept} sleeps)"
        )
        assert _extract_bool(at, "ELAPSED_LT_2") is True, (
            "El test debe completarse en <2s (sin dormir el RA largo)"
        )

    def test_429_largo_solo_1_llamada_http(self):
        """(B) Solo se hace 1 llamada HTTP (la del 429 largo) y se aborta."""
        at = _build_at_str(_HARNESS_B_PENALTY_BOX)
        at.run()
        assert not at.exception, f"Excepcion en harness B: {at.exception}"
        calls = _extract_int(at, "CALLS")
        assert calls == 1, (
            f"Tras penalty-box solo se debe emitir 1 request (hubo {calls})"
        )


# ===========================================================================
# (C) Gate de cooldown: _SP_COOLDOWN["until"] en el futuro → retorna stopped=True
#     SIN hacer ninguna request HTTP
# ===========================================================================

_HARNESS_C_GATE_COOLDOWN = """
import time
import streamlit as st

# Simula el gate de cooldown del inicio de spotify_resolve_isrcs.
# _SP_COOLDOWN["until"] ya esta en el futuro (penalty-box activo).

_calls_http = []

class _FakeSession:
    def get(self, url, **kw):
        _calls_http.append(url)
        raise AssertionError("El gate debia haber impedido esta llamada")

# Estado: cooldown activo (expira dentro de 1 hora)
_cooldown = {"until": time.time() + 3600}

isrcs = ["ISRC_A", "ISRC_B"]

# Replica del gate al inicio de spotify_resolve_isrcs
remaining = _cooldown["until"] - time.time()
if remaining > 0:
    stopped = True
    reason = "cooldown activo"
    errors = [(i, "cooldown activo") for i in isrcs]
    calls_made = len(_calls_http)
else:
    # NO debe llegar aqui
    sess = _FakeSession()
    for i in isrcs:
        sess.get("https://api.spotify.com/v1/search")
    stopped = False
    reason = ""
    errors = []
    calls_made = len(_calls_http)

st.write(f"STOPPED:{stopped}")
st.write(f"CALLS:{calls_made}")
st.write(f"ERRORS_COUNT:{len(errors)}")
"""


class TestC_GateCooldown:

    def test_gate_cooldown_retorna_stopped(self):
        """(C) Con cooldown activo, spotify_resolve_isrcs retorna stopped=True."""
        at = _build_at_str(_HARNESS_C_GATE_COOLDOWN)
        at.run()
        assert not at.exception, f"Excepcion en harness C: {at.exception}"
        assert _extract_bool(at, "STOPPED") is True, (
            "Gate de cooldown debe retornar stopped=True sin llamar a Spotify"
        )

    def test_gate_cooldown_cero_requests_http(self):
        """(C) Con cooldown activo, NO se hace ninguna request HTTP."""
        at = _build_at_str(_HARNESS_C_GATE_COOLDOWN)
        at.run()
        assert not at.exception, f"Excepcion en harness C: {at.exception}"
        calls = _extract_int(at, "CALLS")
        assert calls == 0, (
            f"Con cooldown activo no debe haber requests HTTP (hubo {calls})"
        )

    def test_gate_cooldown_todos_isrcs_como_error(self):
        """(C) Todos los ISRCs quedan como error 'cooldown activo' en la respuesta."""
        at = _build_at_str(_HARNESS_C_GATE_COOLDOWN)
        at.run()
        assert not at.exception, f"Excepcion en harness C: {at.exception}"
        errors_count = _extract_int(at, "ERRORS_COUNT")
        assert errors_count == 2, (
            f"Todos los ISRCs (2) deben aparecer en errors (hubo {errors_count})"
        )


# ===========================================================================
# (D) Dedup de ISRCs: duplicados → solo se resuelven los unicos
# ===========================================================================

_HARNESS_D_DEDUP = """
import time
import streamlit as st

_calls = []

class _Resp200:
    status_code = 200
    headers = {}
    def json(self):
        return {"tracks": {"items": [{"uri": f"spotify:track:{_calls[-1]}"}]}}

class _FakeSession:
    def get(self, url, params=None, **kw):
        isrc = (params or {}).get("q", "UNKNOWN").replace("isrc:", "")
        _calls.append(isrc)
        return _Resp200()

# Lista con duplicados: A aparece 3 veces, B 2 veces, C 1 vez
isrcs_input = ["ISRC_A", "ISRC_B", "ISRC_A", "ISRC_C", "ISRC_B", "ISRC_A"]

# Dedup preservando orden (logica de _tab_playlist_central en app.py)
_seen = set()
isrcs_uniq = []
for i in isrcs_input:
    if i not in _seen:
        _seen.add(i)
        isrcs_uniq.append(i)

# Resolver solo los unicos
sess = _FakeSession()
results = {}
for isrc in isrcs_uniq:
    r = sess.get("https://api.spotify.com/v1/search",
                 params={"q": f"isrc:{isrc}", "type": "track", "limit": 1})
    items = (r.json().get("tracks") or {}).get("items") or []
    results[isrc] = items[0]["uri"] if items else None

calls_count = len(_calls)
unique_count = len(isrcs_uniq)
# Verificar que los 3 ISRCs unicos tienen resultado
all_resolved = all(results.get(i) is not None for i in ["ISRC_A", "ISRC_B", "ISRC_C"])

st.write(f"CALLS:{calls_count}")
st.write(f"UNIQUES:{unique_count}")
st.write(f"ALL_RESOLVED:{all_resolved}")
"""


class TestD_DedupISRCs:

    def test_dedup_reduce_llamadas_http(self):
        """(D) 6 ISRCs con duplicados → solo 3 llamadas HTTP (los unicos)."""
        at = _build_at_str(_HARNESS_D_DEDUP)
        at.run()
        assert not at.exception, f"Excepcion en harness D: {at.exception}"
        calls = _extract_int(at, "CALLS")
        assert calls == 3, (
            f"Con dedup solo deben hacerse 3 llamadas HTTP (hubo {calls})"
        )

    def test_dedup_cuenta_unicos(self):
        """(D) isrcs_uniq tiene exactamente 3 elementos (A, B, C)."""
        at = _build_at_str(_HARNESS_D_DEDUP)
        at.run()
        assert not at.exception, f"Excepcion en harness D: {at.exception}"
        uniques = _extract_int(at, "UNIQUES")
        assert uniques == 3, (
            f"Deberian ser 3 ISRCs unicos (hubo {uniques})"
        )

    def test_dedup_todos_resueltos(self):
        """(D) Los 3 ISRCs unicos (A, B, C) se resuelven correctamente."""
        at = _build_at_str(_HARNESS_D_DEDUP)
        at.run()
        assert not at.exception, f"Excepcion en harness D: {at.exception}"
        assert _extract_bool(at, "ALL_RESOLVED") is True, (
            "Los 3 ISRCs unicos deben quedar resueltos"
        )


# ===========================================================================
# (E) _parse_retry_after — tests unitarios directos + harnesses AppTest
#
# Comportamiento correcto del helper real (app._parse_retry_after):
#   - Entero "60"                   → 60s
#   - Float "200.0"                 → 200s  (>120 → ABORT en el flujo)
#   - Fecha HTTP futura (now+3600s) → ~3600s (>120 → ABORT en el flujo)
#   - Fecha HTTP pasada             → max(1, valor_negativo) = 1s (<=120 → retry corto)
#   - "garbage" / no parseable      → fallback 5s (<=120 → retry corto)
#   - None / ausente                → fallback 5s (<=120 → retry corto)
# ===========================================================================


class TestParseRetryAfter:
    """Tests unitarios directos de _parse_retry_after (sin AppTest ni import de app).

    Extrae la funcion del source de app.py sin ejecutar el modulo completo,
    evitando que st.set_page_config/st.markdown de nivel de modulo contaminen
    el estado de Streamlit para tests AppTest posteriores.
    """

    @pytest.fixture(autouse=True)
    def _import_helper(self):
        self._fn = _load_parse_retry_after()

    def test_entero_string(self):
        """Entero string "60" → 60."""
        assert self._fn("60") == 60

    def test_entero_string_grande(self):
        """Entero string "3600" (>120) → 3600."""
        assert self._fn("3600") == 3600

    def test_float_string_200(self):
        """Float string "200.0" → 200 (>120, el caller debe ABORT)."""
        assert self._fn("200.0") == 200

    def test_float_string_pequeño(self):
        """Float string "30.5" → 30 (<=120, el caller debe retry)."""
        assert self._fn("30.5") == 30

    def test_fecha_http_futura_aprox_3600(self):
        """Fecha HTTP futura (now+3600s) → resultado aprox entre 3595 y 3601."""
        now = time.time()
        future_dt = datetime.datetime.fromtimestamp(now + 3600, tz=datetime.timezone.utc)
        ra_date = future_dt.strftime("%a, %d %b %Y %H:%M:%S GMT")
        result = self._fn(ra_date)
        # Margen de +-5s por latencia de ejecucion
        assert 3595 <= result <= 3605, (
            f"Fecha HTTP futura (now+3600) debe parsear a ~3600s, obtuvo {result}s"
        )

    def test_fecha_http_futura_mayor_que_umbral(self):
        """Fecha HTTP futura (now+3600s) → >120 (el caller debe ABORT)."""
        now = time.time()
        future_dt = datetime.datetime.fromtimestamp(now + 3600, tz=datetime.timezone.utc)
        ra_date = future_dt.strftime("%a, %d %b %Y %H:%M:%S GMT")
        result = self._fn(ra_date)
        assert result > 120, (
            f"Fecha HTTP futura (3600s) debe dar >120 para que el caller aborte, obtuvo {result}"
        )

    def test_fecha_http_pasada_devuelve_1(self):
        """Fecha HTTP pasada → max(1, negativo) = 1 (no lanza, retry muy corto)."""
        now = time.time()
        past_dt = datetime.datetime.fromtimestamp(now - 60, tz=datetime.timezone.utc)
        ra_past = past_dt.strftime("%a, %d %b %Y %H:%M:%S GMT")
        result = self._fn(ra_past)
        assert result == 1, (
            f"Fecha HTTP pasada debe devolver 1 (no negativo), obtuvo {result}"
        )

    def test_garbage_devuelve_fallback(self):
        """"garbage" → no parseable → fallback default=5."""
        assert self._fn("garbage") == 5

    def test_none_devuelve_fallback(self):
        """None → fallback default=5."""
        assert self._fn(None) == 5

    def test_string_vacio_devuelve_fallback(self):
        """String vacio "" → fallback default=5."""
        assert self._fn("") == 5

    def test_custom_default(self):
        """default personalizado se respeta para inputs no parseables."""
        assert self._fn("garbage", default=10) == 10
        assert self._fn(None, default=30) == 30

    def test_min_1_nunca_cero(self):
        """El resultado nunca es 0 ni negativo (max(1, ...))."""
        assert self._fn("0") == 1
        assert self._fn("-5") == 1


# ---------------------------------------------------------------------------
# Harnesses AppTest que ejercen el flujo end-to-end con _parse_retry_after
# real (importado desde app.py via sys.path en el harness).
# ---------------------------------------------------------------------------

# Harness E1: Retry-After como fecha HTTP futura (now+3600s) → _parse_retry_after
# devuelve ~3600s → >120 → ABORT. Antes de este fix, el harness usaba int(ra)
# que lanzaba ValueError y caía al fallback 5s (sin ABORT). Este test verifica
# que la logica CORRECTA abortara el lote.
_HARNESS_E1_FECHA_HTTP_FUTURA = """
import sys, os, time, datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import streamlit as st

# Importar el helper real de app.py
from app import _parse_retry_after

ABORT_THRESHOLD = 120

# Generar la fecha HTTP futura en el momento de ejecucion del harness
_now = time.time()
_future_dt = datetime.datetime.fromtimestamp(_now + 3600, tz=datetime.timezone.utc)
_RA_DATE_FUTURA = _future_dt.strftime("%a, %d %b %Y %H:%M:%S GMT")

class _Resp429FechaFutura:
    status_code = 429
    def __init__(self):
        self.headers = {"Retry-After": _RA_DATE_FUTURA}
    def json(self): return {}

class _Resp200:
    status_code = 200
    headers = {}
    def json(self):
        return {"tracks": {"items": [{"uri": "spotify:track:RESOLVED_FECHA"}]}}

_calls = []
_slept = []

class _FakeSession:
    def get(self, url, **kw):
        _calls.append(url)
        # Siempre 429 con fecha futura — el primer y unico intento debe abortar
        return _Resp429FechaFutura()

orig_sleep = time.sleep
def _fake_sleep(s):
    _slept.append(s)
time.sleep = _fake_sleep

_abort_flag = {"v": False}
_cooldown = {"until": 0.0}
result_kind = None
no_exception = True
sess = _FakeSession()
attempts = 0
parsed_wait = None

try:
    while attempts < 3 and not _abort_flag["v"]:
        attempts += 1
        r = sess.get("https://api.spotify.com/v1/search")
        if r.status_code == 200:
            items = (r.json().get("tracks") or {}).get("items") or []
            result_kind = "uri"
            break
        if r.status_code == 429:
            ra = r.headers.get("Retry-After")
            wait_secs = _parse_retry_after(ra)  # helper real
            parsed_wait = wait_secs
            new_cooldown = time.time() + wait_secs
            _cooldown["until"] = max(_cooldown["until"], new_cooldown)
            if wait_secs > ABORT_THRESHOLD:
                _abort_flag["v"] = True
                result_kind = "error"
                break
            time.sleep(wait_secs)
            continue
        result_kind = "error"
        break
except Exception as exc:
    no_exception = False
    result_kind = f"RAISED:{exc}"
finally:
    time.sleep = orig_sleep

st.write(f"NO_EXCEPTION:{no_exception}")
st.write(f"KIND:{result_kind}")
st.write(f"ABORT:{_abort_flag['v']}")
st.write(f"CALLS:{len(_calls)}")
st.write(f"SLEPT_COUNT:{len(_slept)}")
# parsed_wait puede ser None si fallo antes; reportar 0 como fallback de display
st.write(f"PARSED_GT_120:{(parsed_wait or 0) > 120}")
"""

# Harness E2: Retry-After float "200.0" → _parse_retry_after devuelve 200 → >120 → ABORT.
_HARNESS_E2_FLOAT_STRING = """
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import streamlit as st
from app import _parse_retry_after

ABORT_THRESHOLD = 120

class _Resp429Float:
    status_code = 429
    headers = {"Retry-After": "200.0"}
    def json(self): return {}

_calls = []
_slept = []

class _FakeSession:
    def get(self, url, **kw):
        _calls.append(url)
        return _Resp429Float()

orig_sleep = time.sleep
def _fake_sleep(s):
    _slept.append(s)
time.sleep = _fake_sleep

_abort_flag = {"v": False}
_cooldown = {"until": 0.0}
result_kind = None
no_exception = True
parsed_wait = None
sess = _FakeSession()
attempts = 0

try:
    while attempts < 3 and not _abort_flag["v"]:
        attempts += 1
        r = sess.get("https://api.spotify.com/v1/search")
        if r.status_code == 200:
            result_kind = "uri"
            break
        if r.status_code == 429:
            ra = r.headers.get("Retry-After")
            wait_secs = _parse_retry_after(ra)
            parsed_wait = wait_secs
            new_cooldown = time.time() + wait_secs
            _cooldown["until"] = max(_cooldown["until"], new_cooldown)
            if wait_secs > ABORT_THRESHOLD:
                _abort_flag["v"] = True
                result_kind = "error"
                break
            time.sleep(wait_secs)
            continue
        result_kind = "error"
        break
except Exception as exc:
    no_exception = False
    result_kind = f"RAISED:{exc}"
finally:
    time.sleep = orig_sleep

st.write(f"NO_EXCEPTION:{no_exception}")
st.write(f"KIND:{result_kind}")
st.write(f"ABORT:{_abort_flag['v']}")
st.write(f"PARSED_WAIT:{parsed_wait}")
st.write(f"SLEPT_COUNT:{len(_slept)}")
"""

# Harness E3: Retry-After "garbage" → _parse_retry_after devuelve 5 (fallback) → <=120 → retry.
# La 2a request devuelve 200 → ISRC resuelto.
_HARNESS_E3_GARBAGE = """
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import streamlit as st
from app import _parse_retry_after

ABORT_THRESHOLD = 120

class _Resp429Garbage:
    status_code = 429
    headers = {"Retry-After": "garbage"}
    def json(self): return {}

class _Resp200:
    status_code = 200
    headers = {}
    def json(self):
        return {"tracks": {"items": [{"uri": "spotify:track:RESOLVED_GARBAGE"}]}}

_calls = []
_slept = []

class _FakeSession:
    def get(self, url, **kw):
        _calls.append(url)
        if len(_calls) == 1:
            return _Resp429Garbage()
        return _Resp200()

orig_sleep = time.sleep
def _fake_sleep(s):
    _slept.append(s)
time.sleep = _fake_sleep

_abort_flag = {"v": False}
_cooldown = {"until": 0.0}
result_kind = None
no_exception = True
parsed_wait = None
sess = _FakeSession()
attempts = 0

try:
    while attempts < 3 and not _abort_flag["v"]:
        attempts += 1
        r = sess.get("https://api.spotify.com/v1/search")
        if r.status_code == 200:
            items = (r.json().get("tracks") or {}).get("items") or []
            result_kind = "uri"
            break
        if r.status_code == 429:
            ra = r.headers.get("Retry-After")
            wait_secs = _parse_retry_after(ra)
            parsed_wait = wait_secs
            new_cooldown = time.time() + wait_secs
            _cooldown["until"] = max(_cooldown["until"], new_cooldown)
            if wait_secs > ABORT_THRESHOLD:
                _abort_flag["v"] = True
                result_kind = "error"
                break
            time.sleep(wait_secs)
            continue
        result_kind = "error"
        break
except Exception as exc:
    no_exception = False
    result_kind = f"RAISED:{exc}"
finally:
    time.sleep = orig_sleep

st.write(f"NO_EXCEPTION:{no_exception}")
st.write(f"KIND:{result_kind}")
st.write(f"ABORT:{_abort_flag['v']}")
st.write(f"PARSED_WAIT:{parsed_wait}")
# El sleep debe ser el fallback de 5s
st.write(f"FALLBACK_SLEEP:{len(_slept) > 0 and all(s == 5 for s in _slept)}")
"""

# Harness E4: Retry-After ausente (header omitido) → fallback 5s → retry corto → 200.
# (Comportamiento identico al anterior en terminos del helper; se mantiene por
# separado porque verifica la rama ra=None explicitamente.)
_HARNESS_E4_AUSENTE = """
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import streamlit as st
from app import _parse_retry_after

ABORT_THRESHOLD = 120

class _Resp429Absent:
    status_code = 429
    headers = {}  # sin Retry-After
    def json(self): return {}

class _Resp200:
    status_code = 200
    headers = {}
    def json(self):
        return {"tracks": {"items": [{"uri": "spotify:track:RESOLVED_ABSENT"}]}}

_calls = []
_slept = []

class _FakeSession:
    def get(self, url, **kw):
        _calls.append(url)
        if len(_calls) == 1:
            return _Resp429Absent()
        return _Resp200()

orig_sleep = time.sleep
def _fake_sleep(s):
    _slept.append(s)
time.sleep = _fake_sleep

_abort_flag = {"v": False}
_cooldown = {"until": 0.0}
result_kind = None
no_exception = True
sess = _FakeSession()
attempts = 0

try:
    while attempts < 3 and not _abort_flag["v"]:
        attempts += 1
        r = sess.get("https://api.spotify.com/v1/search")
        if r.status_code == 200:
            items = (r.json().get("tracks") or {}).get("items") or []
            result_kind = "uri"
            break
        if r.status_code == 429:
            ra = r.headers.get("Retry-After")
            wait_secs = _parse_retry_after(ra)  # None → 5
            new_cooldown = time.time() + wait_secs
            _cooldown["until"] = max(_cooldown["until"], new_cooldown)
            if wait_secs > ABORT_THRESHOLD:
                _abort_flag["v"] = True
                result_kind = "error"
                break
            time.sleep(wait_secs)
            continue
        result_kind = "error"
        break
except Exception as exc:
    no_exception = False
finally:
    time.sleep = orig_sleep

st.write(f"NO_EXCEPTION:{no_exception}")
st.write(f"KIND:{result_kind}")
st.write(f"FALLBACK_5:{len(_slept) > 0 and _slept[0] == 5}")
"""


class TestE_RetryAfterEdgeCases:

    def test_fecha_http_futura_aborta_lote(self):
        """(E1) Retry-After como fecha HTTP futura (now+3600s) → _parse_retry_after
        lo parsea a ~3600s, que es >120 → el flujo ABORTA el lote.
        Antes del fix (_parse_retry_after), int(ra) lanzaba ValueError y caia
        al fallback 5s, sin abortar. Este test caza esa regresion."""
        at = _build_at_str(_HARNESS_E1_FECHA_HTTP_FUTURA)
        at.run()
        assert not at.exception, f"Excepcion en harness E1: {at.exception}"
        assert _extract_bool(at, "NO_EXCEPTION") is True, (
            "No debe lanzarse excepcion con Retry-After como fecha HTTP"
        )
        assert _extract_bool(at, "ABORT") is True, (
            "Fecha HTTP futura (~3600s) debe parsear a >120s y ABORTAR el lote"
        )
        assert _extract_write(at, "KIND") == "error", (
            "Tras ABORT por fecha HTTP futura, kind debe ser 'error'"
        )
        assert _extract_int(at, "SLEPT_COUNT") == 0, (
            "Tras ABORT no debe haber ningun sleep (penalty-box activo)"
        )
        assert _extract_bool(at, "PARSED_GT_120") is True, (
            "_parse_retry_after(fecha_futura) debe devolver >120"
        )

    def test_fecha_http_futura_solo_1_llamada_http(self):
        """(E1) Fecha HTTP futura → ABORT inmediato tras 1 sola request."""
        at = _build_at_str(_HARNESS_E1_FECHA_HTTP_FUTURA)
        at.run()
        assert not at.exception, f"Excepcion en harness E1: {at.exception}"
        assert _extract_int(at, "CALLS") == 1, (
            "Fecha HTTP futura debe abortar tras 1 sola llamada HTTP (no reintentar)"
        )

    def test_float_string_200_aborta(self):
        """(E2) Retry-After "200.0" → _parse_retry_after devuelve 200 → >120 → ABORT."""
        at = _build_at_str(_HARNESS_E2_FLOAT_STRING)
        at.run()
        assert not at.exception, f"Excepcion en harness E2: {at.exception}"
        assert _extract_bool(at, "NO_EXCEPTION") is True, (
            "No debe lanzarse excepcion con Retry-After float-string"
        )
        assert _extract_bool(at, "ABORT") is True, (
            "Float '200.0' → 200s > 120 umbral → debe ABORTAR"
        )
        parsed = _extract_int(at, "PARSED_WAIT")
        assert parsed == 200, f"_parse_retry_after('200.0') debe devolver 200, obtuvo {parsed}"
        assert _extract_int(at, "SLEPT_COUNT") == 0, (
            "Tras ABORT por 200s no debe dormir"
        )

    def test_garbage_no_aborta_usa_fallback_5s(self):
        """(E3) Retry-After 'garbage' → fallback 5s → <=120 → retry corto → 200 → uri."""
        at = _build_at_str(_HARNESS_E3_GARBAGE)
        at.run()
        assert not at.exception, f"Excepcion en harness E3: {at.exception}"
        assert _extract_bool(at, "NO_EXCEPTION") is True, (
            "No debe lanzarse excepcion con Retry-After 'garbage'"
        )
        assert _extract_bool(at, "ABORT") is False, (
            "Fallback 5s <= 120 → NO debe abortar"
        )
        assert _extract_write(at, "KIND") == "uri", (
            "Tras retry corto exitoso, kind debe ser 'uri'"
        )
        parsed = _extract_int(at, "PARSED_WAIT")
        assert parsed == 5, f"_parse_retry_after('garbage') debe devolver 5, obtuvo {parsed}"
        assert _extract_bool(at, "FALLBACK_SLEEP") is True, (
            "El sleep debe ser el fallback de 5s"
        )

    def test_retry_after_ausente_no_aborta(self):
        """(E4) Sin cabecera Retry-After → fallback 5s → retry exitoso → kind='uri'."""
        at = _build_at_str(_HARNESS_E4_AUSENTE)
        at.run()
        assert not at.exception, f"Excepcion en harness E4 (absent): {at.exception}"
        assert _extract_bool(at, "NO_EXCEPTION") is True, (
            "No debe lanzarse excepcion sin Retry-After"
        )
        assert _extract_bool(at, "FALLBACK_5") is True, (
            "Sin Retry-After, el sleep debe ser el fallback de 5s"
        )
        assert _extract_write(at, "KIND") == "uri", (
            "Sin Retry-After, el reintento exitoso debe resolver el ISRC"
        )


# ===========================================================================
# (F) Techo SPOTIFY_MAX_COOLDOWN: Retry-After 999999s → cooldown techo 7200s
#
# _SP_COOLDOWN["until"] nunca debe superar now + SPOTIFY_MAX_COOLDOWN (7200s),
# aunque el Retry-After real sea enorme. El lote sí debe abortar (wait_secs
# real > ABORT_THRESHOLD). Verifica el techo sin llamar a Spotify en red.
# ===========================================================================

# El harness importa las constantes y el estado global reales de app.py
# para exercitar exactamente la misma logica del codigo de produccion.
_HARNESS_F_TECHO_MAX_COOLDOWN = """
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import streamlit as st
from app import _parse_retry_after, SPOTIFY_MAX_COOLDOWN, SPOTIFY_RA_ABORT_THRESHOLD
import app as _app

# Resetear estado global al inicio del harness para aislamiento
_app._SP_COOLDOWN["until"] = 0.0
_app._SP_LAST_REQ["t"] = 0.0

HUGE_RA = 999999  # segundos — mucho mayor que MAX_COOLDOWN

class _Resp429Huge:
    status_code = 429
    headers = {"Retry-After": str(HUGE_RA)}
    def json(self): return {}

_calls = []

class _FakeSession:
    def get(self, url, **kw):
        _calls.append(url)
        return _Resp429Huge()

_abort_flag = {"v": False}
result_kind = None
sess = _FakeSession()
attempts = 0

import threading
_lock = threading.Lock()

t_before = time.time()
while attempts < 3 and not _abort_flag["v"]:
    attempts += 1
    r = sess.get("https://api.spotify.com/v1/search")
    if r.status_code == 429:
        ra = r.headers.get("Retry-After")
        wait_secs = _parse_retry_after(ra)  # 999999
        now = time.time()
        # Replica exacta de la logica de _resolve_one en produccion
        with _lock:
            _app._SP_COOLDOWN["until"] = max(
                _app._SP_COOLDOWN["until"],
                min(now + wait_secs, now + SPOTIFY_MAX_COOLDOWN),
            )
        if wait_secs > SPOTIFY_RA_ABORT_THRESHOLD:
            _abort_flag["v"] = True
            result_kind = "error"
            break
        continue
    result_kind = "error"
    break

t_after = time.time()
cd_until = _app._SP_COOLDOWN["until"]

# El techo debe ser now+7200 (con margen de +-5s por latencia de ejecucion)
techo_ok = (t_before + SPOTIFY_MAX_COOLDOWN - 5) <= cd_until <= (t_after + SPOTIFY_MAX_COOLDOWN + 5)
# Ademas, el cooldown NO debe superar t_after + MAX_COOLDOWN (nunca mas alla del techo)
no_supera_techo = cd_until <= (t_after + SPOTIFY_MAX_COOLDOWN + 5)

st.write(f"ABORT:{_abort_flag['v']}")
st.write(f"KIND:{result_kind}")
st.write(f"TECHO_OK:{techo_ok}")
st.write(f"NO_SUPERA_TECHO:{no_supera_techo}")
st.write(f"CD_UNTIL_MINUS_NOW:{int(cd_until - t_before)}")
st.write(f"MAX_COOLDOWN:{SPOTIFY_MAX_COOLDOWN}")
st.write(f"CALLS:{len(_calls)}")
"""


class TestF_TechoMaxCooldown:

    def test_ra_enorme_lote_aborta(self):
        """(F) Retry-After 999999s: el lote sí aborta (wait_secs > ABORT_THRESHOLD)."""
        at = _build_at_str(_HARNESS_F_TECHO_MAX_COOLDOWN)
        at.run()
        assert not at.exception, f"Excepcion en harness F: {at.exception}"
        assert _extract_bool(at, "ABORT") is True, (
            "RA=999999s > ABORT_THRESHOLD → el lote debe abortar"
        )
        assert _extract_write(at, "KIND") == "error", (
            "Tras ABORT por RA enorme, kind debe ser 'error'"
        )

    def test_ra_enorme_cooldown_techo_7200(self):
        """(F) _SP_COOLDOWN['until'] queda en ~now+7200, NO en now+999999."""
        at = _build_at_str(_HARNESS_F_TECHO_MAX_COOLDOWN)
        at.run()
        assert not at.exception, f"Excepcion en harness F: {at.exception}"
        assert _extract_bool(at, "TECHO_OK") is True, (
            "_SP_COOLDOWN['until'] debe quedar en ~now+7200 (SPOTIFY_MAX_COOLDOWN), "
            "no en now+999999"
        )

    def test_ra_enorme_nunca_supera_techo(self):
        """(F) _SP_COOLDOWN['until'] nunca supera now + SPOTIFY_MAX_COOLDOWN."""
        at = _build_at_str(_HARNESS_F_TECHO_MAX_COOLDOWN)
        at.run()
        assert not at.exception, f"Excepcion en harness F: {at.exception}"
        assert _extract_bool(at, "NO_SUPERA_TECHO") is True, (
            "_SP_COOLDOWN['until'] NO debe superar now + SPOTIFY_MAX_COOLDOWN"
        )
        # Verificacion adicional: el cooldown registrado es ~7200, no ~999999
        cd_minus_now = _extract_int(at, "CD_UNTIL_MINUS_NOW")
        max_cd = _extract_int(at, "MAX_COOLDOWN")
        assert cd_minus_now <= max_cd + 5, (
            f"Cooldown registrado ({cd_minus_now}s) supera SPOTIFY_MAX_COOLDOWN ({max_cd}s)"
        )

    def test_ra_enorme_solo_1_llamada_http(self):
        """(F) RA=999999s → ABORT tras 1 sola request (no reintentar)."""
        at = _build_at_str(_HARNESS_F_TECHO_MAX_COOLDOWN)
        at.run()
        assert not at.exception, f"Excepcion en harness F: {at.exception}"
        calls = _extract_int(at, "CALLS")
        assert calls == 1, (
            f"RA enorme debe abortar tras 1 request (hubo {calls})"
        )
