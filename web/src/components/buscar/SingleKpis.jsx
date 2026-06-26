"use client";

/**
 * SingleKpis — Fila de 4 KPIs para la vista de un único ISRC.
 *
 * Métricas:
 *   - Total playlists
 *   - Oficiales / Algorítmicas  (editorial + algorithmic + charts)
 *   - User-created              (playlist_type "Curators & Listeners" o user)
 *   - DSPs con datos            (X / N plataformas consultadas)
 *
 * Bajo los cards, caption con tiempo de respuesta + llamadas API.
 *
 * Props:
 *   playlists       {Array}   — array completo (sin filtros)
 *   elapsedMs       {number}
 *   callsUsed       {number}
 *   platformsCount  {number}  — DSPs con al menos 1 resultado
 *   totalPlatforms  {number}  — DSPs consultadas (del scope)
 *   className       {string}
 */

import { classifyType } from "@/lib/playlist-utils";

// ── Tarjeta de KPI ────────────────────────────────────────────────────────────

function KpiCard({ label, value, sub, highlight }) {
  return (
    <div
      className="flex flex-col gap-0.5 px-5 py-4 rounded-xl"
      style={{
        background: highlight ? "var(--color-accent-bg)" : "var(--color-surface)",
        border: `1px solid ${highlight ? "var(--color-success-border)" : "var(--color-border)"}`,
        boxShadow: "var(--shadow-sm)",
      }}
    >
      <span className="text-xs" style={{ color: "var(--color-text-soft)" }}>
        {label}
      </span>
      <span
        className="text-2xl font-semibold leading-none"
        style={{
          fontFamily: "var(--font-mono)",
          color: highlight ? "var(--color-accent-hover)" : "var(--color-text)",
          letterSpacing: "-0.03em",
        }}
      >
        {value}
      </span>
      {sub && (
        <span className="text-xs mt-0.5" style={{ color: "var(--color-text-muted)" }}>
          {sub}
        </span>
      )}
    </div>
  );
}

// ── Componente ────────────────────────────────────────────────────────────────

export default function SingleKpis({
  playlists = [],
  elapsedMs,
  callsUsed,
  platformsCount,
  totalPlatforms,
  className = "",
}) {
  const nTotal = playlists.length;

  const nOfficial = playlists.filter((p) => {
    const t = classifyType(p.playlist_type);
    return t === "editorial" || t === "algorithmic" || t === "charts";
  }).length;

  const nUser = playlists.filter(
    (p) => classifyType(p.playlist_type) === "user"
  ).length;

  const nPlats =
    platformsCount ??
    new Set(playlists.map((p) => p.platform)).size;

  const nTotal_plats = totalPlatforms ?? nPlats;

  // Caption
  const captionParts = [];
  if (elapsedMs) captionParts.push(`${elapsedMs} ms`);
  if (callsUsed) {
    captionParts.push(
      `${callsUsed} llamada${callsUsed !== 1 ? "s" : ""} API consumida${callsUsed !== 1 ? "s" : ""}`
    );
  }

  return (
    <div className={`flex flex-col gap-2 ${className}`}>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <KpiCard
          label="Total playlists"
          value={nTotal.toLocaleString("es")}
          highlight={nTotal > 0}
        />
        <KpiCard
          label="Oficiales / Algorítmicas"
          value={nOfficial.toLocaleString("es")}
          sub="Editorial · Algoritmo · Charts"
          highlight={nOfficial > 0}
        />
        <KpiCard
          label="User-created"
          value={nUser.toLocaleString("es")}
          sub="Curators & Listeners"
        />
        <KpiCard
          label="DSPs con datos"
          value={`${nPlats} / ${nTotal_plats}`}
        />
      </div>

      {captionParts.length > 0 && (
        <p className="text-xs" style={{ color: "var(--color-text-muted)" }}>
          {captionParts.join(" · ")}
        </p>
      )}
    </div>
  );
}
