"use client";

/**
 * PlaylistList — Lista de playlists agrupada por plataforma DSP.
 *
 * Cada grupo muestra:
 *   [dot color] NombreDSP · N playlists   ← pl-section-header
 *   PlaylistCard × N                       ← una por playlist, orden desc subs
 *
 * Props:
 *   playlists  {Array}   — playlists ya filtradas
 *   allCount   {number}  — total sin filtros (para mostrar "X de Y")
 *   className  {string}
 */

import PlaylistCard from "./PlaylistCard";
import { PLAT_COLORS, PLAT_LABELS, PLAT_ORDER } from "@/lib/playlist-utils";

// ── Sección de plataforma ─────────────────────────────────────────────────────

function PlatformSection({ platform, playlists }) {
  const color = PLAT_COLORS[platform] ?? "#9ba3af";
  const label = PLAT_LABELS[platform] ?? platform;
  const count = playlists.length;

  return (
    <section aria-label={`${label} — ${count} playlists`}>
      {/* Header de sección */}
      <div className="pl-section-header">
        <span
          aria-hidden="true"
          style={{
            display: "inline-block",
            width: 8,
            height: 8,
            borderRadius: "50%",
            background: color,
            flexShrink: 0,
          }}
        />
        <span style={{ color }}>
          {label}
        </span>
        <span
          style={{
            color: "var(--color-text-muted)",
            fontWeight: 400,
            textTransform: "none",
            letterSpacing: 0,
          }}
        >
          · {count} playlist{count !== 1 ? "s" : ""}
        </span>
      </div>

      {/* Tarjetas */}
      <div
        className="flex flex-col gap-1.5"
        role="list"
        aria-label={`Playlists de ${label}`}
      >
        {playlists.map((pl, i) => (
          <PlaylistCard
            key={pl.playlist_uuid ?? `${pl.playlist_name ?? "pl"}-${i}`}
            playlist={pl}
          />
        ))}
      </div>
    </section>
  );
}

// ── Componente principal ──────────────────────────────────────────────────────

export default function PlaylistList({ playlists = [], allCount, className = "" }) {
  // Agrupar por plataforma
  const byPlat = {};
  for (const pl of playlists) {
    const key = pl.platform ?? "unknown";
    if (!byPlat[key]) byPlat[key] = [];
    byPlat[key].push(pl);
  }

  // Ordenar dentro de cada grupo: subs desc
  for (const key of Object.keys(byPlat)) {
    byPlat[key].sort(
      (a, b) => (b.subscriber_count ?? 0) - (a.subscriber_count ?? 0)
    );
  }

  // Orden canónico de plataformas (principales primero, resto al final)
  const orderedPlats = [
    ...PLAT_ORDER.filter((p) => byPlat[p]),
    ...Object.keys(byPlat).filter((p) => !PLAT_ORDER.includes(p)),
  ];

  const shown   = playlists.length;
  const total   = allCount ?? shown;
  const filtered = shown < total;

  if (shown === 0) return null;

  return (
    <div className={`flex flex-col gap-5 ${className}`}>
      {/* Contador */}
      <p className="text-xs" style={{ color: "var(--color-text-muted)" }}>
        {filtered
          ? `Mostrando ${shown.toLocaleString("es")} de ${total.toLocaleString("es")} playlists (filtros aplicados)`
          : `${shown.toLocaleString("es")} playlist${shown !== 1 ? "s" : ""}`}
      </p>

      {/* Grupos por plataforma */}
      {orderedPlats.map((plat) => (
        <PlatformSection
          key={plat}
          platform={plat}
          playlists={byPlat[plat]}
        />
      ))}
    </div>
  );
}
