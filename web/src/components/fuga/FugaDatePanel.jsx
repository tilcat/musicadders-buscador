"use client";

/**
 * FugaDatePanel — Panel de selección de rango de fechas de lanzamiento.
 *
 * Layout desktop: [Desde] [Hasta] [Buscar] en una fila mediante .fuga-date-grid.
 * Muestra aviso warning cuando el rango supera 3 meses (búsqueda lenta).
 * Muestra aviso danger cuando dateFrom > dateTo (rango inválido).
 * Muestra aviso danger cuando el rango supera 366 días (límite del backend).
 *
 * Props:
 *   dateFrom    {string}   — "YYYY-MM-DD"
 *   dateTo      {string}   — "YYYY-MM-DD"
 *   onDateFrom  {fn}
 *   onDateTo    {fn}
 *   onSubmit    {fn}
 *   loading     {boolean}  — true durante "submitting" (deshabilita todo)
 *   rangeError  {boolean}  — true si dateFrom > dateTo
 *   rangeTooBig {boolean}  — true si el rango supera 366 días
 */

import { useMemo } from "react";

// ── Iconos ────────────────────────────────────────────────────────────────────

function IconSearch({ size = 14 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <circle cx="11" cy="11" r="8" />
      <path d="m21 21-4.35-4.35" />
    </svg>
  );
}

function IconLoader() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
      aria-hidden="true" style={{ animation: "spin 0.8s linear infinite" }}>
      <path d="M21 12a9 9 0 1 1-6.219-8.56" />
    </svg>
  );
}

function IconInfo({ size = 13 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="10" />
      <line x1="12" y1="8" x2="12" y2="12" />
      <line x1="12" y1="16" x2="12.01" y2="16" />
    </svg>
  );
}

function IconCalendar({ size = 14 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.75" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="4" width="18" height="18" rx="2" ry="2" />
      <line x1="16" y1="2" x2="16" y2="6" />
      <line x1="8" y1="2" x2="8" y2="6" />
      <line x1="3" y1="10" x2="21" y2="10" />
    </svg>
  );
}

// ── Utilidad ──────────────────────────────────────────────────────────────────

function monthsDiff(from, to) {
  if (!from || !to) return 0;
  const d1 = new Date(from);
  const d2 = new Date(to);
  return (d2.getFullYear() - d1.getFullYear()) * 12 + (d2.getMonth() - d1.getMonth());
}

// ── Componente ────────────────────────────────────────────────────────────────

export default function FugaDatePanel({
  dateFrom,
  dateTo,
  onDateFrom,
  onDateTo,
  onSubmit,
  loading,
  rangeError,
  rangeTooBig,
}) {
  const months = useMemo(() => monthsDiff(dateFrom, dateTo), [dateFrom, dateTo]);
  const isLongRange = months >= 3;

  return (
    <div
      className="flex flex-col gap-5 p-5 rounded-xl animate-reveal animate-reveal-delay-1"
      style={{
        background: "var(--color-surface)",
        border: "1px solid var(--color-border)",
        boxShadow: "var(--shadow-sm)",
      }}
    >
      {/* Grid: [Desde] [Hasta] [Buscar] */}
      <div className="fuga-date-grid">

        {/* Desde */}
        <div>
          <label
            htmlFor="fuga-date-from"
            className="block text-xs font-semibold uppercase tracking-wide mb-1.5"
            style={{ color: "var(--color-text-soft)" }}
          >
            Desde
          </label>
          <div className="fuga-date-wrapper">
            <input
              id="fuga-date-from"
              type="date"
              value={dateFrom}
              onChange={(e) => onDateFrom(e.target.value)}
              disabled={loading}
              aria-invalid={rangeError || rangeTooBig || undefined}
              className="fuga-input fuga-date-input"
              style={rangeError || rangeTooBig ? { borderColor: "var(--color-danger)" } : undefined}
            />
            <span className="fuga-date-icon" aria-hidden="true">
              <IconCalendar />
            </span>
          </div>
        </div>

        {/* Hasta */}
        <div>
          <label
            htmlFor="fuga-date-to"
            className="block text-xs font-semibold uppercase tracking-wide mb-1.5"
            style={{ color: "var(--color-text-soft)" }}
          >
            Hasta
          </label>
          <div className="fuga-date-wrapper">
            <input
              id="fuga-date-to"
              type="date"
              value={dateTo}
              onChange={(e) => onDateTo(e.target.value)}
              disabled={loading}
              aria-invalid={rangeError || rangeTooBig || undefined}
              className="fuga-input fuga-date-input"
              style={rangeError || rangeTooBig ? { borderColor: "var(--color-danger)" } : undefined}
            />
            <span className="fuga-date-icon" aria-hidden="true">
              <IconCalendar />
            </span>
          </div>
        </div>

        {/* Botón Buscar — sin paddingTop artificial: align-items: end en la grid lo alinea */}
        <div>
          <button
            type="button"
            onClick={onSubmit}
            disabled={loading || rangeError || rangeTooBig || !dateFrom || !dateTo}
            className="btn btn-primary"
            style={{ width: "100%" }}
          >
            {loading ? (
              <>
                <IconLoader />
                Buscando…
              </>
            ) : (
              <>
                <IconSearch />
                Buscar
              </>
            )}
          </button>
        </div>
      </div>

      {/* Aviso: rango inválido (dateFrom > dateTo) */}
      {rangeError && (
        <div
          className="flex items-center gap-2 px-3 py-2 rounded-lg text-xs animate-reveal"
          role="alert"
          style={{
            background: "var(--color-danger-bg)",
            border: "1px solid var(--color-danger-border)",
            color: "var(--color-danger-text)",
          }}
        >
          <span style={{ flexShrink: 0 }}><IconInfo /></span>
          La fecha "Desde" debe ser anterior o igual a "Hasta".
        </div>
      )}

      {/* Aviso: rango demasiado largo (> 366 días) */}
      {rangeTooBig && !rangeError && (
        <div
          className="flex items-center gap-2 px-3 py-2 rounded-lg text-xs animate-reveal"
          role="alert"
          style={{
            background: "var(--color-danger-bg)",
            border: "1px solid var(--color-danger-border)",
            color: "var(--color-danger-text)",
          }}
        >
          <span style={{ flexShrink: 0 }}><IconInfo /></span>
          El rango no puede superar <strong>366 días</strong>. Divide la búsqueda en rangos más cortos.
        </div>
      )}

      {/* Aviso: rango largo (≥3 meses) — solo si no hay error */}
      {isLongRange && !rangeError && !rangeTooBig && (
        <div
          className="flex items-start gap-2.5 px-3 py-2.5 rounded-lg text-xs animate-reveal"
          role="status"
          style={{
            background: "var(--color-warning-bg)",
            border: "1px solid var(--color-warning-border)",
            color: "var(--color-warning-text)",
          }}
        >
          <span style={{ flexShrink: 0, marginTop: 1 }}><IconInfo /></span>
          <span>
            Rango de <strong>{months} meses</strong> — la búsqueda puede tardar{" "}
            <strong>1-2 minutos</strong> paginando FUGA.{" "}
            {months > 6 && "Para rangos superiores a 6 meses considera dividir la búsqueda."}
          </span>
        </div>
      )}
    </div>
  );
}
