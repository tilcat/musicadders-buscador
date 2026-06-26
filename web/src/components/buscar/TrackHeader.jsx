"use client";

/**
 * TrackHeader — Cabecera del track encontrado.
 *
 * Muestra: nombre, artista/credit_name, fecha de lanzamiento, badge ISRC.
 *
 * Props:
 *   meta      {{ song_name, credit_name, release_date }}
 *   isrc      {string}
 *   className {string}
 */

export default function TrackHeader({ meta, isrc, className = "" }) {
  const { song_name, credit_name, release_date } = meta ?? {};

  const parts = [];
  if (credit_name)  parts.push({ type: "artist",  value: credit_name });
  if (release_date) parts.push({ type: "date",    value: release_date.slice(0, 10) });
  if (isrc)         parts.push({ type: "isrc",    value: isrc });

  return (
    <div
      className={`px-5 py-4 rounded-xl ${className}`}
      style={{
        background: "var(--color-surface)",
        border: "1px solid var(--color-border)",
        boxShadow: "var(--shadow-sm)",
      }}
    >
      <h2
        className="text-lg font-semibold leading-snug"
        style={{ color: "var(--color-text)", letterSpacing: "-0.01em" }}
      >
        {song_name || "—"}
      </h2>

      {parts.length > 0 && (
        <div
          className="flex items-center gap-2 flex-wrap mt-1.5"
          style={{ color: "var(--color-text-soft)" }}
        >
          {parts.map((part, i) => (
            <span key={part.type} className="flex items-center gap-2">
              {i > 0 && (
                <span style={{ color: "var(--color-border-strong)" }} aria-hidden="true">·</span>
              )}

              {part.type === "artist" && (
                <span className="text-sm font-medium" style={{ color: "var(--color-text-soft)" }}>
                  {part.value}
                </span>
              )}

              {part.type === "date" && (
                <span className="text-sm" style={{ color: "var(--color-text-muted)" }}>
                  {part.value}
                </span>
              )}

              {part.type === "isrc" && (
                <span className="isrc-badge" aria-label={`ISRC: ${part.value}`}>
                  {part.value}
                </span>
              )}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
