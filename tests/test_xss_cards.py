"""
Regresión XSS — _build_card_html (cards.py)
======================================================
Importa la función REAL de producción desde cards.py para comprobar que los
payloads hostiles quedan escapados y que ningún caso edge dispara
TypeError / AttributeError.

Ejecutar:
    python3 -m pytest tests/test_xss_cards.py -v
"""
import re

import pytest

from cards import _build_card_html


# ---------------------------------------------------------------------------
# Casos hostiles (XSS)
# ---------------------------------------------------------------------------

XSS_CASES = [
    {
        "id": "xss_playlist_name_script",
        "p": {
            "playlist_name": "<script>alert(1)</script>",
            "playlist_type": "editorial",
            "position": 1,
            "subscriber_count": 5000,
            "country_code": "ES",
        },
    },
    {
        "id": "xss_country_img_onerror",
        "p": {
            "playlist_name": "Safe Name",
            "playlist_type": "editorial",
            "position": 2,
            "subscriber_count": 100,
            "country_code": '"><img src=x onerror=alert(1)>',
        },
    },
    {
        "id": "xss_playlist_type",
        "p": {
            "playlist_name": "Safe",
            "playlist_type": '<b onmouseover=alert(2)>type</b>',
            "position": 3,
            "subscriber_count": None,
            "country_code": "US",
        },
    },
    {
        "id": "xss_entry_date",
        "p": {
            "playlist_name": "Safe",
            "playlist_type": "algorithmic",
            "position": 4,
            "subscriber_count": 0,
            "country_code": "FR",
            "entry_date": '"><svg onload=alert(3)>',
        },
    },
    {
        "id": "xss_position_str",
        "p": {
            "playlist_name": "Safe",
            "playlist_type": "",
            "position": '"><script>alert(4)</script>',
            "subscriber_count": 1,
            "country_code": "DE",
        },
    },
    # Caso nuevo (a): subscriber_count como string hostil
    # Distingue iteración 1 (sin isinstance → formatea sin escape) de iteración 2
    # (isinstance falla → html.escape aplicado). Debe FALLAR con lógica vieja.
    {
        "id": "xss_subscriber_count_string_hostile",
        "p": {
            "playlist_name": "Safe",
            "playlist_type": "editorial",
            "position": 1,
            "subscriber_count": '"><script>alert(1)</script>',
            "country_code": "ES",
        },
    },
]


@pytest.mark.parametrize("case", XSS_CASES, ids=[c["id"] for c in XSS_CASES])
def test_xss_payload_escapeado(case):
    """
    El HTML generado no debe contener elementos HTML ejecutables sin escapar.

    La condición correcta NO es "la cadena 'onerror=' no aparece" sino
    "no hay ningún elemento HTML formado con atributos de evento activos".
    Un elemento HTML está formado cuando hay un '<' real (no &lt;) seguido
    del nombre del tag.  Si todos los '<' están escapados a '&lt;', el
    browser renderiza el contenido como texto plano — no es ejecutable.
    """
    full_html = _build_card_html(case["p"])

    # Ningún tag <script> sin escapar (< real + 'script')
    assert "<script>" not in full_html, (
        f"[{case['id']}] <script> sin escapar en: {full_html}"
    )
    assert "<SCRIPT>" not in full_html.upper(), (
        f"[{case['id']}] <SCRIPT> (case-insensitive) sin escapar en: {full_html}"
    )

    # Ningún elemento <img real (< real) — formaría un tag ejecutable
    assert "<img" not in full_html.lower(), (
        f"[{case['id']}] <img sin escapar (elemento ejecutable) en: {full_html}"
    )

    # Ningún elemento <svg real
    assert "<svg" not in full_html.lower(), (
        f"[{case['id']}] <svg sin escapar en: {full_html}"
    )

    # Verificación positiva: los '<' de los payloads deben estar como &lt;
    dangerous_tags = re.compile(r'<(script|img|svg|iframe|object|embed)\b', re.IGNORECASE)
    assert not dangerous_tags.search(full_html), (
        f"[{case['id']}] tag peligroso sin escapar en: {full_html}"
    )


# ---------------------------------------------------------------------------
# Casos legítimos (deben preservar el texto visible)
# ---------------------------------------------------------------------------

LEGIT_CASES = [
    {
        "id": "legit_rock_and_roll",
        "p": {
            "playlist_name": "Rock & Roll",
            "playlist_type": "editorial",
            "position": 1,
            "subscriber_count": 12000,
            "country_code": "US",
        },
        "name_contains": "Rock",   # & se escapa a &amp; pero "Rock" sigue visible
    },
    {
        "id": "legit_cafe_emoji",
        "p": {
            "playlist_name": "Café <3",
            "playlist_type": "algorithmic",
            "position": 5,
            "subscriber_count": 500,
            "country_code": "ES",
        },
        "name_contains": "Caf",
    },
    {
        "id": "legit_tildes",
        "p": {
            "playlist_name": "Música española 🎵",
            "playlist_type": "charts",
            "position": 10,
            "subscriber_count": 1000000,
            "country_code": "ES",
        },
        "name_contains": "M",
    },
]


@pytest.mark.parametrize("case", LEGIT_CASES, ids=[c["id"] for c in LEGIT_CASES])
def test_legit_data_no_exception(case):
    """Los datos legítimos no deben lanzar excepción."""
    full_html = _build_card_html(case["p"])
    # El HTML debe estar presente
    assert "<div class='ma-pl-card" in full_html
    assert "<div class='pl-name'>" in full_html
    # El texto del nombre debe aparecer (quizás escapado) — al menos el prefijo
    assert case["name_contains"] in full_html


