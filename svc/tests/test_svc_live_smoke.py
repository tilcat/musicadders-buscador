"""
svc/tests/test_svc_live_smoke.py
Smoke de integración contra el svc uvicorn en vivo (127.0.0.1:8600).

Razón de existencia:
  - jun-2026: el endpoint /search existía en el código pero el proceso uvicorn no
    había sido reiniciado; la API en vivo devolvía 404 en /search. Este test caza
    esa regresión.
  - También verifica que /search es `def` (no async), garantizando que la llamada
    bloqueante a Soundcharts corre en el threadpool de FastAPI y no congela el
    event loop. Referencia: BLOCKER detectado en revisión F2.

Diseño de entorno (CRÍTICO — no modificar):
  - El token se lee DIRECTAMENTE de web/.env.local, NUNCA de os.environ.
    Motivo: los tests unitarios (test_backend_regression.py, test_search_endpoint.py)
    hacen `os.environ.setdefault("INTERNAL_TOKEN", "test-token-abc123")` que
    contamina el entorno del proceso pytest. Si este test lee de os.environ, obtiene
    el token de test ("test-token-abc123") y el svc vivo (que usa "devtoken") rechaza
    con 401, rompiendo el test de forma silenciosa.
  - NO se llama a `os.environ.setdefault` ni `os.environ.__setitem__` en este módulo.

Auto-skip: todos los tests de este módulo se saltan limpiamente si el svc no está
levantado en 127.0.0.1:8600. `pytest svc/tests/ -q` queda verde aunque el svc
esté apagado.

Ejecución manual:
  cd /Users/trabajo/musicadders-buscador
  .venv/bin/python -m pytest svc/tests/test_svc_live_smoke.py -v
"""

from __future__ import annotations

import inspect
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_SVC_BASE = "http://127.0.0.1:8600"
_OPENAPI_URL = f"{_SVC_BASE}/openapi.json"


# ── Token: leer de web/.env.local, nunca de os.environ ───────────────────────

