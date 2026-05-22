"""Generador de PDF — placement report Musicadders.

Portado/adaptado del playlists_report.py del dashboard interno.
Diferencias clave:
- Lee el logo desde assets/logo_negro.png (incluido en el repo).
- No guarda en disco — solo devuelve bytes (Streamlit Cloud no tiene volumen).
- Filtro de tipos centralizado con misma lógica que la app principal:
  Editorial + Editorial Personalized 'Algotorial' + Algorithmic + Charts.
"""
from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)
from reportlab.platypus.flowables import HRFlowable

ASSETS_DIR = Path(__file__).parent / "assets"
LOGO_PATH = ASSETS_DIR / "logo_negro.png"


def _is_editorial(t: str) -> bool:
    """Misma definición que app.py: Editorial + Algotorial + Algorithmic + Charts."""
    if not t:
        return False
    tl = str(t).lower()
    if ("curators" in tl or tl.strip() == "radios"
        or "this is" in tl or "major" in tl):
        return False
    return (
        "editorial" in tl or "algotorial" in tl
        or "chart" in tl or "algorithmic" in tl
    )


_styles = getSampleStyleSheet()


def _make_styles():
    base = _styles["BodyText"].clone("Body")
    base.fontName = "Helvetica"
    base.fontSize = 12
    base.leading = 16
    base.textColor = colors.HexColor("#1f2937")

    return {
        "body": base,
        "title": ParagraphStyle("Title", parent=base, fontName="Helvetica-Bold",
                                  fontSize=20, leading=24,
                                  textColor=colors.HexColor("#0f172a"),
                                  alignment=1, spaceAfter=6),
        "subtitle": ParagraphStyle("Subtitle", parent=base, fontSize=12, leading=15,
                                     textColor=colors.HexColor("#64748b"),
                                     alignment=1, spaceAfter=12),
        "section": ParagraphStyle("Section", parent=base, fontName="Helvetica-Bold",
                                    fontSize=16, leading=20,
                                    textColor=colors.HexColor("#0f172a"),
                                    spaceBefore=14, spaceAfter=8),
        "meta": ParagraphStyle("Meta", parent=base, fontSize=11, leading=15,
                                 textColor=colors.HexColor("#374151")),
    }


def _download_image(url: str, max_bytes: int = 2_000_000) -> bytes | None:
    if not url:
        return None
    try:
        r = requests.get(url, timeout=10, stream=True)
        if r.status_code != 200:
            return None
        data = r.content
        if len(data) > max_bytes:
            return None
        return data
    except Exception:
        return None


def _logo_block():
    if not LOGO_PATH.exists():
        return Spacer(0, 0)
    try:
        from PIL import Image as PILImage
        with PILImage.open(LOGO_PATH) as im:
            w, h = im.size
        target_w = 5 * cm
        ratio = target_w / w
        target_h = h * ratio
    except Exception:
        target_w, target_h = 5 * cm, 5 * cm
    img = Image(str(LOGO_PATH), width=target_w, height=target_h)
    img.hAlign = "CENTER"
    return img


def _playlist_card(pl: dict, styles: dict):
    img_data = _download_image(pl.get("image_url") or "")
    if img_data:
        try:
            img = Image(io.BytesIO(img_data), width=3 * cm, height=3 * cm)
        except Exception:
            img = Paragraph("(sin portada)", styles["meta"])
    else:
        img = Paragraph("(sin portada)", styles["meta"])

    name = pl.get("playlist_name") or "—"
    plat = (pl.get("platform") or "").title()
    ptype = pl.get("playlist_type") or "—"
    country = pl.get("country_code") or ""
    subs = pl.get("subscriber_count") or 0
    pos = pl.get("position")
    peak = pl.get("peak_position")
    entry = (pl.get("entry_date") or "")[:10]
    peak_d = (pl.get("peak_position_date") or "")[:10]

    type_color = {
        "Editorial": "#1ED760",
        "Algorithmic": "#A855F7",
        "Charts": "#F59E0B",
    }.get(ptype, "#06B6D4" if "algotorial" in ptype.lower() else "#6B7280")

    country_html = f' · <font size="11">{country}</font>' if country else ""
    peak_html = f" <font size='10' color='#9ca3af'>({peak_d})</font>" if peak_d else ""
    subs_str = f"{int(subs):,}" if subs else "—"
    info_html = (
        f'<font name="Helvetica-Bold" size="13" color="#0f172a">{name}</font><br/>'
        f'<font size="11" color="#6b7280">{plat}</font> · '
        f'<font size="11" color="{type_color}"><b>{ptype}</b></font>{country_html}'
        f'<br/><br/>'
        f'<font size="12" color="#374151"><b>{subs_str}</b> subscribers</font><br/>'
        f'<font size="12" color="#374151">Posición: <b>{pos or "—"}</b>'
        f' · Mejor: <b>{peak or "—"}</b>{peak_html}</font><br/>'
        f'<font size="11" color="#6b7280">Entró: {entry or "—"}</font>'
    )
    info = Paragraph(info_html, styles["meta"])
    tbl = Table([[img, info]], colWidths=[3.2 * cm, None])
    tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return KeepTogether([
        tbl,
        HRFlowable(width="100%", thickness=0.3, color=colors.HexColor("#e5e7eb"),
                   spaceBefore=3, spaceAfter=3),
    ])


