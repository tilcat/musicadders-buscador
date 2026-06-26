"""
Regresion — procesado batch troceado (chunk + checkpoint) — app.py (musicadders-buscador)
==========================================================================================

Cubre el nuevo flujo de tab_batch() con st.session_state["batch_job"] + reruns
por chunk (BATCH_CHUNK=10, MAX_CHUNK_SECONDS=25).  Sin ninguna llamada real de red:
search_isrc se mockea completamente en los harnesses.

Estrategia:
-----------
  AppTest.run() ejecuta TODOS los st.rerun() internos en un solo call (se comporta
  como el servidor Streamlit que relanza el script hasta que no hay mas reruns).
  Por tanto, un solo at.run() con job inyectado procesa todos los chunks hasta done=True.

  Para testear cancelar a mitad, el harness intercepta app.st.rerun haciendo que sea
  un no-op: asi cada at.run() procesa exactamente 1 chunk y el boton Cancelar queda
  visible para el test.

  Los harnesses inyectan batch_job directamente en session_state (simulando que el
  usuario ya pulso "Procesar batch") y llaman a tab_batch() directamente sin st.tabs,
  igual que probe_create_playlist_mock.py con _tab_playlist_central().

  NOTA sobre el test de cancelar:
  Con st.rerun() interceptado a no-op, cuando el boton Cancelar se pulsa:
    - job["cancelled"] = True se setea
    - st.rerun() (no-op) no detiene el script
    - el chunk ACTUAL se procesa igualmente (el rerun real lo habria detenido antes)
  Por eso tras Cancelar en el run-2, idx avanza 1 chunk mas (idx=20, no 10).
  Este comportamiento esta documentado aqui como diferencia con el flujo de produccion;
  los asserts verifican que cancelled=True, done=True y batch_result tiene datos parciales.
  Los ISRCs restantes tras la cancelacion (isrcs[idx:]) se agregan a not_found con
  "no procesado (cancelado)".

Casos cubiertos:
----------------
  (A) Job de 50 ISRCs (BATCH_CHUNK=10 real) se completa en 1 at.run() (reruns internos),
      procesando los 50 exactamente una vez (sin saltar ni duplicar), done=True al final.

  (B) batch_result / batch_isrcs tras done tienen los N ISRCs y los placements esperados.

  (C) calls_used / calls_today se cuentan correctamente (sin doble conteo).

  (D) Cancelar a mitad (click en batch_cancel tras el 1er chunk con rerun interceptado)
      -> cancelled=True, done=True, conserva lo procesado, no sigue en reruns adicionales.
      Los ISRCs no procesados aparecen en not_found como "no procesado (cancelado)".

  (E) Tras done, reruns adicionales no avanzan idx ni meta (job.done bloquea Fase A).

  (F) 429 de Soundcharts: lookup_isrc_to_uuid / get_song_playlists lanza RuntimeError
      -> el ISRC va a not_found con mensaje de error (no se traga silenciosamente).

  (G) batch_job_msg: el mensaje de fin/cancelacion queda en session_state y se consume
      una sola vez (pop) al entrar en Fase B.

Ejecutar:
    /Users/trabajo/dashboard-regalias/.venv/bin/python \\
        -m pytest tests/test_batch_chunked.py -v
"""

import sys
import os
import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

streamlit = pytest.importorskip("streamlit", reason="streamlit no disponible")

# ---------------------------------------------------------------------------
# Secrets minimos que la app necesita
# ---------------------------------------------------------------------------
_BASE_SECRETS = {
    "SOUNDCHARTS_APP_ID": "fake_app_id",
    "SOUNDCHARTS_API_KEY": "fake_api_key",
    "SOUNDCHARTS_MAX_PER_DAY": "5000",
    "APP_BASE_URL": "https://localhost",
    "SPOTIFY_CLIENT_ID": "fake_cid",
    "SPOTIFY_CLIENT_SECRET": "fake_cs",
    "SPOTIFY_CENTRAL_ADMINS": [],
    "users": {"test@musicadders.com":
              "$2b$12$FGyglEGXxGWz9BJPmsXdR.A9sht8nBUsLgl1e2Crml3ghZjoHopYG"},
}

# N ISRCs: 50. Con BATCH_CHUNK=10 -> 5 chunks (0-10, 10-20, 20-30, 30-40, 40-50).
_N = 50
_ISRCS = [f"FAKE{i:04d}" for i in range(_N)]
_CHUNK = 10  # BATCH_CHUNK en app.py (bajado de 20 a 10)


def _make_at(harness_src: str, extra_secrets: dict = None):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_string(harness_src, default_timeout=60)
    for k, v in {**_BASE_SECRETS, **(extra_secrets or {})}.items():
        at.secrets[k] = v
    return at


def _ss_get(at, key, default=None):
    """Accede a at.session_state[key] de forma segura (no tiene .get())."""
    try:
        return at.session_state[key]
    except (KeyError, AttributeError):
        return default