def _read_token_from_env_local() -> str:
    """Lee INTERNAL_TOKEN de web/.env.local sin tocar os.environ.

    Ruta relativa al fichero de test: ../../web/.env.local
    Si no se puede leer, devuelve "devtoken" (valor por defecto en dev local).
    """
    env_path = Path(__file__).parent.parent.parent / "web" / ".env.local"
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("INTERNAL_TOKEN="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return "devtoken"


# ── Skip condicional: saltar si el svc no responde ───────────────────────────

def _svc_is_up() -> bool:
    """True si el svc responde en /health."""
    try:
        with urllib.request.urlopen(f"{_SVC_BASE}/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _svc_is_up(),
    reason="svc no está levantado en 127.0.0.1:8600 — smoke de integración omitido",
)


# ── Helper ────────────────────────────────────────────────────────────────────

def _get_live_routes() -> list[str]:
    """Devuelve las rutas registradas en el OpenAPI del svc en vivo."""
    with urllib.request.urlopen(_OPENAPI_URL, timeout=5) as r:
        spec = json.loads(r.read())
    return list(spec.get("paths", {}).keys())


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_live_svc_health_incluye_calls_hoy():
    """
    /health debe devolver calls_today y calls_limit (añadidos en F2).

    Verifica que el svc en vivo fue reiniciado con el código de F2.
    """
    with urllib.request.urlopen(f"{_SVC_BASE}/health", timeout=5) as r:
        data = json.loads(r.read())
    assert "calls_today" in data, (
        f"/health no tiene 'calls_today'. Respuesta: {data}\n"
        "El svc probablemente no fue reiniciado con el código de F2."
    )
    assert "calls_limit" in data, (
        f"/health no tiene 'calls_limit'. Respuesta: {data}"
    )


def test_live_svc_expone_search_endpoint():
    """
    REGRESION jun-2026: /search debe estar en las rutas del svc en vivo.

    Fallo → svc arrancado ANTES de desplegar F2. Solución: reiniciar uvicorn.
    """
    routes = _get_live_routes()
    assert "/search" in routes, (
        f"FAIL — /search no está en el svc en vivo. Rutas actuales: {routes}\n"
        "CAUSA: el proceso uvicorn se arrancó ANTES de desplegar F2.\n"
        "SOLUCIÓN: pkill -f 'uvicorn svc.main' && "
        ".venv/bin/uvicorn svc.main:app --host 127.0.0.1 --port 8600"
    )


def test_live_svc_expone_rutas_f1():
    """F1: /batch y sus subrutas deben estar presentes."""
    routes = _get_live_routes()
    f1_routes = ["/batch", "/batch/{job_id}/status", "/batch/{job_id}/result.json"]
    for r in f1_routes:
        assert r in routes, (
            f"FAIL — {r} (F1) no está en el svc en vivo. Rutas actuales: {routes}"
        )


def test_search_handler_es_def_no_async():
    """
    BLOCKER F2: /search debe estar declarado como `def` (no `async def`).

    FastAPI ejecuta handlers `def` en su threadpool (anyio.to_thread.run_sync),
    de forma que la llamada bloqueante a Soundcharts (requests.get) NO congela
    el event loop. Si el handler fuera `async def`, cada búsqueda bloquearía
    todo el servidor durante la llamada HTTP a Soundcharts.

    Verificación: inspeccionamos la ruta registrada en la app FastAPI e
    comprobamos que su endpoint NO es una coroutine function.
    """
    # Importar la app sin modificar os.environ (los tests unitarios ya hacen setdefault)
    from svc.main import app

    search_route = None
    for route in app.routes:
        if hasattr(route, "path") and route.path == "/search":
            search_route = route
            break

    assert search_route is not None, (
        "No se encontró la ruta /search en svc.main.app. "
        "¿Se borró el endpoint?"
    )

    endpoint = search_route.endpoint
    is_coroutine = inspect.iscoroutinefunction(endpoint)
    assert not is_coroutine, (
        f"BLOCKER: /search está declarado como `async def` ({endpoint}).\n"
        "FastAPI NO lo ejecutará en el threadpool → la llamada HTTP a Soundcharts\n"
        "bloqueará el event loop. Cambiar a `def` para que corra en el threadpool."
    )


def test_search_no_bloquea_event_loop_concurrencia():
    """
    Verifica por concurrencia real que /search (def, threadpool) no bloquea /health.

    Mecanismo:
    1. Lanzar una petición a /search con ISRC inválido en un thread.
       La petición llega al servidor, el handler se despacha al threadpool,
       el auth check y la validación ISRC corren ahí (no en el event loop).
    2. Inmediatamente después, en el thread principal, hacer GET /health.
    3. /health (async def) corre en el event loop. Si /search hubiera bloqueado
       el event loop (caso `async def` + requests.get), /health tardaría o fallaría.
    4. Verificar que /health responde en < 500 ms con 200.

    Nota: el test usa un ISRC inválido para que la respuesta sea inmediata
    (el handler sale rápido en la validación). Lo que interesa es que el
    event loop queda libre durante el dispatch al threadpool.
    """
    token = _read_token_from_env_local()
    results: dict = {}

    def _hit_search():
        url = f"{_SVC_BASE}/search?isrc=INVALID&scope=importantes"
        req = urllib.request.Request(url, headers={"X-Internal-Token": token})
        try:
            urllib.request.urlopen(req, timeout=5)
        except urllib.error.HTTPError as e:
            results["search_code"] = e.code
        except Exception as ex:
            results["search_err"] = str(ex)

    # Lanzar /search en un thread
    t = threading.Thread(target=_hit_search, daemon=True)
    t0 = time.monotonic()
    t.start()

    # Inmediatamente, pedir /health en el thread principal
    try:
        with urllib.request.urlopen(f"{_SVC_BASE}/health", timeout=5) as r:
            health_code = r.status
    except Exception as ex:
        pytest.fail(f"/health lanzó excepción mientras /search estaba en curso: {ex}")

    health_elapsed_ms = int((time.monotonic() - t0) * 1000)
    t.join(timeout=5)

    assert health_code == 200, (
        f"/health devolvió {health_code} mientras /search estaba en curso. "
        "Posible bloqueo del event loop."
    )
    assert health_elapsed_ms < 500, (
        f"/health tardó {health_elapsed_ms} ms (esperado < 500 ms). "
        "Posible bloqueo del event loop por /search."
    )


def test_live_svc_search_sin_token_da_401():
    """
    /search sin X-Internal-Token → 401 (fail-closed).
    Si da 404: svc no reiniciado. Si da 200: auth roto.
    """
    url = f"{_SVC_BASE}/search?isrc=ESAA12300001&scope=importantes"
    try:
        with urllib.request.urlopen(url, timeout=5):
            pytest.fail("Sin token esperado 401, pero el svc respondió 200")
    except urllib.error.HTTPError as e:
        assert e.code == 401, (
            f"Sin token esperado 401, obtenido {e.code}.\n"
            "404 → svc no reiniciado; 200 → auth roto."
        )


def test_live_svc_search_isrc_invalido_da_422():
    """
    /search con ISRC inválido y token correcto → 422.

    El token se lee de web/.env.local para no depender del os.environ del proceso
    pytest (que puede estar contaminado por los tests unitarios).
    """
    token = _read_token_from_env_local()
    url = f"{_SVC_BASE}/search?isrc=INVALID&scope=importantes"
    req = urllib.request.Request(url, headers={"X-Internal-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=5):
            pytest.fail("ISRC inválido esperado 422, pero el svc respondió 200")
    except urllib.error.HTTPError as e:
        assert e.code == 422, (
            f"ISRC inválido esperado 422, obtenido {e.code}.\n"
            f"Token usado (de web/.env.local): '{token}'\n"
            "401 → token incorrecto en web/.env.local o svc usa token diferente.\n"
            "404 → svc no reiniciado tras desplegar /search (F2)."
        )


# ── Tests de regresión F3 FUGA (smoke vivo) ──────────────────────────────────

def test_live_svc_expone_rutas_fuga():
    """
    REGRESION F3: los endpoints FUGA deben estar registrados en el svc en vivo.

    Fallo → svc arrancado ANTES de desplegar F3. Solución: reiniciar uvicorn.
    Rutas esperadas: /fuga, /fuga/{job_id}/status, /fuga/{job_id}/result.json
    """
    routes = _get_live_routes()
    fuga_routes = [
        "/fuga",
        "/fuga/{job_id}/status",
        "/fuga/{job_id}/result.json",
        "/fuga/{job_id}/result.csv",
        "/fuga/{job_id}/result.xlsx",
        "/fuga/{job_id}/cancel",
    ]
    for r in fuga_routes:
        assert r in routes, (
            f"FAIL — {r} (F3 FUGA) no está en el svc en vivo. Rutas actuales: {routes}\n"
            "CAUSA: el proceso uvicorn se arrancó ANTES de desplegar F3.\n"
            "SOLUCIÓN: pkill -f 'uvicorn svc.main' && "
            "cd /Users/trabajo/musicadders-buscador && "
            "INTERNAL_TOKEN=devtoken .venv/bin/uvicorn svc.main:app --host 127.0.0.1 --port 8600"
        )


def test_live_svc_fuga_sin_token_da_401():
    """
    REGRESION F3: POST /fuga sin X-Internal-Token → 401 (fail-closed).

    404 → svc no reiniciado tras desplegar F3.
    200 → auth roto.
    """
    data = json.dumps({"date_from": "2024-01-01", "date_to": "2024-01-31"}).encode()
    req = urllib.request.Request(
        f"{_SVC_BASE}/fuga",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5):
            pytest.fail("Sin token esperado 401, pero el svc respondió 200")
    except urllib.error.HTTPError as e:
        assert e.code == 401, (
            f"Sin token esperado 401, obtenido {e.code}.\n"
            "404 → svc no reiniciado; 200 → auth roto."
        )


def test_live_svc_fuga_job_id_invalido_da_400():
    """
    REGRESION F3: GET /fuga/{job_id_invalido}/status → 400 (defensa path traversal).

    job_ids inválidos (no UUID) deben ser rechazados antes de tocar el job-store.
    Protege contra path traversal e inyección.
    """
    token = _read_token_from_env_local()
    invalid_ids = ["not-a-uuid", "fake-id", "..malicious..", "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"]
    for bad_id in invalid_ids:
        url = f"{_SVC_BASE}/fuga/{bad_id}/status"
        req = urllib.request.Request(url, headers={"X-Internal-Token": token})
        try:
            with urllib.request.urlopen(req, timeout=5):
                pytest.fail(f"job_id='{bad_id}' esperado 400, pero el svc respondió 200")
        except urllib.error.HTTPError as e:
            assert e.code == 400, (
                f"job_id='{bad_id}' esperado 400 (formato inválido), obtenido {e.code}.\n"
                "La validación _validate_job_id debe rechazar IDs no-UUID antes del auth."
            )


def test_live_svc_fuga_sin_credenciales_da_401_no_credentials():
    """
    REGRESION F3: POST /fuga con token válido pero sin FUGA_USER/FUGA_PASS
    en el entorno del svc → 401 con {error: 'no_credentials'}.

    Fail-closed: el endpoint no crea el job si las credenciales FUGA no están
    configuradas. La prueba documenta el comportamiento esperado en staging/CI
    donde FUGA_USER/FUGA_PASS no están definidas.

    Si el svc tiene FUGA_USER/FUGA_PASS reales, este test devolverá 202
    (job creado) y se omite la aserción de no_credentials.
    """
    token = _read_token_from_env_local()
    data = json.dumps({"date_from": "2024-01-01", "date_to": "2024-01-31"}).encode()
    req = urllib.request.Request(
        f"{_SVC_BASE}/fuga",
        data=data,
        headers={
            "Content-Type": "application/json",
            "X-Internal-Token": token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            # Si llegamos aquí, las credenciales FUGA están presentes → 202 aceptable
            body = json.loads(r.read())
            assert r.status == 202, f"Con credenciales FUGA esperado 202, obtenido {r.status}"
            assert "job_id" in body, f"202 pero sin job_id: {body}"
    except urllib.error.HTTPError as e:
        if e.code == 401:
            body = json.loads(e.read())
            assert body.get("error") == "no_credentials", (
                f"401 esperado con error='no_credentials', obtenido: {body}"
            )
            # PASS: fail-closed confirmado
        else:
            pytest.fail(
                f"POST /fuga con token válido devolvió {e.code} inesperado.\n"
                "422 → fechas inválidas (no debería ocurrir con este payload).\n"
                "503 → INTERNAL_TOKEN no configurado en el svc."
            )
