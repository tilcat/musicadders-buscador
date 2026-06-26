"use client";

/**
 * PlaylistCard — Fila compacta de una playlist individual.
 *
 * Layout (horizontal):
 *   [dot plataforma] [nombre truncado ...] [TypeChip] [seguidores mono] [#pos]
 *
 * Props:
 *   playlist {{ platform, playlist_name, playlist_type, subscriber_count, position }}
 */

import { classifyType, formatNumber, PLAT_COLORS, TYPE_LABELS } from "@/lib/playlist-utils";

// ── Dot de color ──────────────────────────────────────────────────────────────

function PlatDot({ platform }) {
  const color = PLAT_COLORS[platform] ?? "#9ba3af";
  return (
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
  );
}

// ── Componente ────────────────────────────────────────────────────────────────

export default function PlaylistCard({ playlist }) {
  const {
    platform,
    playlist_name,
    playlist_type,
    subscriber_count,
    position,
  } = playlist;

  const type = classifyType(playlist_type);

  return (
    <div className="pl-card" role="listitem">
      <PlatDot platform={platform} />

      <span className="pl-card-name" title={playlist_name ?? undefined}>
        {playlist_name || (
          <span style={{ color: "var(--color-text-muted)" }}>—</span>
        )}
      </span>

      <span className="type-chip" data-type={type} aria-label={`Tipo: ${TYPE_LABELS[type] ?? type}`}>
        {TYPE_LABELS[type] ?? type}
      </span>

      {subscriber_count != null && (
        <span
          className="pl-card-subs"
          aria-label={`${subscriber_count.toLocaleString("es")} seguidores`}
        >
          {formatNumber(subscriber_count)}
        </span>
      )}

      {position != null && (
        <span
          className="pl-card-pos"
          aria-label={`Posición ${position}`}
        >
          #{position}
        </span>
      )}
    </div>
  );
}