# ---------------------------------------------------------------------------
# HARNESS A/B/C/E — job completo (reruns internos automáticos, sin interceptar)
#
# AppTest.run() ejecuta todos los st.rerun() internos: el job se completa en
# 1 sola llamada a at.run().
# ---------------------------------------------------------------------------
_HARNESS_COMPLETE = f"""
import sys
sys.path.insert(0, {_REPO_ROOT!r})
import streamlit as st
import app

# ---- mock de search_isrc (sin red) ----
def _fake_search_isrc(isrc, platforms, buster=""):
    return {{
        "meta": {{
            "uuid": isrc,
            "song_name": f"Song_{{isrc}}",
            "credit_name": "FakeArtist",
        }},
        "playlists": [{{
            "playlist_id": f"PL_{{isrc}}",
            "platform": "spotify",
            "playlist_name": f"FakePL_{{isrc}}",
            "followers": 1000,
            "position": 1,
        }}],
        "calls_used": 2,
    }}

app.search_isrc = _fake_search_isrc

# ---- inyectar job en session_state ----
_isrcs = {_ISRCS!r}
if "batch_job" not in st.session_state:
    st.session_state["batch_job"] = {{
        "isrcs": _isrcs,
        "platforms": ["spotify"],
        "scope": "spotify",
        "buster": "",
        "idx": 0,
        "meta": {{}},
        "playlists": [],
        "not_found": [],
        "calls_used": 0,
        "done": False,
        "cancelled": False,
    }}

# Llamar a la funcion real de la pestana
app.tab_batch()
"""

# ---------------------------------------------------------------------------
# HARNESS D — cancelar a mitad
#
# Intercepta app.st.rerun para que sea no-op: cada at.run() procesa 1 chunk
# y el boton Cancelar permanece visible entre runs.
# ---------------------------------------------------------------------------
_HARNESS_CANCEL = f"""
import sys
sys.path.insert(0, {_REPO_ROOT!r})
import streamlit as st
import app

# ---- mock de search_isrc (sin red) ----
def _fake_search_isrc(isrc, platforms, buster=""):
    return {{
        "meta": {{
            "uuid": isrc,
            "song_name": f"Song_{{isrc}}",
            "credit_name": "FakeArtist",
        }},
        "playlists": [{{
            "playlist_id": f"PL_{{isrc}}",
            "platform": "spotify",
            "playlist_name": f"FakePL_{{isrc}}",
            "followers": 1000,
            "position": 1,
        }}],
        "calls_used": 2,
    }}
app.search_isrc = _fake_search_isrc

# ---- interceptar st.rerun para que sea no-op (1 chunk por run de AppTest) ----
_rerun_calls = []
def _noop_rerun():
    _rerun_calls.append(1)
app.st.rerun = _noop_rerun

# ---- inyectar job ----
_isrcs = {_ISRCS!r}
if "batch_job" not in st.session_state:
    st.session_state["batch_job"] = {{
        "isrcs": _isrcs,
        "platforms": ["spotify"],
        "scope": "spotify",
        "buster": "",
        "idx": 0,
        "meta": {{}},
        "playlists": [],
        "not_found": [],
        "calls_used": 0,
        "done": False,
        "cancelled": False,
    }}

app.tab_batch()
"""


# ===========================================================================
# (A) Job de N ISRCs procesado: exactamente N, sin duplicar, done=True
# ===========================================================================

class TestA_CompletionAndCoverage:

    def test_done_en_un_solo_run(self):
        """(A) Con reruns internos de AppTest, done=True tras 1 sola llamada a at.run()."""
        at = _make_at(_HARNESS_COMPLETE)
        at.run()
        assert not at.exception, f"Excepcion en run: {at.exception}"
        job = _ss_get(at, "batch_job")
        assert job is not None, "batch_job debe existir en session_state"
        assert job["done"] is True, (
            f"job.done debe ser True tras completar los {_N} ISRCs, es {job['done']}"
        )

    def test_idx_final_igual_a_N(self):
        """(A) Tras done, job['idx'] == N (todos los ISRCs procesados)."""
        at = _make_at(_HARNESS_COMPLETE)
        at.run()
        assert not at.exception, f"Excepcion: {at.exception}"
        job = _ss_get(at, "batch_job")
        assert job["idx"] == _N, (
            f"idx final debe ser {_N} (todos procesados), fue {job['idx']}"
        )

    def test_meta_tiene_N_isrcs_sin_duplicar(self):
        """(A) job['meta'] tiene exactamente N entradas, una por ISRC, sin duplicados."""
        at = _make_at(_HARNESS_COMPLETE)
        at.run()
        assert not at.exception, f"Excepcion: {at.exception}"
        job = _ss_get(at, "batch_job")
        assert len(job["meta"]) == _N, (
            f"meta debe tener {_N} ISRCs, tiene {len(job['meta'])}"
        )
        assert set(job["meta"].keys()) == set(_ISRCS), (
            "meta debe contener exactamente los ISRCs del job"
        )

    def test_playlists_sin_duplicar(self):
        """(A) Cada ISRC genera exactamente 1 playlist entry (sin duplicar por reruns)."""
        at = _make_at(_HARNESS_COMPLETE)
        at.run()
        assert not at.exception, f"Excepcion: {at.exception}"
        job = _ss_get(at, "batch_job")
        # Mock devuelve 1 playlist por ISRC -> esperamos N playlists total
        assert len(job["playlists"]) == _N, (
            f"playlists debe tener {_N} entradas (1 por ISRC), tiene {len(job['playlists'])}"
        )
        pl_isrcs = [p["isrc"] for p in job["playlists"]]
        duplicados = sorted(set(i for i in pl_isrcs if pl_isrcs.count(i) > 1))
        assert not duplicados, (
            f"Hay ISRCs duplicados en playlists (procesados mas de 1 vez): {duplicados}"
        )

    def test_not_found_vacio(self):
        """(A) not_found vacio cuando el mock resuelve todos los ISRCs."""
        at = _make_at(_HARNESS_COMPLETE)
        at.run()
        assert not at.exception, f"Excepcion: {at.exception}"
        job = _ss_get(at, "batch_job")
        assert job["not_found"] == [], (
            f"not_found debe estar vacio, tiene: {job['not_found']}"
        )


