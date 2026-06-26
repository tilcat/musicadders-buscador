"use client";

/**
 * BatchProgress — Panel de progreso mientras el job está en curso.
 *
 * Muestra:
 *   - Barra de progreso (determinada o indeterminada si total=0 aún)
 *   - Contadores en vivo: procesados / total, encontrados, no encontrados, llamadas API
 *   - ETA estimado
 *   - Botón de cancelar
 *   - Indicador visual de actividad (pulsación + spinner)
 *
 * Props:
 *   hechos       {number}
 *   total        {number}
 *   callsUsed    {number}
 *   notFoundCount{number}
 *   startedAt    {number}    — Date.now() al arrancar el job
 *   onCancel     {() => void}
 */

import { useMemo } from "react";

function formatEta(secsLeft) {
  if (secsLeft <= 0) return null;
  if (secsLeft < 60) return `~${Math.ceil(secsLeft)} s`;
  return `~${Math.ceil(secsLeft / 60)} min`;
}

// ── Iconos ────────────────────────────────────────────────────────────────────

function IconCheck() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function IconMinus() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.5" strokeLinecap="round" aria-hidden="true">
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );
}

function IconLoader() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.5" strokeLinecap="round" aria-hidden="true"
      style={{ animation: "spin 0.8s linear infinite" }}>
      <path d="M21 12a9 9 0 1 1-6.219-8.56" />
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
        border: `1px solid ${highlight ? "var(--color-success-border)" : "var(--color-border)"}`,
        boxShadow: "var(--shadow-sm)",
        minWidth: "120px",
      }}
    >
      <div className="flex items-center gap-1.5">
        <span style={{ color: accentColor }}>{icon}</span>
        <span className="text-xs" style={{ color: highlight ? "var(--color-accent-hover)" : "var(--color-text-soft)" }}>
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
        {value.toLocaleString("es")}
      </span>
    </div>
  );
}

// ── Componente principal ──────────────────────────────────────────────────────

export default function BatchProgress({
  hechos,
  total,
  callsUsed,
  notFoundCount,
  startedAt,
  onCancel,
}) {
  const indeterminate = !total || total === 0;
  const pct = indeterminate ? 0 : Math.round((hechos / total) * 100);
  const encontrados = hechos - notFoundCount;

  // ETA: tiempo transcurrido → tiempo restante estimado
  const eta = useMemo(() => {
    if (indeterminate || !hechos || !startedAt) return null;
    const elapsed = (Date.now() - startedAt) / 1000;
    const rate = hechos / elapsed; // ISRCs por segundo
    if (!rate) return null;
    const remaining = (total - hechos) / rate;
    return formatEta(remaining);
  }, [hechos, total, startedAt, indeterminate]);

  return (
    <div className="flex flex-col gap-5 animate-reveal">
      {/* Encabezado */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-2.5">
          <span style={{ color: "var(--color-accent)" }}>
            <IconLoader />
          </span>
          <p className="text-sm font-medium" style={{ color: "var(--color-text)" }}>
            Consultando Soundcharts
            {!indeterminate && (
              <span className="ml-2" style={{ color: "var(--color-text-muted)", fontFamily: "var(--font-mono)", fontSize: "12px" }}>
                {hechos.toLocaleString("es")} / {total.toLocaleString("es")}
              </span>
            )}
          </p>
          {eta && (
            <span className="text-xs" style={{ color: "var(--color-text-muted)" }}>
              {eta} restante
            </span>
          )}
        </div>

        {/* Cancelar */}
        <button
          type="button"
          onClick={onCancel}
          className="btn btn-danger text-xs px-3 py-1.5"
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
            <div
              className="fuga-progress-fill"
              style={{ width: `${pct}%` }}
            />
          )}
        </div>
        <style>{`
          @keyframes indeterminate-bar {
            0%   { transform: translateX(-100%); }
            50%  { transform: translateX(185%); }
            100% { transform: translateX(185%); }
          }
        `}</style>
        {!indeterminate && (
          <p className="text-xs mt-1.5" style={{ color: "var(--color-text-muted)" }}>
            {pct}% completado
          </p>
        )}
      </div>

      {/* Contadores */}
      <div className="flex gap-3 flex-wrap">
        <CounterCard
          label="Procesados"
          value={hechos}
          icon={null}
          accentColor="var(--color-text-soft)"
        />
        <CounterCard
          label="Encontrados"
          value={Math.max(0, encontrados)}
          icon={<IconCheck />}
          accentColor="var(--color-accent)"
          highlight
        />
        <CounterCard
          label="Sin resultado"
          value={notFoundCount}
          icon={<IconMinus />}
          accentColor="var(--color-warning)"
        />
        <CounterCard
          label="Llamadas API"
          value={callsUsed}
          icon={null}
          accentColor="var(--color-text-muted)"
        />
      </div>

      {/* Nota de persistencia */}
      <p className="text-xs" style={{ color: "var(--color-text-muted)" }}>
        El progreso se mantiene si recargas la página. Puedes cerrar esta pestaña y volver.
      </p>
    </div>
  );
}
