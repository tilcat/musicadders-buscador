/**
 * playlist-utils.js — Utilidades compartidas para vistas de playlists.
 *
 * Usado en: buscar/ (F2). Batch las mantiene inline por compatibilidad.
 * Si en el futuro se quiere unificar, importar desde aquí en BatchResults.jsx.
 */

/** Clasifica el tipo raw de Soundcharts en uno de los 4 tipos internos. */
export function classifyType(raw) {
  if (!raw) return "user";
  const t = raw.toLowerCase();
  if (t.includes("editorial") || t.includes("algotorial")) return "editorial";
  if (t.includes("algorithmic")) return "algorithmic";
  if (t.includes("chart")) return "charts";
  return "user";
}

/** Formatea un número en K / M con locale es. */
export function formatNumber(n) {
  if (!n && n !== 0) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000)     return `${(n / 1_000).toFixed(0)}K`;
  return n.toLocaleString("es");
}

/** Color de marca por slug de plataforma (mismo sistema que globals.css). */
export const PLAT_COLORS = {
  "spotify":     "#1a9e5c",
  "apple-music": "#fc3c44",
  "amazon":      "#e87c14",
  "deezer":      "#a066d3",
  "youtube":     "#dc2626",
  "soundcloud":  "#d44c00",
  "tidal":       "#1a1f2e",
  "audiomack":   "#f59e0b",
  "pandora":     "#005fa3",
};

/** Nombre de display por slug. */
export const PLAT_LABELS = {
  "spotify":     "Spotify",
  "apple-music": "Apple Music",
  "amazon":      "Amazon Music",
  "deezer":      "Deezer",
  "youtube":     "YouTube",
  "soundcloud":  "SoundCloud",
  "tidal":       "Tidal",
  "audiomack":   "Audiomack",
  "pandora":     "Pandora",
};

/** Etiquetas de tipo de playlist. */
export const TYPE_LABELS = {
  editorial:   "Editorial",
  algorithmic: "Algoritmo",
  charts:      "Charts",
  user:        "Usuario",
};

/** Orden canónico de plataformas (principales primero). */
export const PLAT_ORDER = [
  "spotify", "apple-music", "amazon", "deezer",
  "youtube", "soundcloud", "tidal", "audiomack", "pandora",
];