# ===========================================================================
# (B) batch_result / batch_isrcs tras done tienen los datos correctos
# ===========================================================================

class TestB_FinalResult:

    def test_batch_result_existe(self):
        """(B) batch_result en session_state al terminar."""
        at = _make_at(_HARNESS_COMPLETE)
        at.run()
        assert not at.exception, f"Excepcion: {at.exception}"
        assert _ss_get(at, "batch_result") is not None, (
            "batch_result debe existir en session_state tras done"
        )

    def test_batch_isrcs_igual_a_input(self):
        """(B) batch_isrcs == lista original de N ISRCs."""
        at = _make_at(_HARNESS_COMPLETE)
        at.run()
        assert not at.exception, f"Excepcion: {at.exception}"
        result_isrcs = _ss_get(at, "batch_isrcs")
        assert result_isrcs is not None, "batch_isrcs debe existir"
        assert list(result_isrcs) == _ISRCS, (
            f"batch_isrcs debe ser la lista original de {_N} ISRCs"
        )

    def test_batch_result_meta_N_isrcs(self):
        """(B) batch_result['meta'] tiene N ISRCs resueltos."""
        at = _make_at(_HARNESS_COMPLETE)
        at.run()
        assert not at.exception, f"Excepcion: {at.exception}"
        res = _ss_get(at, "batch_result")
        assert len(res["meta"]) == _N, (
            f"batch_result.meta debe tener {_N} ISRCs, tiene {len(res['meta'])}"
        )

    def test_batch_result_playlists_N(self):
        """(B) batch_result['playlists'] tiene N placements (1 por ISRC del mock)."""
        at = _make_at(_HARNESS_COMPLETE)
        at.run()
        assert not at.exception, f"Excepcion: {at.exception}"
        res = _ss_get(at, "batch_result")
        assert len(res["playlists"]) == _N, (
            f"batch_result.playlists debe tener {_N} entradas, tiene {len(res['playlists'])}"
        )

    def test_batch_result_not_found_vacio(self):
        """(B) batch_result['not_found'] vacio cuando todos los ISRCs se resuelven."""
        at = _make_at(_HARNESS_COMPLETE)
        at.run()
        assert not at.exception, f"Excepcion: {at.exception}"
        res = _ss_get(at, "batch_result")
        assert res["not_found"] == [], (
            f"not_found debe estar vacio, tiene: {res['not_found']}"
        )


# ===========================================================================
# (C) calls_used / calls_today sin doble conteo
# ===========================================================================

class TestC_CallsCounting:

    def test_calls_used_es_N_por_2(self):
        """(C) job.calls_used == N * 2 (mock devuelve calls_used=2 por ISRC, sin doble conteo)."""
        expected = _N * 2
        at = _make_at(_HARNESS_COMPLETE)
        at.run()
        assert not at.exception, f"Excepcion: {at.exception}"
        job = _ss_get(at, "batch_job")
        assert job["calls_used"] == expected, (
            f"job.calls_used debe ser {expected} ({_N} ISRCs x 2), fue {job['calls_used']}"
        )

    def test_calls_today_acumulado_correctamente(self):
        """(C) calls_today == N * 2 (sin doble conteo entre chunks)."""
        expected = _N * 2
        at = _make_at(_HARNESS_COMPLETE)
        at.run()
        assert not at.exception, f"Excepcion: {at.exception}"
        calls_today = _ss_get(at, "calls_today", 0)
        assert calls_today == expected, (
            f"calls_today debe ser {expected}, fue {calls_today}"
        )

    def test_calls_today_parte_de_valor_previo(self):
        """(C) Si calls_today parte de 10, termina en 10 + N*2 (acumulacion, no reset)."""
        expected_extra = _N * 2
        at = _make_at(_HARNESS_COMPLETE)
        # Pre-inyectar calls_today antes del run
        at.session_state["calls_today"] = 10
        at.run()
        assert not at.exception, f"Excepcion: {at.exception}"
        calls_today = _ss_get(at, "calls_today", 0)
        assert calls_today == 10 + expected_extra, (
            f"calls_today debe ser 10 + {expected_extra} = {10 + expected_extra}, "
            f"fue {calls_today}"
        )

    def test_batch_result_calls_used_coincide_con_job(self):
        """(C) batch_result.calls_used == job.calls_used (no se descarta al volcar)."""
        at = _make_at(_HARNESS_COMPLETE)
        at.run()
        assert not at.exception, f"Excepcion: {at.exception}"
        job = _ss_get(at, "batch_job")
        res = _ss_get(at, "batch_result")
        assert res["calls_used"] == job["calls_used"], (
            f"batch_result.calls_used ({res['calls_used']}) debe coincidir "
            f"con job.calls_used ({job['calls_used']})"
        )


