"use client";

/**
 * PlaylistResult — Resultado de la creación de la playlist.
 *
 * Estructura:
 *   1. Banner (verde si done, ámbar si cancelled) + botón "Nueva playlist".
 *   2. Enlace Spotify: pill verde (.spotify-playlist-link) si done;
 *      btn-secondary si cancelled (resultado parcial).
 *   3. Tarjetas KPI: tracks añadidos / no encontrados / total ISRCs / errores.
 *   4. Si hay ISRCs no encontrados: descarga CSV + preview de los primeros 5.
 *
 * Props:
 *   estado      {"done"|"cancelled"}
 *   result      {{ playlist_url, playlist_name, tracks_added,
 *                  not_found_isrcs, total_isrcs, errors_count } | null}
 *   downloadUrl {(fmt: string) => string|null}
 *   onReset     {() => void}
 */

import PlaylistKpiCard from "@/components/playlist/PlaylistKpiCard";

// ── Iconos ────────────────────────────────────────────────────────────────────

function IconExternalLink() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
      <polyline points="15 3 21 3 21 9" />
      <line x1="10" y1="14" x2="21" y2="3" />
    </svg>
  );
}

function IconDownload({ size = 13 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none"
      stroke="currentColor" strokeWidth="1.75" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <path d="M8 2v8M5 7l3 3 3-3" />
      <path d="M2 12h12" />
    </svg>
  );
}

function IconRefresh({ size = 12 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <polyline points="1 4 1 10 7 10" />
      <path d="M3.51 15a9 9 0 1 0 .49-4.5" />
    </svg>
  );
}

function IconMusic() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <path d="M9 18V5l12-2v13" />
      <circle cx="6" cy="18" r="3" />
      <circle cx="18" cy="16" r="3" />
    </svg>
  );
}

// KpiCard eliminado — se usa PlaylistKpiCard compartido (Fix 17)

// ── Componente principal ──────────────────────────────────────────────────────

export default function PlaylistResult({ estado, result, downloadUrl, onReset }) {
  const isCancelled  = estado === "cancelled";
  const playlistUrl  = result?.playlist_url    ?? null;
  const playlistName = result?.playlist_name   ?? null;
  const tracksAdded  = result?.tracks_added    ?? 0;
  const notFoundList = result?.not_found_isrcs ?? [];
  const totalIsrcs   = result?.total_isrcs     ?? 0;
  const errorsCount  = result?.errors_count    ?? 0;
  const hasNotFound  = notFoundList.length > 0;

  function handleDownloadNotFound() {
    const url = downloadUrl("not_found_csv");
    if (!url) return;
    const a = document.createElement("a");
    a.href     = url;
    a.download = "isrcs_no_encontrados.csv";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  return (
    <div className="flex flex-col gap-6 animate-reveal">

      {/* ── 1. Banner de estado ──────────────────────────────────────────── */}
      <div
        className="flex flex-col gap-4 px-5 py-4 rounded-xl"
        style={{
          background: isCancelled ? "var(--color-warning-bg)" : "var(--color-accent-bg)",
          border:     `1px solid ${isCancelled ? "var(--color-warning-border)" : "var(--color-success-border)"}`,
        }}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <p
              className="text-sm font-semibold"
              style={{ color: isCancelled ? "var(--color-warning-text)" : "var(--color-accent-hover)" }}
            >
              {isCancelled
                ? `Proceso cancelado — ${tracksAdded.toLocaleString("es")} track${tracksAdded !== 1 ? "s" : ""} añadido${tracksAdded !== 1 ? "s" : ""} hasta el momento`
                : `Playlist creada · ${tracksAdded.toLocaleString("es")} track${tracksAdded !== 1 ? "s" : ""} añadido${tracksAdded !== 1 ? "s" : ""}`}
            </p>
            {!isCancelled && playlistName && (
              <p className="text-xs mt-0.5" style={{ color: "var(--color-text-soft)" }}>
                {playlistName}
              </p>
            )}
          </div>
          <button
            type="button"
            onClick={onReset}
            className="btn btn-secondary flex items-center gap-1.5 text-xs"
            style={{ flexShrink: 0 }}
          >
            <IconRefresh /> Nueva playlist
          </button>
        </div>

        {/* Enlace a la playlist:
            - done     → pill verde (.spotify-playlist-link)
            - cancelled → btn-secondary (resultado parcial, no celebración) */}
        {playlistUrl && !isCancelled && (
          <a
            href={playlistUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="spotify-playlist-link"
          >
            <IconMusic />
            Abrir en Spotify
            <IconExternalLink />
          </a>
        )}
        {playlistUrl && isCancelled && (
          <a
            href={playlistUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="btn btn-secondary flex items-center gap-1.5"
            style={{ fontSize: "12px" }}
          >
            <IconMusic />
            Ver playlist parcial en Spotify
            <IconExternalLink />
          </a>
        )}
      </div>

      {/* ── 2. KPIs ─────────────────────────────────────────────────────── */}
      <div className="flex gap-3 flex-wrap animate-reveal animate-reveal-delay-1">
        <PlaylistKpiCard label="Tracks añadidos"  value={tracksAdded}        highlight />
        <PlaylistKpiCard label="No encontrados"   value={notFoundList.length} warn={hasNotFound} />
        <PlaylistKpiCard label="Total ISRCs"      value={totalIsrcs} />
        <PlaylistKpiCard label="Errores"          value={errorsCount}         warn={errorsCount > 0} />
      </div>

      {/* ── 3. ISRCs no encontrados ──────────────────────────────────────── */}
      {hasNotFound && (
        <div
          className="flex flex-col gap-3 p-4 rounded-xl animate-reveal animate-reveal-delay-2"
          style={{
            background: "var(--color-surface)",
            border:     "1px solid var(--color-border)",
            boxShadow:  "var(--shadow-sm)",
          }}
        >
          <div className="flex items-center justify-between gap-4 flex-wrap">
            <p className="text-sm font-medium" style={{ color: "var(--color-text)" }}>
              {notFoundList.length.toLocaleString("es")} ISRC{notFoundList.length !== 1 ? "s" : ""} sin URI en Spotify
            </p>
            <button
              type="button"
              onClick={handleDownloadNotFound}
              className="btn btn-secondary flex items-center gap-1.5"
              style={{ fontSize: "12px" }}
            >
              <IconDownload size={13} /> Descargar CSV
            </button>
          </div>

          {/* Preview: primeros 5 */}
          <div
            className="flex flex-col gap-1 p-3 rounded-lg"
            style={{
              background: "var(--color-surface-raised)",
              border:     "1px solid var(--color-border)",
            }}
          >
            {notFoundList.slice(0, 5).map((isrc) => (
              <span
                key={isrc}
                style={{
                  fontFamily:    "var(--font-mono)",
                  fontSize:      "12px",
                  color:         "var(--color-text-soft)",
                  letterSpacing: "0.03em",
                }}
              >
                {isrc}
              </span>
            ))}
            {notFoundList.length > 5 && (
              <span className="text-xs" style={{ color: "var(--color-text-muted)" }}>
                … y {(notFoundList.length - 5).toLocaleString("es")} más. Descarga el CSV para la lista completa.
              </span>
            )}
          </div>

          <p className="text-xs" style={{ color: "var(--color-text-muted)" }}>
            Estos ISRCs no tienen URI de Spotify disponible o no están en el catálogo. Puedes
            subir el CSV al buscador de ISRC para investigarlos.
          </p>
        </div>
      )}
    </div>
  );
}