def _song_header(isrc: str, song_name: str, artist_name: str, styles: dict):
    html = (
        f'<font name="Helvetica-Bold" size="15" color="#0f172a">{song_name or "—"}</font>'
        f' &nbsp;<font size="12" color="#374151">·</font> '
        f'<font size="13" color="#374151">{artist_name or "—"}</font><br/>'
        f'<font size="10" color="#9ca3af">ISRC: {isrc}</font>'
    )
    return KeepTogether([
        Spacer(1, 8),
        Paragraph(html, styles["meta"]),
        HRFlowable(width="100%", thickness=1, color=colors.HexColor("#1ED760"),
                   spaceBefore=2, spaceAfter=6),
    ])


def _header_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 9)
    canvas.setFillColor(colors.HexColor("#9ca3af"))
    canvas.drawCentredString(A4[0] / 2, 1 * cm,
                              f"Musicadders · Placement Report · página {doc.page}")
    canvas.restoreState()


def generate_pdf(playlists: list[dict], meta_by_isrc: dict[str, dict],
                 title: str = "Placement Report — Editoriales") -> bytes:
    """Genera el PDF agrupado por canción.

    playlists: lista de dicts con keys isrc, platform, playlist_name, playlist_type,
               country_code, subscriber_count, image_url, position, peak_position,
               entry_date, peak_position_date.
    meta_by_isrc: {isrc: {song_name, credit_name|artist_name, ...}}
    """
    # Filtrar solo editoriales con helper
    editorials = [p for p in playlists if _is_editorial(p.get("playlist_type") or "")]

    styles = _make_styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm,
        topMargin=1.5 * cm, bottomMargin=1.5 * cm,
        title=title,
    )

    story = []
    story.append(_logo_block())
    story.append(Spacer(1, 8))
    story.append(Paragraph(title, styles["title"]))

    n_songs = len({p["isrc"] for p in editorials if p.get("isrc")})
    story.append(Paragraph(
        f"Generado el {datetime.now().strftime('%d/%m/%Y %H:%M')} · "
        f"{len(editorials)} placements editoriales en {n_songs} canciones",
        styles["subtitle"],
    ))
    story.append(HRFlowable(width="100%", thickness=1,
                              color=colors.HexColor("#1ED760"),
                              spaceBefore=4, spaceAfter=4))

    if not editorials:
        story.append(Paragraph(
            "No se encontraron playlists editoriales en este lote de ISRCs.",
            styles["meta"],
        ))
    else:
        # Agrupar por ISRC y ordenar por nº placements
        by_isrc: dict[str, list[dict]] = {}
        for p in editorials:
            by_isrc.setdefault(p.get("isrc") or "?", []).append(p)
        isrc_order = sorted(by_isrc.keys(), key=lambda k: -len(by_isrc[k]))

        for isrc in isrc_order:
            meta = meta_by_isrc.get(isrc) or {}
            song_name = meta.get("song_name") or "—"
            artist = meta.get("credit_name") or meta.get("artist_name") or "—"
            story.append(_song_header(isrc, song_name, artist, styles))
            # Dentro del ISRC, ordenar por subscribers desc
            song_pls = sorted(by_isrc[isrc],
                              key=lambda p: -(p.get("subscriber_count") or 0))
            for p in song_pls:
                story.append(_playlist_card(p, styles))

    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    return buf.getvalue()