# ===========================================================================
# (D) Cancelar a mitad -> cancelled/done, conserva lo procesado, no sigue
#
# Con st.rerun() interceptado (noop), cada at.run() procesa 1 chunk.
# Run 1: procesa chunk 0-10 (idx=10, done=False, boton Cancelar visible).
# Click Cancelar + Run 2: el click setea cancelled=True al inicio, luego el
# codigo procesa el chunk actual (10-20) antes de detectar cancelled en el
# gate "new_idx >= total or job.get('cancelled')".
# Resultado: idx=20, done=True, cancelled=True (comportamiento con noop-rerun;
# en produccion real el rerun detiene el script antes de procesar el chunk).
# ISRCs isrcs[20:] (30 ISRCs) van a not_found como "no procesado (cancelado)".
# ===========================================================================

class TestD_Cancel:

    def _setup_at_after_chunk1(self):
        """Crea AppTest con noop-rerun, ejecuta 1 chunk, devuelve at listo para cancel."""
        at = _make_at(_HARNESS_CANCEL)
        # Run 1: procesa los primeros 10 ISRCs (chunk 0-10, BATCH_CHUNK=10)
        at.run()
        assert not at.exception, f"Excepcion en run 1 (chunk 1): {at.exception}"
        job = _ss_get(at, "batch_job")
        assert job is not None and not job["done"], (
            f"Tras 1er chunk el job no debe estar done (rerun interceptado). job={job}"
        )
        assert job["idx"] == _CHUNK, (
            f"Tras 1er chunk, idx debe ser {_CHUNK}, es {job['idx']}"
        )
        return at

    def test_cancel_boton_visible_tras_primer_chunk(self):
        """(D) El boton Cancelar es visible (key='batch_cancel') tras el 1er chunk."""
        at = self._setup_at_after_chunk1()
        cancel_btn = next((b for b in at.button if b.key == "batch_cancel"), None)
        assert cancel_btn is not None, (
            f"batch_cancel debe ser visible. Botones: {[b.key for b in at.button]}"
        )

    def test_cancel_sets_done_and_cancelled(self):
        """(D) Tras pulsar Cancelar y un run, job.done=True y job.cancelled=True."""
        at = self._setup_at_after_chunk1()
        cancel_btn = next((b for b in at.button if b.key == "batch_cancel"), None)
        cancel_btn.click()
        at.run()
        assert not at.exception, f"Excepcion tras Cancelar: {at.exception}"
        job = _ss_get(at, "batch_job")
        assert job["done"] is True, (
            f"job.done debe ser True tras cancelar, es {job['done']}"
        )
        assert job["cancelled"] is True, (
            f"job.cancelled debe ser True tras cancelar, es {job['cancelled']}"
        )

    def test_cancel_conserva_datos_parciales(self):
        """(D) Tras cancelar, meta y playlists tienen datos (>0 ISRCs procesados)."""
        at = self._setup_at_after_chunk1()
        cancel_btn = next((b for b in at.button if b.key == "batch_cancel"), None)
        cancel_btn.click()
        at.run()
        assert not at.exception, f"Excepcion tras Cancelar: {at.exception}"
        job = _ss_get(at, "batch_job")
        assert len(job["meta"]) > 0, (
            "Tras cancelar, meta debe tener datos parciales (>0 ISRCs procesados)"
        )
        assert len(job["playlists"]) > 0, (
            "Tras cancelar, playlists debe tener datos parciales"
        )

    def test_cancel_batch_result_volcado(self):
        """(D) batch_result se vuelca en session_state tras cancelar."""
        at = self._setup_at_after_chunk1()
        cancel_btn = next((b for b in at.button if b.key == "batch_cancel"), None)
        cancel_btn.click()
        at.run()
        assert not at.exception, f"Excepcion tras Cancelar: {at.exception}"
        res = _ss_get(at, "batch_result")
        assert res is not None, "batch_result debe existir tras cancelar"
        assert len(res["meta"]) > 0, (
            "batch_result.meta debe tener datos parciales tras cancelar"
        )

    def test_cancel_idx_no_avanza_en_reruns_adicionales(self):
        """(D) Tras cancelled+done, reruns adicionales no avanzan idx (Fase A bloqueada)."""
        at = self._setup_at_after_chunk1()
        cancel_btn = next((b for b in at.button if b.key == "batch_cancel"), None)
        cancel_btn.click()
        at.run()
        assert not at.exception, f"Excepcion tras Cancelar: {at.exception}"
        job_tras_cancel = _ss_get(at, "batch_job")
        idx_tras_cancel = job_tras_cancel["idx"]
        meta_tras_cancel = len(job_tras_cancel["meta"])

        # Un run adicional: job.done=True => Fase B => no debe procesar nada
        at.run()
        assert not at.exception, f"Excepcion en run extra: {at.exception}"
        job_extra = _ss_get(at, "batch_job")
        assert job_extra["idx"] == idx_tras_cancel, (
            f"idx no debe avanzar tras cancelar (job done). "
            f"Antes: {idx_tras_cancel}, despues: {job_extra['idx']}"
        )
        assert len(job_extra["meta"]) == meta_tras_cancel, (
            f"meta no debe crecer tras cancelar. "
            f"Antes: {meta_tras_cancel}, despues: {len(job_extra['meta'])}"
        )


