"use client";

/**
 * PlaylistProgress — Panel de progreso durante la creación de la playlist.
 *
 * Tres fases lineales: resolver ISRCs → crear playlist → añadir tracks.
 * Estructura visual idéntica a FugaProgress / BatchProgress, con el añadido
 * del indicador de fases (step pills).
 *
 * Props:
 *   phase       {"resolving"|"creating"|"adding"|null}
 *   resolved    {number}   — ISRCs con URI de Spotify encontrado
 *   total       {number}   — total ISRCs de la solicitud
 *   added       {number}   — tracks añadidos a la playlist hasta ahora
 *   notFound    {number}   — ISRCs sin URI en Spotify
 *   progressPct {number}   — 0-100 (controlado por el backend)
 *   statusText  {string}   — texto libre del backend ("Resolviendo 3/120…")
 *   startedAt   {number}   — Date.now() cuando arrancó el job (para ETA)
 *   onCancel    {() => void}
 */

import { useMemo } from "react";
import PlaylistKpiCard from "@/components/playlist/PlaylistKpiCard";

// ── Fases ─────────────────────────────────────────────────────────────────────

const PHASES = [
  { key: "resolving", label: "Resolver ISRCs" },
  { key: "creating",  label: "Crear playlist"  },
  { key: "adding",    label: "Añadir tracks"   },
];

function phaseIndex(phase) {
  const i = PHASES.findIndex((p) => p.key === phase);
  return i < 0 ? 0 : i;
}

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

function IconCheckSmall() {
  return (
    <svg width="10" height="10" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="3.5" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function IconCheckMed() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function IconMinus() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" aria-hidden="true">
      <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
  );
}

// ── Indicador de fases ────────────────────────────────────────────────────────

function PhaseSteps({ phase }) {
  const current = phaseIndex(phase);

  return (
    <div className="flex items-center" aria-label="Fases del proceso">
      {PHASES.map((p, i) => {
        const done    = i < current;
        const active  = i === current;

        return (
          <div key={p.key} className="flex items-center" style={{ flex: i < PHASES.length - 1 ? 1 : undefined }}>
            {/* Pill de fase */}
            <div
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium"
              style={{
                background: done   ? "var(--color-accent-bg)"      : active ? "var(--color-accent)" : "var(--color-surface-raised)",
                border:     `1px solid ${done ? "var(--color-success-border)" : active ? "var(--color-accent)" : "var(--color-border)"}`,
                color:      done   ? "var(--color-accent-hover)"   : active ? "#ffffff"              : "var(--color-text-muted)",
                whiteSpace: "nowrap",
                flexShrink: 0,
              }}
              aria-current={active ? "step" : undefined}
            >
              {done ? (
                <span style={{ color: "var(--color-accent)" }}>
                  <IconCheckSmall />
                </span>
              ) : (
                <span
                  style={{
                    fontFamily: "var(--font-mono)",
                    fontSize: "10px",
                    lineHeight: 1,
                    opacity: active ? 1 : 0.5,
                  }}
                >
                  {i + 1}
                </span>
              )}
              <span className="hidden sm:inline">{p.label}</span>
            </div>

            {/* Línea conectora */}
            {i < PHASES.length - 1 && (
              <div
                style={{
                  flex: 1,
                  height: 2,
                  margin: "0 4px",
                  background: done ? "var(--color-accent)" : "var(--color-border)",
                  borderRadius: 1,
                  minWidth: 8,
                }}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

// CounterCard eliminado — se usa PlaylistKpiCard compartido (Fix 17)

// ── Componente principal ──────────────────────────────────────────────────────

export default function PlaylistProgress({
  phase,
  resolved,
  total,
  added,
  notFound,
  progressPct,
  statusText,
  startedAt,
  onCancel,
}) {
  const eta = useMemo(() => {
    if (!progressPct || progressPct >= 100 || !startedAt) return null;
    const elapsed = (Date.now() - startedAt) / 1000;
    const rate    = progressPct / elapsed;
    if (!rate) return null;
    return formatEta((100 - progressPct) / rate);
  }, [progressPct, startedAt]);

  const pct = Math.max(2, Math.min(99, progressPct)); // mínimo 2% para visibilidad

  return (
    <div className="flex flex-col gap-5 animate-reveal">

      {/* Encabezado */}
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-2.5 flex-wrap">
          <span style={{ color: "var(--color-accent)" }}>
            <IconLoader />
          </span>
          <p className="text-sm font-medium" style={{ color: "var(--color-text)" }}>
            Creando playlist
            {total > 0 && resolved > 0 && (
              <span
                className="ml-2"
                style={{
                  color:       "var(--color-text-muted)",
                  fontFamily:  "var(--font-mono)",
                  fontSize:    "12px",
                }}
              >
                {resolved.toLocaleString("es")} / {total.toLocaleString("es")} ISRCs
              </span>
            )}
          </p>
          {statusText && (
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

      {/* Indicador de fases */}
      <PhaseSteps phase={phase} />

      {/* Barra de progreso */}
      <div>
        <div className="fuga-progress-track">
          <div className="fuga-progress-fill" style={{ width: `${pct}%` }} />
        </div>
        <p className="text-xs mt-1.5" style={{ color: "var(--color-text-muted)" }}>
          {progressPct}% completado
        </p>
      </div>

      {/* Contadores */}
      <div className="flex gap-3 flex-wrap">
        <PlaylistKpiCard
          label="ISRCs resueltos"
          value={resolved}
          icon={<IconCheckMed />}
          accentColor="var(--color-text-muted)"
        />
        <PlaylistKpiCard
          label="Tracks añadidos"
          value={added}
          icon={<IconCheckMed />}
          accentColor="var(--color-accent)"
          highlight={added > 0}
        />
        <PlaylistKpiCard
          label="No encontrados"
          value={notFound}
          icon={<IconMinus />}
          accentColor="var(--color-warning)"
          warn={notFound > 0}
        />
      </div>

      {/* Nota de persistencia */}
      <p className="text-xs" style={{ color: "var(--color-text-muted)" }}>
        Cada ISRC se resuelve individualmente respetando el rate-limit de Spotify — puede
        tardar varios minutos. Puedes cerrar esta pestaña y volver; el progreso se mantiene.
      </p>
    </div>
  );
}
