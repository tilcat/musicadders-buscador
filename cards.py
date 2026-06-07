"""Construcción de HTML de cards de playlist — módulo puro sin dependencias de Streamlit.

Extraído de app.py para permitir tests unitarios sin inicializar Streamlit.
"""
from __future__ import annotations

import html


def _build_card_html(p: dict) -> str:
    """Construye el HTML de una card de playlist Soundcharts con todos los
    campos de origen externo escapados (html.escape). Función pura testeable;
    no llama a st.*"""
    t = p.get("playlist_type") or ""
    css_class = (
        "algorithmic" if "algorithmic" in t.lower() or "algotorial" in t.lower() else
        "charts" if "chart" in t.lower() else
        "user" if t == "Curators & Listeners" else ""
    )
    subs = p.get("subscriber_count")
    subs_fmt = f"{subs:,}" if isinstance(subs, int) and subs >= 1000 else (html.escape(str(subs)) if subs is not None else "—")
    pos = p.get("position") if p.get("position") is not None else "—"
    countries = p.get("country_code") or ""
    try:
        _nv = int(p.get("n_variantes") or 1)
    except (TypeError, ValueError):
        _nv = 1
    variantes = f" · {_nv} variantes" if _nv > 1 else ""
    entry = (p.get("entry_date") or "")[:10]
    meta_line = (
        f"{html.escape(t)} · pos #{html.escape(str(pos))} · {subs_fmt} subs · {html.escape(countries) or 'global'}"
        f"{variantes}"
        f"{' · entró ' + html.escape(entry) if entry else ''}"
    )
    return (
        f"<div class='ma-pl-card {css_class}'>"
        f"<div class='pl-name'>{html.escape(p.get('playlist_name') or '?')}</div>"
        f"<div class='pl-meta'>{meta_line}</div>"
        f"</div>"
    )