# ===========================================================================
# (E) Tras done, el job no se reprocesa en reruns adicionales
#
# Usa el harness con noop-rerun para poder hacer multiples at.run() sin que
# AppTest falle por cambio de widgets (Fase A -> Fase B cambia los widgets,
# lo que confunde a AppTest si se usa el harness con reruns internos reales).
# Con noop-rerun: run1=chunk1, run2=chunk2, run3=chunk3+done, run4=post-done.
# Run 4 verifica que idx y calls_today no crecen (Fase B activa, no Fase A).
# ===========================================================================

class TestE_NorerunAfterDone:

    def _run_until_done_noop(self, at, max_runs: int = 10):
        """Con noop-rerun, avanza run a run hasta que done=True. Devuelve runs usados."""
        for i in range(max_runs):
            at.run()
            assert not at.exception, f"Excepcion en run {i+1}: {at.exception}"
            job = _ss_get(at, "batch_job")
            if job and job.get("done"):
                return i + 1
        raise AssertionError(
            f"job no llego a done tras {max_runs} runs. "
            f"job={_ss_get(at, 'batch_job')}"
        )

    def test_idx_no_avanza_tras_done(self):
        """(E) Tras done=True, un run adicional no avanza idx (Fase B activa)."""
        at = _make_at(_HARNESS_CANCEL)  # HARNESS_CANCEL tiene noop-rerun
        runs = self._run_until_done_noop(at)
        job_done = _ss_get(at, "batch_job")
        idx_final = job_done["idx"]
        meta_count = len(job_done["meta"])
        assert job_done["done"] is True, f"Precondicion: done debe ser True (runs={runs})"

        # Run adicional post-done: Fase B activa, no debe procesar nada
        at.run()
        assert not at.exception, f"Excepcion en run extra post-done: {at.exception}"
        job2 = _ss_get(at, "batch_job")
        assert job2["idx"] == idx_final, (
            f"idx no debe avanzar tras done. idx_final={idx_final}, ahora={job2['idx']}"
        )
        assert len(job2["meta"]) == meta_count, (
            f"meta no debe crecer tras done. meta_count={meta_count}, ahora={len(job2['meta'])}"
        )

    def test_calls_today_no_crece_tras_done(self):
        """(E) calls_today no aumenta en runs post-done (job.done bloquea Fase A)."""
        at = _make_at(_HARNESS_CANCEL)
        self._run_until_done_noop(at)
        calls_after_done = _ss_get(at, "calls_today", 0)

        at.run()
        assert not at.exception, f"Excepcion en run extra 1: {at.exception}"
        at.run()
        assert not at.exception, f"Excepcion en run extra 2: {at.exception}"

        calls_after_extras = _ss_get(at, "calls_today", 0)
        assert calls_after_extras == calls_after_done, (
            f"calls_today no debe aumentar tras done. "
            f"Tras done: {calls_after_done}, tras extras: {calls_after_extras}"
        )

    def test_batch_result_estable_tras_reruns_post_done(self):
        """(E) batch_result no cambia tras runs adicionales post-done."""
        at = _make_at(_HARNESS_CANCEL)
        self._run_until_done_noop(at)
        res_inicial = _ss_get(at, "batch_result")
        assert res_inicial is not None, "batch_result debe existir tras done"
        calls_iniciales = res_inicial["calls_used"]
        meta_inicial = len(res_inicial["meta"])

        at.run()
        assert not at.exception
        at.run()
        assert not at.exception

        res_final = _ss_get(at, "batch_result")
        assert res_final["calls_used"] == calls_iniciales, (
            f"calls_used no debe cambiar tras reruns post-done. "
            f"Antes: {calls_iniciales}, ahora: {res_final['calls_used']}"
        )
        assert len(res_final["meta"]) == meta_inicial, (
            f"meta en batch_result no debe cambiar tras reruns post-done. "
            f"Antes: {meta_inicial}, ahora: {len(res_final['meta'])}"
        )


