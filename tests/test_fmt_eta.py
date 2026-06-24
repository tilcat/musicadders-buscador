"""
Regresion _fmt_eta — app.py (musicadders-buscador)
===================================================
Tests unitarios del helper _fmt_eta(segundos: float) -> str.

El helper formatea una duracion estimada de forma legible.
Logica real (post-refactor math.ceil):

    def _fmt_eta(segundos: float) -> str:
        import math
        if segundos >= 60:
            return f"~{max(1, math.ceil(segundos / 60))} min"
        return f"~{max(1, int(segundos))} s"

Casos cubiertos:
  A) 0 s       → "~1 s"   (max(1, 0) = 1)
  B) 45 s      → "~45 s"  (path s, valor directo)
  C) 59.9 s    → "~59 s"  (tope del path s, justo por debajo de 60)
  D) 60 s      → "~1 min" (ceil(60/60)=1; ceil exacto sin +1 artificial)
  E) 186 s     → "~4 min" (ceil(186/60)=ceil(3.1)=4)
  F) 3600 s    → "~60 min" (ceil(3600/60)=ceil(60)=60)
  G) float 90.5 → "~2 min" (ceil(90.5/60)=ceil(1.508)=2)
  H) 1 s       → "~1 s"  (max(1,1) = 1)

Estrategia:
  Misma tecnica que test_spotify_antipenalty.py/_load_parse_retry_after:
  se extrae _fmt_eta del source de app.py via ast + exec en un namespace
  limpio, sin importar el modulo completo (que llama a st.set_page_config
  y otros efectos globales de Streamlit al nivel de modulo).

Ejecutar:
    /Users/trabajo/dashboard-regalias/.venv/bin/python \\
        -m pytest tests/test_fmt_eta.py -v
"""
import ast
import os
import sys
import textwrap

import pytest

# ---------------------------------------------------------------------------
# Ruta al repo raiz
# ---------------------------------------------------------------------------
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# ---------------------------------------------------------------------------
# Loader: extrae _fmt_eta sin ejecutar el modulo completo
# ---------------------------------------------------------------------------

def _load_fmt_eta():
    """Devuelve la funcion _fmt_eta extraida del source de app.py
    sin importar el modulo completo."""
    app_path = os.path.join(_REPO_ROOT, "app.py")
    with open(app_path, "r") as f:
        src = f.read()

    tree = ast.parse(src)
    fn_node = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_fmt_eta"
    )
    fn_lines = src.splitlines()[fn_node.lineno - 1: fn_node.end_lineno]
    fn_src = textwrap.dedent("\n".join(fn_lines))

    ns = {}
    exec(fn_src, ns)
    return ns["_fmt_eta"]


# Cargamos una unica vez para todos los tests del modulo.
_fmt_eta = _load_fmt_eta()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFmtEta:
    """Tests unitarios de _fmt_eta(segundos: float) -> str."""

    def test_cero_segundos(self):
        """(A) 0 s → '~1 s': max(1, int(0)) = 1."""
        result = _fmt_eta(0)
        assert result == "~1 s", f"Esperado '~1 s', obtenido '{result}'"

    def test_45_segundos(self):
        """(B) 45 s → '~45 s': path rama s, valor directo."""
        result = _fmt_eta(45)
        assert result == "~45 s", f"Esperado '~45 s', obtenido '{result}'"

    def test_59_9_segundos(self):
        """(C) 59.9 s → '~59 s': tope del path s justo por debajo de 60."""
        result = _fmt_eta(59.9)
        assert result == "~59 s", f"Esperado '~59 s', obtenido '{result}'"

    def test_60_segundos(self):
        """(D) 60 s → '~1 min': ceil(60/60)=1 (sin +1 artificial; exacto)."""
        result = _fmt_eta(60)
        assert result == "~1 min", f"Esperado '~1 min', obtenido '{result}'"

    def test_186_segundos(self):
        """(E) 186 s → '~4 min': ceil(186/60)=ceil(3.1)=4."""
        result = _fmt_eta(186)
        assert result == "~4 min", f"Esperado '~4 min', obtenido '{result}'"

    def test_3600_segundos(self):
        """(F) 3600 s → '~60 min': ceil(3600/60)=ceil(60.0)=60."""
        result = _fmt_eta(3600)
        assert result == "~60 min", f"Esperado '~60 min', obtenido '{result}'"

    def test_float_90_5(self):
        """(G) 90.5 s → '~2 min': ceil(90.5/60)=ceil(1.508)=2."""
        result = _fmt_eta(90.5)
        assert result == "~2 min", f"Esperado '~2 min', obtenido '{result}'"

    def test_1_segundo(self):
        """(H) 1 s → '~1 s': max(1, 1) = 1."""
        result = _fmt_eta(1)
        assert result == "~1 s", f"Esperado '~1 s', obtenido '{result}'"

    def test_formato_resultado_tiene_tilde(self):
        """El resultado siempre comienza con '~' (formato legible)."""
        for seg in [0, 1, 30, 60, 120, 600]:
            result = _fmt_eta(seg)
            assert result.startswith("~"), (
                f"_fmt_eta({seg}) debe empezar con '~', obtenido '{result}'"
            )

    def test_formato_resultado_min_o_s(self):
        """El resultado termina en ' min' o ' s' segun el rango."""
        assert _fmt_eta(30).endswith(" s")
        assert _fmt_eta(120).endswith(" min")
