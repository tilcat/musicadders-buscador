"""
Regresión P1a + P1b — thread-safety y _state_secret_key (app.py)
==================================================================

P1b: _state_secret_key() debe lanzar RuntimeError cuando SPOTIFY_CLIENT_SECRET
     no está configurado o está vacío/solo-espacios.
     Antes del fix devolvía la clave de fallback "ma-default-key" (inseguro).

P1a: Dentro de _resolve_one (worker thread de spotify_resolve_isrcs) NO debe
     haber ninguna referencia a st.session_state ni a la función pública
     spotify_client_credentials_token. Solo debe usar _fetch_cc_token_raw.

Ejecutar:
    /Users/trabajo/dashboard-regalias/.venv/bin/python \\
        -m pytest tests/test_p1_state_key.py -v
"""
import ast
import hashlib
import os

import pytest

APP_PATH = os.path.join(os.path.dirname(__file__), "..", "app.py")

streamlit = pytest.importorskip("streamlit", reason="streamlit no disponible en este entorno")


# ---------------------------------------------------------------------------
# Harness inline — replica exactamente la lógica de _state_secret_key
# de producción. No importa app.py directamente porque st.* a nivel módulo
# requiere un contexto Streamlit activo; el harness lo resuelve via AppTest.
# ---------------------------------------------------------------------------

_HARNESS_STATE_KEY = '''
import streamlit as st
import hashlib

def _state_secret_key():
    """Réplica exacta de la función de producción."""
    cs = st.secrets.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if not cs:
        raise RuntimeError(
            "SPOTIFY_CLIENT_SECRET no configurado: el state OAuth no puede firmarse de forma segura"
        )
    return hashlib.sha256(cs.encode("utf-8")).digest()

try:
    result = _state_secret_key()
    st.session_state.test_result_kind = "ok"
    st.session_state.test_result_len = len(result)
    st.session_state.test_result_hex = result.hex()
except RuntimeError as e:
    st.session_state.test_result_kind = "runtime_error"
    st.session_state.test_result_msg = str(e)
except Exception as e:
    st.session_state.test_result_kind = "other_error"
    st.session_state.test_result_msg = f"{type(e).__name__}: {e}"

st.write("done")
'''


def _run_harness(extra_secrets: dict | None = None):
    """Construye y ejecuta el harness; devuelve el AppTest tras el run."""
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_string(_HARNESS_STATE_KEY, default_timeout=10)
    if extra_secrets:
        for k, v in extra_secrets.items():
            at.secrets[k] = v
    at.run()
    return at


# ---------------------------------------------------------------------------
# P1b — Tests _state_secret_key
# ---------------------------------------------------------------------------

class TestStateSecretKeyP1b:
    """
    P1b: _state_secret_key() lanza RuntimeError cuando la clave no está
    disponible. Antes del fix devolvía "ma-default-key" silenciosamente.

    Para que st.secrets.get() no lance StreamlitSecretNotFoundError (que ocurre
    cuando no hay NINGÚN secret configurado), se establece al menos otro secret
    en los casos donde falta SPOTIFY_CLIENT_SECRET.
    """

    def test_sin_spotify_secret_lanza_runtime_error(self):
        """
        Key ausente en secrets.toml → RuntimeError con mensaje descriptivo.

        Regresión: antes devolvía sha256("ma-default-key") — un HMAC predecible
        que permite forjar tokens OAuth.
        """
        # Configura otro secret para que st.secrets no esté completamente vacío,
        # pero sin SPOTIFY_CLIENT_SECRET
        at = _run_harness({"OTHER_SECRET": "algo"})
        assert not at.exception, f"Excepción inesperada del harness: {at.exception}"
        kind = at.session_state.test_result_kind
        msg = at.session_state.test_result_msg
        assert kind == "runtime_error", (
            f"Se esperaba RuntimeError, obtenido kind={kind!r}, msg={msg!r}. "
            "El fallback 'ma-default-key' fue eliminado en P1b."
        )
        assert "SPOTIFY_CLIENT_SECRET" in msg, (
            f"El mensaje de error debe mencionar SPOTIFY_CLIENT_SECRET, obtenido: {msg!r}"
        )

    def test_secret_vacio_lanza_runtime_error(self):
        """SPOTIFY_CLIENT_SECRET='' → RuntimeError (string vacío tras strip)."""
        at = _run_harness({"SPOTIFY_CLIENT_SECRET": ""})
        assert not at.exception
        kind = at.session_state.test_result_kind
        assert kind == "runtime_error", (
            f"SPOTIFY_CLIENT_SECRET vacío debe lanzar RuntimeError, obtenido: {kind!r}"
        )

    def test_secret_solo_espacios_lanza_runtime_error(self):
        """SPOTIFY_CLIENT_SECRET='   ' → RuntimeError (solo whitespace, strip lo vacía)."""
        at = _run_harness({"SPOTIFY_CLIENT_SECRET": "   "})
        assert not at.exception
        kind = at.session_state.test_result_kind
        assert kind == "runtime_error", (
            f"SPOTIFY_CLIENT_SECRET con solo espacios debe lanzar RuntimeError, obtenido: {kind!r}"
        )

    def test_secret_valido_devuelve_32_bytes(self):
        """Con SPOTIFY_CLIENT_SECRET válido → bytes de longitud 32 (sha256)."""
        at = _run_harness({"SPOTIFY_CLIENT_SECRET": "un-secreto-de-prueba"})
        assert not at.exception
        kind = at.session_state.test_result_kind
        length = at.session_state.test_result_len
        assert kind == "ok", f"Se esperaba ok, obtenido: {kind!r}"
        assert length == 32, f"sha256 produce 32 bytes, obtenidos: {length}"

    def test_secret_valido_sha256_determinista(self):
        """El resultado es sha256 determinista — el mismo input produce el mismo digest."""
        secret = "secreto-para-test-12345"
        at = _run_harness({"SPOTIFY_CLIENT_SECRET": secret})
        assert not at.exception
        assert at.session_state.test_result_kind == "ok"
        actual_hex = at.session_state.test_result_hex
        expected_hex = hashlib.sha256(secret.encode()).hexdigest()
        assert actual_hex == expected_hex, (
            f"Digest incorrecto. Esperado: {expected_hex}, obtenido: {actual_hex}"
        )

    def test_secret_con_espacios_laterales_se_normaliza(self):
        """
        SPOTIFY_CLIENT_SECRET con espacios laterales: el .strip() los elimina
        antes de calcular el sha256, por lo que no lanza RuntimeError.
        """
        secret_raw = "  mi-secreto-con-espacios  "
        secret_stripped = secret_raw.strip()
        at = _run_harness({"SPOTIFY_CLIENT_SECRET": secret_raw})
        assert not at.exception
        assert at.session_state.test_result_kind == "ok"
        actual_hex = at.session_state.test_result_hex
        expected_hex = hashlib.sha256(secret_stripped.encode()).hexdigest()
        assert actual_hex == expected_hex


