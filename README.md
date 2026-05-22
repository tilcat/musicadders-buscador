# Musicadders · Buscador de placements

App standalone para que cualquier trabajador de Musicadders pegue un ISRC
y vea en qué playlists de Spotify/Apple/Amazon/Deezer/etc. está, en tiempo real.

## Características

- **Multi-usuario** con bcrypt (lista en `secrets.toml → [users]`).
- **Modo LIVE puro**: cada búsqueda llama Soundcharts API directamente, sin BD local.
- Cache en memoria (`st.cache_data`) — mismo ISRC en la misma sesión no se re-consulta.
- **Kill-switch** diario (`SOUNDCHARTS_MAX_PER_DAY`).
- Soporta hasta **9 plataformas** Soundcharts: spotify, apple-music, amazon, deezer, youtube, soundcloud, tidal, audiomack, pandora.
- **Deduplicación** automática (Amazon devuelve la misma playlist varias veces — se agrupa).
- **Branding Musicadders**: gradient verde-cian + logo.

## Deploy en Streamlit Cloud

1. Sube este directorio a un repo GitHub (puede ser el mismo `dashboard-regalias`).
2. En https://share.streamlit.io → "New app".
3. Repo: `tilcat/dashboard-regalias`, branch `main`, **Main file path**: `apps/buscador/app.py`.
4. Custom subdomain: `musicadders-isrc` (o el que prefieras).
5. **Advanced settings → Python version**: 3.11 o 3.12.
6. **Settings → Secrets**: pega los secrets como en `secrets.toml.example`, con los hashes bcrypt reales.

## Generar hashes bcrypt para los usuarios

```bash
python -c "import bcrypt; print(bcrypt.hashpw(b'mi_password_secreto', bcrypt.gensalt()).decode())"
```

Pega el output (algo como `$2b$12$xxxxxxxxxxxx...`) como valor del usuario en `[users]`.

**Importante**: cada vez que cambies passwords o añadas usuarios, actualiza los Secrets en
Streamlit Cloud y la app se reinicia automáticamente.

## Probar en local

```bash
cd apps/buscador
cp secrets.toml.example .streamlit/secrets.toml   # rellena los valores
streamlit run app.py
```

## Coste estimado

Soundcharts plan actual = 500.000 llamadas/mes. Cada búsqueda de un ISRC consume
~10 llamadas (1 lookup ISRC→UUID + 1 por plataforma × ~9). Con 50 búsquedas/día
por persona × 10 personas → 500 búsquedas/día × 10 = 5.000 calls/día = 150k/mes.
Está holgadamente dentro de cuota. El kill-switch evita abuso.

## Limitaciones

- Apple/Deezer/Amazon: cobertura de Soundcharts más baja que Spotify. Para
  catálogos no-anglosajones es habitual ver 0 placements en esas DSPs aunque
  el track sí esté en alguna playlist Apple/Deezer (Soundcharts no las indexa
  todas).
- "Force refresh" no aplica: cada sesión Streamlit Cloud arranca con cache vacío.
- Si necesitamos cobertura Apple Music exhaustiva, hay que añadir el cliente
  `apple_music.py` con Developer Token aparte.