# ===========================================================================
# (D-extra) Cancelar: los ISRCs no procesados aparecen en not_found
#           como "no procesado (cancelado)".
#
# Al cancelar con idx=20 (2 chunks de 10 procesados con noop-rerun),
# los ISRCs isrcs[20:] -> 30 ISRCs deben aparecer en not_found.
# ===========================================================================

class TestD_CancelNotFound:

    def test_cancel_isrcs_restantes_en_not_found(self):
        """(D-extra) Los ISRCs no procesados al cancelar van a not_found con
        razon 'no procesado (cancelado)'."""
        at = _make_at(_HARNESS_CANCEL)
        # Run 1: procesa chunk 0-10
        at.run()
        assert not at.exception, f"Excepcion en run 1: {at.exception}"
        job = _ss_get(at, "batch_job")
        assert job is not None and not job["done"]
        # Click Cancelar + Run 2: procesa chunk 10-20, luego detecta cancelled -> done
        cancel_btn = next((b for b in at.button if b.key == "batch_cancel"), None)
        assert cancel_btn is not None, "batch_cancel debe existir"
        cancel_btn.click()
        at.run()
        assert not at.exception, f"Excepcion tras cancelar: {at.exception}"

        job = _ss_get(at, "batch_job")
        assert job["done"] is True
        assert job["cancelled"] is True

        # idx=20 tras 2 chunks; isrcs[20:] = 30 ISRCs pendientes
        idx_final = job["idx"]
        isrcs_pendientes = _ISRCS[idx_final:]
        not_found = job["not_found"]

        # Verificar que los ISRCs pendientes estan en not_found con razon correcta
        nf_isrcs_cancelados = [
            isrc for isrc, motivo in not_found
            if motivo == "no procesado (cancelado)"
        ]
        assert set(nf_isrcs_cancelados) == set(isrcs_pendientes), (
            f"Los ISRCs pendientes deben estar en not_found como 'no procesado (cancelado)'. "
            f"Pendientes: {len(isrcs_pendientes)}, encontrados con esa razon: {len(nf_isrcs_cancelados)}"
        )

    def test_cancel_not_found_no_duplica_procesados(self):
        """(D-extra) Los ISRCs YA procesados no aparecen en not_found como cancelados."""
        at = _make_at(_HARNESS_CANCEL)
        at.run()
        assert not at.exception
        cancel_btn = next((b for b in at.button if b.key == "batch_cancel"), None)
        cancel_btn.click()
        at.run()
        assert not at.exception

        job = _ss_get(at, "batch_job")
        assert job["done"] is True
        idx_final = job["idx"]
        isrcs_procesados = set(_ISRCS[:idx_final])
        not_found = job["not_found"]

        nf_cancelados_procesados = [
            isrc for isrc, motivo in not_found
            if motivo == "no procesado (cancelado)" and isrc in isrcs_procesados
        ]
        assert not nf_cancelados_procesados, (
            f"ISRCs ya procesados no deben aparecer como 'no procesado (cancelado)': "
            f"{nf_cancelados_procesados}"
        )


# ===========================================================================
# (F) 429 de Soundcharts en lookup_isrc_to_uuid / get_song_playlists
#     -> RuntimeError capturado -> ISRC va a not_found con mensaje de error.
#     No se traga silenciosamente (no queda como "no en Soundcharts").
# ===========================================================================

# Harness que mockea search_isrc para lanzar RuntimeError en un ISRC especifico
_ISRC_429 = "FAKE429X"
_ISRC_OK = "FAKEOK00"

_HARNESS_429 = f"""
import sys
sys.path.insert(0, {_REPO_ROOT!r})
import streamlit as st
import app

# Mock: ISRC_429 lanza RuntimeError (simula 429 propagado desde lookup_isrc_to_uuid
# o get_song_playlists), ISRC_OK se resuelve normal.
def _fake_search_isrc_429(isrc, platforms, buster=""):
    if isrc == {_ISRC_429!r}:
        raise RuntimeError("Soundcharts 429 rate-limited")
    return {{
        "meta": {{
            "uuid": isrc,
            "song_name": f"Song_{{isrc}}",
            "credit_name": "FakeArtist",
        }},
        "playlists": [{{
            "playlist_id": f"PL_{{isrc}}",
            "platform": "spotify",
            "playlist_name": f"FakePL_{{isrc}}",
            "followers": 1000,
            "position": 1,
        }}],
        "calls_used": 2,
    }}

app.search_isrc = _fake_search_isrc_429

_isrcs = [{_ISRC_429!r}, {_ISRC_OK!r}]
if "batch_job" not in st.session_state:
    st.session_state["batch_job"] = {{
        "isrcs": _isrcs,
        "platforms": ["spotify"],
        "scope": "spotify",
        "buster": "",
        "idx": 0,
        "meta": {{}},
        "playlists": [],
        "not_found": [],
        "calls_used": 0,
        "done": False,
        "cancelled": False,
    }}

app.tab_batch()
"""