# ---------------------------------------------------------------------------
# Casos edge (None, ints, strings vacíos)
# ---------------------------------------------------------------------------

EDGE_CASES = [
    {
        "id": "edge_playlist_name_none",
        "p": {
            "playlist_name": None,   # debe quedar "?"
            "playlist_type": None,
            "position": None,
            "subscriber_count": None,
            "country_code": None,
        },
    },
    {
        "id": "edge_position_int_zero",
        "p": {
            "playlist_name": "Test",
            "playlist_type": "editorial",
            "position": 0,
            "subscriber_count": 0,
            "country_code": "",
        },
    },
    {
        "id": "edge_entry_date_empty",
        "p": {
            "playlist_name": "Test",
            "playlist_type": "editorial",
            "position": 1,
            "subscriber_count": 999,
            "country_code": "IT",
            "entry_date": "",        # no debe aparecer " · entró "
        },
    },
    {
        "id": "edge_position_none",
        "p": {
            "playlist_name": "Test",
            "playlist_type": "editorial",
            "position": None,        # debe quedar "—"
            "subscriber_count": 500,
            "country_code": "MX",
        },
    },
    {
        "id": "edge_n_variantes_gt1",
        "p": {
            "playlist_name": "Test",
            "playlist_type": "editorial",
            "position": 2,
            "subscriber_count": 2000,
            "country_code": "AR",
            "n_variantes": 3,
        },
    },
]


@pytest.mark.parametrize("case", EDGE_CASES, ids=[c["id"] for c in EDGE_CASES])
def test_edge_no_exception(case):
    """Ningún caso edge debe lanzar TypeError/AttributeError."""
    full_html = _build_card_html(case["p"])
    assert isinstance(full_html, str)
    assert len(full_html) > 0


def test_edge_name_none_renders_question_mark():
    """Cuando playlist_name es None, la card muestra '?'."""
    p = {
        "playlist_name": None,
        "playlist_type": "editorial",
        "position": 1,
        "subscriber_count": 100,
        "country_code": "ES",
    }
    full_html = _build_card_html(p)
    assert "<div class='pl-name'>?</div>" in full_html, (
        f"Esperado '?' en pl-name, obtenido: {full_html!r}"
    )


def test_edge_position_none_renders_dash():
    """Cuando position es None, la card muestra pos #—."""
    p = {
        "playlist_name": "Test",
        "playlist_type": "editorial",
        "position": None,
        "subscriber_count": 100,
        "country_code": "ES",
    }
    full_html = _build_card_html(p)
    assert "pos #—" in full_html, f"Esperado 'pos #—' en: {full_html!r}"


def test_xss_script_escapes_to_entities():
    """Verifica que <script>alert(1)</script> se convierte en entidades HTML."""
    p = {
        "playlist_name": "<script>alert(1)</script>",
        "playlist_type": "editorial",
        "position": 1,
        "subscriber_count": 100,
        "country_code": "ES",
    }
    full_html = _build_card_html(p)
    assert "&lt;script&gt;" in full_html, (
        f"Esperado &lt;script&gt; en: {full_html!r}"
    )
    assert "<script>" not in full_html


# ---------------------------------------------------------------------------
# Casos nuevos que distinguen iteración 1 de iteración 2
# Deben FALLAR con la lógica vieja y PASAR con la lógica actual (iteración 2)
# ---------------------------------------------------------------------------

def test_new_subscriber_count_string_hostile_is_escaped():
    """
    Caso (a): subscriber_count como string hostil.

    Iteración 1: `subs and subs >= 1000` → TypeError con string;
    en la rama else hacía `str(subs)` sin html.escape → XSS posible.
    Iteración 2: isinstance(subs, int) falla → html.escape(str(subs)) aplicado.

    El output NO debe contener <script> sin escapar.
    """
    p = {
        "playlist_name": "Safe",
        "playlist_type": "editorial",
        "position": 1,
        "subscriber_count": '"><script>alert(1)</script>',
        "country_code": "ES",
    }
    full_html = _build_card_html(p)
    assert "<script>" not in full_html, (
        f"subscriber_count string hostil no fue escapado: {full_html!r}"
    )
    assert "&lt;script&gt;" in full_html, (
        f"Se esperaba &lt;script&gt; en el output escapado: {full_html!r}"
    )


def test_new_position_zero_renders_pos_zero():
    """
    Caso (b): position = 0.

    Iteración 1: `p.get('position') or '—'` → 0 es falsy → renderiza 'pos #—' (BUG).
    Iteración 2: `p.get('position') if p.get('position') is not None else '—'`
                 → 0 is not None → renderiza 'pos #0' (CORRECTO).
    """
    p = {
        "playlist_name": "Test",
        "playlist_type": "editorial",
        "position": 0,
        "subscriber_count": 100,
        "country_code": "ES",
    }
    full_html = _build_card_html(p)
    assert "pos #0" in full_html, (
        f"position=0 debe renderizar 'pos #0', obtenido: {full_html!r}"
    )
    assert "pos #—" not in full_html, (
        f"position=0 no debe renderizar 'pos #—' (bug de iteración 1): {full_html!r}"
    )
