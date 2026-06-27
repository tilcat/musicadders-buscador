"use client";

/**
 * PlaylistKpiCard — Tarjeta de indicador numérico compartida entre
 * PlaylistProgress (contadores en vuelo) y PlaylistResult (KPIs de resultado).
 *
 * Unifica CounterCard y KpiCard: mismo tamaño de valor (text-2xl).
 *
 * Props:
 *   label       {string}        — etiqueta descriptiva
 *   value       {number|string} — valor a mostrar
 *   icon        {ReactNode}     — icono opcional (aparece junto al label)
 *   accentColor {string}        — color CSS del icono (default: var(--color-text-soft))
 *   highlight   {boolean}       — fondo/color verde (tracks añadidos)
 *   warn        {boolean}       — fondo/color ámbar (no encontrados, errores)
 */

export default function PlaylistKpiCard({
  label,
  value,
  icon        = null,
  accentColor = "var(--color-text-soft)",
  highlight   = false,
  warn        = false,
}) {
  const bg     = highlight ? "var(--color-accent-bg)"
               : warn      ? "var(--color-warning-bg)"
               :             "var(--color-surface)";
  const border = highlight ? "var(--color-success-border)"
               : warn      ? "var(--color-warning-border)"
               :             "var(--color-border)";
  const labelColor = highlight ? "var(--color-accent-hover)"
                   : warn      ? "var(--color-warning-text)"
                   :             "var(--color-text-soft)";
  const valueColor = highlight ? "var(--color-accent-hover)"
                   : warn      ? "var(--color-warning-text)"
                   :             "var(--color-text)";

  return (
    <div
      className="flex flex-col gap-1 px-4 py-3 rounded-xl"
      style={{
        background: bg,
        border:     `1px solid ${border}`,
        boxShadow:  "var(--shadow-sm)",
        minWidth:   "110px",
      }}
    >
      <div className="flex items-center gap-1.5">
        {icon && (
          <span style={{ color: accentColor, flexShrink: 0 }}>{icon}</span>
        )}
        <span className="text-xs" style={{ color: labelColor }}>
          {label}
        </span>
      </div>
      <span
        className="text-2xl font-semibold leading-none"
        style={{
          fontFamily:    "var(--font-mono)",
          letterSpacing: "-0.03em",
          color:         valueColor,
        }}
      >
        {typeof value === "number" ? value.toLocaleString("es") : (value ?? "—")}
      </span>
    </div>
  );
}