class TestF_Soundcharts429:

    def test_429_isrc_va_a_not_found(self):
        """(F) Un ISRC que provoca RuntimeError('Soundcharts 429') acaba en not_found."""
        at = _make_at(_HARNESS_429)
        at.run()
        assert not at.exception, f"Excepcion en run: {at.exception}"
        job = _ss_get(at, "batch_job")
        assert job["done"] is True

        nf_isrcs = [isrc for isrc, _ in job["not_found"]]
        assert _ISRC_429 in nf_isrcs, (
            f"El ISRC con 429 debe estar en not_found. not_found={job['not_found']}"
        )

    def test_429_mensaje_error_visible(self):
        """(F) El not_found del ISRC 429 contiene informacion del error (no vacio)."""
        at = _make_at(_HARNESS_429)
        at.run()
        assert not at.exception, f"Excepcion en run: {at.exception}"
        job = _ss_get(at, "batch_job")

        motivo_429 = next(
            (motivo for isrc, motivo in job["not_found"] if isrc == _ISRC_429),
            None,
        )
        assert motivo_429 is not None, f"No encontrado {_ISRC_429} en not_found"
        assert motivo_429 != "", "El motivo del error no debe estar vacio"
        # La razon debe mencionar 'error' (el batch_search prefija con 'error: ...')
        assert "error" in motivo_429.lower(), (
            f"El motivo debe mencionar 'error', fue: {motivo_429!r}"
        )

    def test_429_no_afecta_a_isrc_ok(self):
        """(F) El ISRC sin error se procesa correctamente aunque otro lance 429."""
        at = _make_at(_HARNESS_429)
        at.run()
        assert not at.exception, f"Excepcion en run: {at.exception}"
        job = _ss_get(at, "batch_job")

        assert _ISRC_OK in job["meta"], (
            f"El ISRC sin error debe estar en meta. meta keys={list(job['meta'].keys())}"
        )
        assert _ISRC_OK not in [isrc for isrc, _ in job["not_found"]], (
            f"El ISRC sin error no debe estar en not_found"
        )

    def test_429_batch_result_refleja_not_found(self):
        """(F) batch_result.not_found incluye el ISRC con 429."""
        at = _make_at(_HARNESS_429)
        at.run()
        assert not at.exception, f"Excepcion en run: {at.exception}"
        res = _ss_get(at, "batch_result")
        assert res is not None, "batch_result debe existir"
        nf_isrcs = [isrc for isrc, _ in res["not_found"]]
        assert _ISRC_429 in nf_isrcs, (
            f"batch_result.not_found debe incluir el ISRC 429. not_found={res['not_found']}"
        )


# ===========================================================================
# (G) batch_job_msg: el mensaje de fin/cancelacion se guarda en session_state
#     y se consume (pop) una sola vez al entrar en Fase B.
#
# Verificamos:
#   G1. Tras done completo: batch_job_msg tiene level='success'.
#   G2. Tras cancelar: batch_job_msg tiene level='warning'.
#   G3. El mensaje se consume (pop) en el run de Fase B -> no persiste en runs
#       adicionales.
# ===========================================================================

# Harness para G: usa reruns reales (completa todo en 1 at.run()).
# Para G3 necesitamos poder hacer un run adicional sin que AppTest reinicie
# el job, por eso usamos el harness con noop-rerun y lo llevamos a done
# manualmente.
_HARNESS_MSG_COMPLETE = f"""
import sys
sys.path.insert(0, {_REPO_ROOT!r})
import streamlit as st
import app

def _fake_search_isrc(isrc, platforms, buster=""):
    return {{
        "meta": {{"uuid": isrc, "song_name": f"Song_{{isrc}}", "credit_name": "FA"}},
        "playlists": [{{"playlist_id": f"PL_{{isrc}}", "platform": "spotify",
                        "playlist_name": f"FakePL_{{isrc}}", "followers": 100, "position": 1}}],
        "calls_used": 1,
    }}
app.search_isrc = _fake_search_isrc

_isrcs = ["FAKE0001", "FAKE0002", "FAKE0003"]
if "batch_job" not in st.session_state:
    st.session_state["batch_job"] = {{
        "isrcs": _isrcs,
        "platforms": ["spotify"],
        "scope": "spotify",
        "buster": "",
        "idx": 0,
        "meta": {{}},
        "playlists": [],
        "not_found": [],
        "calls_used": 0,
        "done": False,
        "cancelled": False,
    }}

app.tab_batch()
"""

_HARNESS_MSG_CANCEL = f"""
import sys
sys.path.insert(0, {_REPO_ROOT!r})
import streamlit as st
import app

def _fake_search_isrc(isrc, platforms, buster=""):
    return {{
        "meta": {{"uuid": isrc, "song_name": f"Song_{{isrc}}", "credit_name": "FA"}},
        "playlists": [{{"playlist_id": f"PL_{{isrc}}", "platform": "spotify",
                        "playlist_name": f"FakePL_{{isrc}}", "followers": 100, "position": 1}}],
        "calls_used": 1,
    }}
app.search_isrc = _fake_search_isrc

# noop-rerun para controlar chunks
_rerun_calls = []
def _noop_rerun():
    _rerun_calls.append(1)
app.st.rerun = _noop_rerun

_isrcs = [f"MSGC{{i:04d}}" for i in range(30)]
if "batch_job" not in st.session_state:
    st.session_state["batch_job"] = {{
        "isrcs": _isrcs,
        "platforms": ["spotify"],
        "scope": "spotify",
        "buster": "",
        "idx": 0,
        "meta": {{}},
        "playlists": [],
        "not_found": [],
        "calls_used": 0,
        "done": False,
        "cancelled": False,
    }}

app.tab_batch()
"""


