# Musicadders · Buscador de placements

App standalone para que el equipo de Musicadders consulte un ISRC y vea en qué
playlists está, en todas las DSPs que cubre Soundcharts.

**URL pública:** https://musicadders-isrc.streamlit.app

**Acceso:** email + password por usuario (gestionado por Víctor).

---

## ¿Qué hace?

- Pegas un ISRC válido (ej. `ES14H2600001`).
- Eliges plataformas a consultar:
  - **Importantes (4)**: spotify + apple-music + amazon + deezer.
  - **Todas (9)**: añade youtube + soundcloud + tidal + audiomack + pandora.
- En ~3 segundos te muestra:
  - Metadata del track (nombre, artista, fecha de release).
  - KPIs: total playlists, oficiales/algorítmicas, user-curated, DSPs con datos.
  - Lista por plataforma de cada playlist con nombre, tipo, posición, subscribers, países, fecha de entrada.
- Botón **🔄 Refrescar** ignora la cache y vuelve a consultar Soundcharts ahora mismo.

## Categorías que cuentan como "Oficial / Algorítmica"

| Tipo Soundcharts | Cuenta | Ejemplo |
|---|---|---|
| Editorial | ✅ | "Novedades Flamenco", "Rap Español" |
| Editorial Personalized "Algotorial" | ✅ | "Rock Callejero" |
| Algorithmic | ✅ | "Daily Mix", "Discover Weekly" |
| Charts | ✅ | "Top 50 España", "Viral 50" |
| **This is...** | ❌ | "This is Bad Bunny" |
| **Major label** | ❌ | "Filtr España", "Digster" |
| Radios | ❌ | Auto-generadas página artista |
| Curators & Listeners | ❌ | User-created (ruido) |

## Limitaciones de cobertura por DSP

- **Spotify**: alta cobertura, dato fiable.
- **Apple Music**: cobertura media-baja, sobre todo editorial mainstream.
- **Amazon**: cobertura media, sobre todo editorial.
- **Deezer / Tidal / Pandora**: cobertura muy baja para tracks no-anglosajones.

Si un track aparece en Apple/Amazon manualmente pero Soundcharts devuelve 0, es
porque su crawler no ha indexado todavía esa playlist. Re-procesa después de 24-48h.

---

## Operación

### Cómo añadir un usuario nuevo

1. Decide email + password que le vas a dar.
2. Genera el hash bcrypt del password (ejecuta UNA vez en tu Mac):
   ```bash
   cd ~/dashboard-regalias
   .venv/bin/python -c "import bcrypt; print(bcrypt.hashpw(b'PASSWORD_REAL', bcrypt.gensalt()).decode())"
   ```
3. Copia el hash (algo tipo `$2b$12$xxxx...`).
4. En https://share.streamlit.io → tu app `musicadders-isrc` → ⋮ → Settings → Secrets, añade:
   ```toml
   [users]
   "nuevo.usuario@musicadders.com" = "$2b$12$el_hash_completo"
   ```
5. Save. La app reinicia automáticamente (~30 seg).
6. Mándale al user su email + password por canal privado (NUNCA chat público).

### Cómo cambiar el password de alguien

- Generas hash nuevo (mismo comando) con el nuevo password.
- En Secrets, reemplazas el hash viejo manteniendo el email.
- Save → al siguiente login, el user usa el nuevo password.

### Cómo eliminar un usuario

- En Secrets, borra la línea de su email (o coméntala con `#`).
- Save. No podrá entrar.

### Cómo cambiar el límite diario de llamadas

- En Secrets, ajusta:
  ```toml
  SOUNDCHARTS_MAX_PER_DAY = "10000"
  ```
- Save.

### Cómo desplegar cambios de código

1. Edita los ficheros en `~/musicadders-buscador/`.
2. `git add . && git commit -m "..." && git push origin main`.
3. Streamlit Cloud detecta el push y redeploya en ~2 min.
4. Si hay error de build → Streamlit Cloud → Manage app → Logs.

### Ver logs / debugging

- Streamlit Cloud → tu app → **Manage app** (botón abajo derecha en la app, o ⋮ en share.streamlit.io).
- Pestaña **Logs**: muestra stdout/stderr en vivo. Útil para ver errores Soundcharts (rate limit, 401, etc).

---

## Coste

Plan Soundcharts contratado: **500.000 llamadas/mes por 250€/mes** = 0,0005 €/call.

| Acción | Llamadas |
|---|---|
| 1 búsqueda "Importantes" | ~5 |
| 1 búsqueda "Todas" | ~10 |
| Misma búsqueda <1h | 0 (cache) |
| Refresh manual | re-cuenta |

Con 10 usuarios × 50 búsquedas/día = **~150k calls/mes** (dentro de cuota holgada).

Kill-switch diario configurable en Secrets (`SOUNDCHARTS_MAX_PER_DAY`).

---

## Arquitectura técnica

- **Streamlit Cloud free tier** + repo público GitHub.
- **No usa base de datos** persistente — modo LIVE puro contra Soundcharts.
- **Cache en memoria** (`st.cache_data` TTL 1h) compartida entre usuarios mientras la app no se reinicia.
- **Auth** con bcrypt + `st.session_state` (no es JWT, no es Cookie persistente — el user re-loguea si refresca después de tiempo de inactividad).
- **Branding** Musicadders: gradient verde-cian + logo.
- **Deduplicación** automática de playlists (Amazon devuelve la misma playlist con varios UUID por país — se agrupan).

---

## Probar en local

```bash
cd ~/musicadders-buscador
cp secrets.toml.example .streamlit/secrets.toml   # rellena con valores reales
streamlit run app.py
# Abre http://localhost:8501
```
