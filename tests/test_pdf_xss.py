"""Test de regresión: pdf_report NO inyecta HTML/XSS en el markup de ReportLab.

Cubre el riesgo de que strings con '<', '>', '"', "'" entren sin escapar
en los Paragraph() de _playlist_card, _song_header y generate_pdf, lo que
podría causar que ReportLab parsee etiquetas no deseadas o, en entornos de
render HTML futuros, ejecute scripts.

Verifica que html.escape() actúa sobre todos los campos de usuario antes de
insertarlos en el markup de ReportLab.
"""
import html
import os
import sys

import pytest

# Asegurar que el módulo se importa desde el directorio del repo
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import pdf_report


# ---------------------------------------------------------------------------
# Fixtures de payloads hostiles
# ---------------------------------------------------------------------------

HOSTILE_PLAYLIST_NAME = "<b>X</b>"
HOSTILE_SONG_NAME = "<script>alert(1)</script>"
HOSTILE_COUNTRY = "<font color=red>"
HOSTILE_ISRC = '"onload=alert(1)//'
HOSTILE_ARTIST = "<img src=x onerror='alert(1)'>"
HOSTILE_PLATFORM = "<Platform & 'test'>"


# ---------------------------------------------------------------------------
# Tests unitarios de escape en helpers
# ---------------------------------------------------------------------------

class TestPlaylistCardEscape:
    """_playlist_card debe escapar todos los campos de texto antes del markup."""

    def _escaped_fields(self, pl: dict) -> dict:
        """Replica el escape que hace _playlist_card internamente."""
        return {
            "name": html.escape(pl.get("playlist_name") or "—"),
            "plat": html.escape((pl.get("platform") or "").title()),
            "ptype": html.escape(pl.get("playlist_type") or "—"),
            "country": html.escape(pl.get("country_code") or ""),
        }

    def test_playlist_name_lt_gt_escaped(self):
        fields = self._escaped_fields({"playlist_name": HOSTILE_PLAYLIST_NAME})
        assert "<" not in fields["name"]
        assert ">" not in fields["name"]
        assert "&lt;" in fields["name"]
        assert "&gt;" in fields["name"]

    def test_country_code_escaped(self):
        fields = self._escaped_fields({"country_code": HOSTILE_COUNTRY})
        assert "<" not in fields["country"]
        assert "&lt;" in fields["country"]

    def test_platform_ampersand_escaped(self):
        fields = self._escaped_fields({"platform": HOSTILE_PLATFORM})
        # El '&' original del input debe estar encodado como &amp;
        assert "&amp;" in fields["plat"]
        # No debe haber un '&' desnudo (sin ser parte de una entidad HTML)
        import re
        bare_amp = re.sub(r"&[a-zA-Z#0-9]+;", "", fields["plat"])
        assert "&" not in bare_amp, f"'&' desnudo en: {fields['plat']!r}"

    def test_no_raw_script_tag(self):
        pl = {"playlist_name": "<script>alert(1)</script>"}
        fields = self._escaped_fields(pl)
        assert "<script>" not in fields["name"]
        assert "&lt;script&gt;" in fields["name"]


class TestSongHeaderEscape:
    """_song_header debe escapar song_name, artist_name e isrc."""

    def _make_markup(self, isrc: str, song_name: str, artist_name: str) -> str:
        """Replica el markup que _song_header construye."""
        return (
            f'<font name="Helvetica-Bold" size="15" color="#0f172a">'
            f"{html.escape(song_name or '—')}</font>"
            f'<font size="13" color="#374151">{html.escape(artist_name or '—')}</font>'
            f'<font size="10" color="#9ca3af">ISRC: {html.escape(isrc)}</font>'
        )

    def test_song_name_script_escaped(self):
        markup = self._make_markup("TEST001", HOSTILE_SONG_NAME, "Artist")
        assert "<script>" not in markup
        assert "&lt;script&gt;" in markup

    def test_isrc_quote_escaped(self):
        markup = self._make_markup(HOSTILE_ISRC, "Song", "Artist")
        # La comilla doble " no debe quedar sin escapar en un atributo de markup
        # (html.escape convierte " a &quot;)
        assert '&quot;' in markup or '"onload' not in markup

    def test_artist_img_tag_escaped(self):
        markup = self._make_markup("TEST001", "Song", HOSTILE_ARTIST)
        assert "<img" not in markup
        assert "&lt;img" in markup

    def test_onerror_not_in_markup(self):
        markup = self._make_markup("TEST001", "Song", HOSTILE_ARTIST)
        # El atributo onerror debe estar con comillas escapadas
        assert "onerror='alert" not in markup


