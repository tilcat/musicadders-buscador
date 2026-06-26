"use client";

/**
 * FugaProgress — Panel de progreso mientras FUGA está paginando.
 *
 * Diseño visual idéntico a BatchProgress: spinner + texto de estado,
 * barra determinada/indeterminada, 3 tarjetas de contadores, nota de persistencia,
 * botón cancelar.
 *
 * Diferencia respecto a batch: los contadores son Páginas / ISRCs / Releases
 * en lugar de Procesados / Encontrados / Sin resultado.
 *
 * Props:
 *   pagesDone     {number}
 *   pagesTotal    {number|null}  — null hasta que el backend lo conoce (indeterminado)
 *   statusText    {string}       — texto del backend, ej. "Paginando… página 5/80"
 *   isrcsFound    {number}
 *   releasesFound {number}
 *   startedAt     {number}       — Date.now() cuando arrancó el job
 *   onCancel      {() => void}
 */

import { useMemo } from "react";

// ── Utilidad ETA ──────────────────────────────────────────────────────────────

function formatEta(secsLeft) {
  if (secsLeft <= 0) return null;
  if (secsLeft < 60) return `~${Math.ceil(secsLeft)} s`;
  return `~${Math.ceil(secsLeft / 60)} min`;
}

// ── Iconos ────────────────────────────────────────────────────────────────────

function IconLoader() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
      aria-hidden="true" style={{ animation: "spin 0.8s linear infinite" }}>
      <path d="M21 12a9 9 0 1 1-6.219-8.56" />
    </svg>
  );
}

function IconPages() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
    </svg>
  );
}

function IconCheck() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function IconDisc() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="10" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

// ── Tarjeta de contador ───────────────────────────────────────────────────────

function CounterCard({ label, value, icon, accentColor, highlight }) {
  return (
    <div
      className="flex flex-col gap-1 px-4 py-3 rounded-xl"
      style={{
        background: highlight ? "var(--color-accent-bg)" : "var(--color-surface)",
        border: `1px solid ${highlight ? "var(--color-accent)" : "var(--color-border)"}`,
        boxShadow: "var(--shadow-sm)",
        minWidth: "120px",
      }}
    >
      <div className="flex items-center gap-1.5">
        {icon && <span style={{ color: accentColor }}>{icon}</span>}
        <span
          className="text-xs"
          style={{ color: highlight ? "var(--color-accent-hover)" : "var(--color-text-soft)" }}
        >
          {label}
        </span>
      </div>
      <span
        className="text-xl font-semibold leading-none"
        style={{
          fontFamily: "var(--font-mono)",
          color: highlight ? "var(--color-accent-hover)" : "var(--color-text)",
          letterSpacing: "-0.02em",
        }}
      >
        {typeof value === "number" ? value.toLocaleString("es") : (value ?? "—")}
      </span>
    </div>
  );
}

// ── Componente principal ──────────────────────────────────────────────────────

export default function FugaProgress({
  pagesDone,
  pagesTotal,
  statusText,
  isrcsFound,
  releasesFound,
  startedAt,
  onCancel,
}) {
  const indeterminate = !pagesTotal;
  // Igual que Streamlit: min(95%, pages_done / estimated_total)
  const pct = indeterminate
    ? 0
    : Math.min(95, Math.round((pagesDone / pagesTotal) * 100));

  const eta = useMemo(() => {
    if (indeterminate || !pagesDone || !startedAt || !pagesTotal) return null;
    const elapsed = (Date.now() - startedAt) / 1000;
    const rate    = pagesDone / elapsed; // páginas/segundo
    if (!rate) return null;
    return formatEta((pagesTotal - pagesDone) / rate);
  }, [pagesDone, pagesTotal, startedAt, indeterminate]);

  return (
    <div className="flex flex-col gap-5 animate-reveal">

      {/* Encabezado */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-2.5 flex-wrap">
          <span style={{ color: "var(--color-accent)" }}>
            <IconLoader />
          </span>
          <p className="text-sm font-medium" style={{ color: "var(--color-text)" }}>
            Consultando FUGA
            {pagesDone > 0 && (
              <span
                className="ml-2"
                style={{
                  color: "var(--color-text-muted)",
                  fontFamily: "var(--font-mono)",
                  fontSize: "12px",
                }}
              >
                {pagesTotal
                  ? `pág. ${pagesDone.toLocaleString("es")} / ~${pagesTotal.toLocaleString("es")}`
                  : `pág. ${pagesDone.toLocaleString("es")}`}
              </span>
            )}
          </p>
          {/* Texto de estado del backend cuando aún no hay páginas */}
          {statusText && !pagesDone && (
            <span className="text-xs" style={{ color: "var(--color-text-muted)" }}>
              {statusText}
            </span>
          )}
          {eta && (
            <span className="text-xs" style={{ color: "var(--color-text-muted)" }}>
              {eta} restante
            </span>
          )}
        </div>

        <button
          type="button"
          onClick={onCancel}
          className="btn btn-danger text-xs px-3 py-1.5"
          style={{ flexShrink: 0 }}
        >
          Cancelar
        </button>
      </div>

      {/* Barra de progreso */}
      <div>
        <div className="fuga-progress-track">
          {indeterminate ? (
            <div
              style={{
                height: "100%",
                width: "35%",
                background: "var(--color-accent)",
                borderRadius: "9999px",
                animation: "indeterminate-bar 1.4s ease-in-out infinite",
              }}
            />
          ) : (
            <div className="fuga-progress-fill" style={{ width: `${pct}%` }} />
          )}
        </div>
        {/* Reutiliza el keyframe definido también en BatchProgress — mismo nombre, sin conflicto */}
        <style>{`
          @keyframes indeterminate-bar {
            0%   { transform: translateX(-100%); }
            50%  { transform: translateX(185%); }
            100% { transform: translateX(185%); }
          }
        `}</style>
        {!indeterminate && (
          <p className="text-xs mt-1.5" style={{ color: "var(--color-text-muted)" }}>
            {pct}% completado (estimado)
          </p>
        )}
      </div>

      {/* Contadores */}
      <div className="flex gap-3 flex-wrap">
        <CounterCard
          label="Páginas paginadas"
          value={pagesDone}
          icon={<IconPages />}
          accentColor="var(--color-text-soft)"
        />
        <CounterCard
          label="ISRCs encontrados"
          value={isrcsFound}
          icon={<IconCheck />}
          accentColor="var(--color-accent)"
          highlight={isrcsFound > 0}
        />
        <CounterCard
          label="Releases"
          value={releasesFound}
          icon={<IconDisc />}
          accentColor="var(--color-text-muted)"
        />
      </div>

      {/* Nota de persistencia */}
      <p className="text-xs" style={{ color: "var(--color-text-muted)" }}>
        La búsqueda puede tardar 1-2 min para rangos largos. Puedes cerrar
        esta pestaña y volver — el progreso se mantiene.
      </p>
    </div>
  );
}