class TestG_BatchJobMsg:

    def test_msg_success_tras_completar(self):
        """(G1) Tras completar el job, batch_job_msg tiene level='success' en
        session_state antes de que Fase B lo consuma.

        Con reruns INTERNOS de AppTest (harness sin noop-rerun), la secuencia es:
          at.run() -> Fase A procesa los ISRCs -> st.rerun() interno -> Fase B
          -> pop(batch_job_msg) -> fin (sin mas reruns).
        AppTest expone el session_state del ultimo run interno, donde el pop ya
        ocurrio. Por tanto batch_job_msg puede ser None al final de at.run().

        Para verificar que el mensaje SI se genero con level='success' antes del pop,
        usamos el harness con noop-rerun: Fase A termina, hace st.rerun() (noop),
        batch_job_msg queda en session_state. El run siguiente (Fase B) lo consume.
        """
        # Usar harness con noop-rerun para poder inspeccionar batch_job_msg antes
        # de que Fase B lo consuma.
        at = _make_at(_HARNESS_MSG_CANCEL)  # noop-rerun, 30 ISRCs
        # Avanzar hasta done con noop-rerun
        for _ in range(20):  # max_runs de sobra
            at.run()
            assert not at.exception
            job = _ss_get(at, "batch_job")
            if job and job.get("done") and not job.get("cancelled"):
                break
        else:
            raise AssertionError("El job no llego a done (sin cancelar)")

        job = _ss_get(at, "batch_job")
        assert job["done"] is True and not job.get("cancelled"), (
            "Precondicion: job done y no cancelado"
        )

        # batch_job_msg debe existir con level='success' (antes de Fase B)
        msg = _ss_get(at, "batch_job_msg", None)
        assert msg is not None, (
            "batch_job_msg debe existir en session_state tras done (antes del run de Fase B)"
        )
        assert msg["level"] == "success", (
            f"batch_job_msg.level debe ser 'success' tras completar, fue {msg['level']!r}"
        )
        assert msg["text"], "batch_job_msg.text no debe estar vacio"

    def test_msg_warning_tras_cancelar(self):
        """(G2) Tras cancelar, batch_job_msg tiene level='warning'."""
        at = _make_at(_HARNESS_MSG_CANCEL)
        # Run 1: chunk 0-10
        at.run()
        assert not at.exception
        job = _ss_get(at, "batch_job")
        assert job is not None and not job["done"]

        # Cancelar
        cancel_btn = next((b for b in at.button if b.key == "batch_cancel"), None)
        assert cancel_btn is not None
        cancel_btn.click()
        at.run()
        assert not at.exception, f"Excepcion tras cancelar: {at.exception}"

        job = _ss_get(at, "batch_job")
        assert job["done"] is True
        assert job["cancelled"] is True

        # Con noop-rerun, el rerun al final de Fase A no ejecuta Fase B en el mismo
        # at.run(): batch_job_msg debe existir en session_state ahora.
        msg = _ss_get(at, "batch_job_msg", None)
        assert msg is not None, (
            "batch_job_msg debe existir en session_state tras cancelar (antes de Fase B)"
        )
        assert msg["level"] == "warning", (
            f"batch_job_msg.level debe ser 'warning' tras cancelar, fue {msg['level']!r}"
        )

    def test_msg_consumido_en_fase_b(self):
        """(G3) batch_job_msg se consume (pop) en el run de Fase B: no persiste en
        runs adicionales."""
        at = _make_at(_HARNESS_MSG_CANCEL)
        # Llevar a done con noop-rerun
        at.run()
        assert not at.exception
        cancel_btn = next((b for b in at.button if b.key == "batch_cancel"), None)
        assert cancel_btn is not None
        cancel_btn.click()
        at.run()
        assert not at.exception

        job = _ss_get(at, "batch_job")
        assert job["done"] is True

        # Verificar que batch_job_msg existe antes de Fase B
        msg_antes = _ss_get(at, "batch_job_msg", None)
        assert msg_antes is not None, "batch_job_msg debe existir antes del run de Fase B"

        # Run adicional: Fase B hace pop(batch_job_msg) -> no debe quedar
        at.run()
        assert not at.exception, f"Excepcion en run Fase B: {at.exception}"
        msg_despues = _ss_get(at, "batch_job_msg", None)
        assert msg_despues is None, (
            f"batch_job_msg debe ser None tras Fase B (fue consumido con pop), "
            f"pero quedó: {msg_despues}"
        )
