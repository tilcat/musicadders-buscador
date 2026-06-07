"""
Smoke test AppTest — buscador Musicadders (app.py)
======================================================
Verifica que la app Streamlit arranca sin excepción con secrets
mínimos mockeados.  No necesita red ni credenciales reales.

Ejecutar:
    /Users/trabajo/dashboard-regalias/.venv/bin/python \
        -m pytest tests/test_smoke_apptest.py -v

Nota: requiere el venv que tenga streamlit instalado.
Si no hay streamlit disponible el test se saltará automáticamente.
"""
import os
import pytest

APP_PATH = os.path.join(os.path.dirname(__file__), "..", "app.py")

# Hash bcrypt de "testpass" — generado offline, sin red
FAKE_HASH = "$2b$12$FGyglEGXxGWz9BJPmsXdR.A9sht8nBUsLgl1e2Crml3ghZjoHopYG"

FAKE_SECRETS = {
    "SOUNDCHARTS_APP_ID": "fake_app_id",
    "SOUNDCHARTS_API_KEY": "fake_api_key",
    "SOUNDCHARTS_MAX_PER_DAY": "5000",
    "APP_BASE_URL": "https://localhost",
    "SPOTIFY_CLIENT_ID": "fake_client_id",
    "SPOTIFY_CLIENT_SECRET": "fake_client_secret",
    "SPOTIFY_CENTRAL_ADMINS": [],
    "users": {"test@musicadders.com": FAKE_HASH},
}

streamlit = pytest.importorskip("streamlit", reason="streamlit no disponible en este entorno")


def _build_at():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP_PATH, default_timeout=20)
    for k, v in FAKE_SECRETS.items():
        at.secrets[k] = v
    return at


def test_app_arranca_sin_excepcion():
    """La app debe arrancar y mostrar el login sin lanzar ninguna excepción."""
    at = _build_at()
    at.run()
    assert not at.exception, f"Excepción al arrancar la app: {at.exception}"


def test_login_form_renderizado():
    """En el primer run (sin sesión) debe aparecer el formulario de login."""
    at = _build_at()
    at.run()
    assert not at.exception, f"Excepción: {at.exception}"
    # Debe haber al menos un botón de submit del formulario de login
    buttons = [w.key for w in at.button]
    assert any("login" in (k or "").lower() or "entrar" in (k or "").lower()
               for k in buttons), (
        f"No se encontró el botón de login. Botones: {buttons}"
    )


def test_login_credenciales_invalidas_no_excepcion():
    """
    Introducir credenciales inválidas en el form de login no debe
    lanzar excepción — debe mostrar error silencioso.
    """
    at = _build_at()
    at.run()
    assert not at.exception

    # Rellenar los dos text_inputs del formulario de login
    if len(at.text_input) >= 2:
        at.text_input[0].set_value("noexiste@example.com")
        at.text_input[1].set_value("wrongpassword")

    # Pulsar el botón de submit
    login_btns = [w for w in at.button
                  if "login" in (w.key or "").lower() or "entrar" in (w.key or "").lower()]
    if login_btns:
        login_btns[0].click().run()
        assert not at.exception, f"Excepción tras login fallido: {at.exception}"