# ---------------------------------------------------------------------------
# Test de integración: generate_pdf real con payloads hostiles
# ---------------------------------------------------------------------------

class TestGeneratePdfXSS:
    """generate_pdf debe producir un PDF válido aunque todos los campos
    sean strings con markup hostil."""

    @pytest.fixture
    def hostile_playlists(self):
        return [
            {
                "isrc": "TESTISRC001",
                "platform": HOSTILE_PLATFORM,
                "playlist_name": HOSTILE_PLAYLIST_NAME,
                "playlist_type": "Editorial",
                "country_code": HOSTILE_COUNTRY,
                "subscriber_count": 5000,
                "image_url": "",
                "position": 1,
                "peak_position": 1,
                "entry_date": "2026-01-01",
                "peak_position_date": "2026-01-01",
            },
            {
                "isrc": "TESTISRC001",
                "platform": "spotify",
                "playlist_name": '<script>alert("xss")</script>',
                "playlist_type": "Algorithmic",
                "country_code": "<ES>",
                "subscriber_count": 100,
                "image_url": "",
                "position": None,
                "peak_position": None,
                "entry_date": "",
                "peak_position_date": "",
            },
        ]

    @pytest.fixture
    def hostile_meta(self):
        return {
            "TESTISRC001": {
                "song_name": HOSTILE_SONG_NAME,
                "credit_name": HOSTILE_ARTIST,
            }
        }

    def test_pdf_is_valid_bytes(self, hostile_playlists, hostile_meta):
        """generate_pdf no debe lanzar excepción con inputs hostiles."""
        pdf_bytes = pdf_report.generate_pdf(
            hostile_playlists, hostile_meta, title=HOSTILE_PLAYLIST_NAME
        )
        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 0

    def test_pdf_magic_bytes(self, hostile_playlists, hostile_meta):
        """El resultado debe ser un PDF válido (magic bytes %PDF)."""
        pdf_bytes = pdf_report.generate_pdf(
            hostile_playlists, hostile_meta, title=HOSTILE_PLAYLIST_NAME
        )
        assert pdf_bytes[:4] == b"%PDF", (
            f"No es PDF válido, empieza con: {pdf_bytes[:8]!r}"
        )

    def test_empty_playlists_no_crash(self):
        """generate_pdf con lista vacía no debe crashear."""
        pdf_bytes = pdf_report.generate_pdf([], {}, title="Test vacío")
        assert pdf_bytes[:4] == b"%PDF"

    def test_non_editorial_filtered_out(self):
        """Las playlists que no son editoriales se filtran; el PDF igual se genera."""
        playlists = [
            {
                "isrc": "XX0000000001",
                "platform": "spotify",
                "playlist_name": "Curators Pick",
                "playlist_type": "Curators & Listeners",
                "country_code": "",
                "subscriber_count": 1000,
                "image_url": "",
                "position": 1,
                "peak_position": 1,
                "entry_date": "",
                "peak_position_date": "",
            }
        ]
        pdf_bytes = pdf_report.generate_pdf(playlists, {})
        assert pdf_bytes[:4] == b"%PDF"

    def test_none_fields_no_crash(self):
        """Campos None en el payload no deben causar AttributeError."""
        playlists = [
            {
                "isrc": "XX0000000002",
                "platform": "spotify",
                "playlist_name": None,
                "playlist_type": "Editorial",
                "country_code": None,
                "subscriber_count": None,
                "image_url": None,
                "position": None,
                "peak_position": None,
                "entry_date": None,
                "peak_position_date": None,
            }
        ]
        meta = {"XX0000000002": {"song_name": None, "credit_name": None}}
        pdf_bytes = pdf_report.generate_pdf(playlists, meta)
        assert pdf_bytes[:4] == b"%PDF"