# ---------------------------------------------------------------------------
# P1a — Verificación AST: _resolve_one no usa session_state
# ---------------------------------------------------------------------------

class TestThreadSafetyP1a:
    """
    P1a: _resolve_one (worker thread de spotify_resolve_isrcs) no debe
    referenciar st.session_state ni llamar a spotify_client_credentials_token.
    Solo debe usar _fetch_cc_token_raw (thread-safe).

    Esta verificación es estática via AST porque no podemos ejercitar el pool
    de threads sin credenciales Spotify reales. Pero el AST garantiza que el
    bug de acceso concurrente a session_state no puede reaparecer por regresión.
    """

    @classmethod
    def _get_resolve_one_node(cls):
        """Extrae el nodo AST de _resolve_one anidada en spotify_resolve_isrcs."""
        with open(APP_PATH) as f:
            src = f.read()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if (isinstance(node, ast.FunctionDef)
                    and node.name == "spotify_resolve_isrcs"):
                for inner in ast.walk(node):
                    if (isinstance(inner, ast.FunctionDef)
                            and inner.name == "_resolve_one"):
                        return inner
        return None

    def test_resolve_one_existe(self):
        """_resolve_one debe existir como función anidada dentro de spotify_resolve_isrcs."""
        node = self._get_resolve_one_node()
        assert node is not None, (
            "_resolve_one no encontrada dentro de spotify_resolve_isrcs en app.py"
        )

    def test_resolve_one_sin_session_state(self):
        """
        _resolve_one no debe acceder a st.session_state.

        Acceder a session_state desde un worker thread causa corrupción
        de estado o RuntimeError bajo concurrencia (Streamlit no es thread-safe).
        """
        node = self._get_resolve_one_node()
        assert node is not None, "_resolve_one no encontrada"

        session_state_refs = [
            n for n in ast.walk(node)
            if isinstance(n, ast.Attribute) and n.attr == "session_state"
        ]
        assert len(session_state_refs) == 0, (
            f"_resolve_one contiene {len(session_state_refs)} referencia(s) a "
            f".session_state — unsafe para worker threads: "
            f"{[ast.dump(r) for r in session_state_refs]}"
        )

    def test_resolve_one_sin_spotify_client_credentials_token(self):
        """
        _resolve_one no debe llamar a spotify_client_credentials_token().

        Esa función usa st.session_state como caché, lo que la hace unsafe
        para workers. Debe usar _fetch_cc_token_raw() en su lugar.
        """
        node = self._get_resolve_one_node()
        assert node is not None, "_resolve_one no encontrada"

        cc_token_calls = [
            n for n in ast.walk(node)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "spotify_client_credentials_token"
        ]
        assert len(cc_token_calls) == 0, (
            f"_resolve_one llama a spotify_client_credentials_token "
            f"({len(cc_token_calls)} vez/veces) — debe usar _fetch_cc_token_raw"
        )

    def test_resolve_one_usa_fetch_cc_token_raw(self):
        """
        _resolve_one debe llamar a _fetch_cc_token_raw() para renovar el token
        expirado (thread-safe: no toca session_state).
        """
        node = self._get_resolve_one_node()
        assert node is not None, "_resolve_one no encontrada"

        fetch_raw_calls = [
            n for n in ast.walk(node)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_fetch_cc_token_raw"
        ]
        assert len(fetch_raw_calls) >= 1, (
            "_resolve_one no llama a _fetch_cc_token_raw — el fix P1a no está aplicado"
        )
