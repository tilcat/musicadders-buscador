"use client";

/**
 * PlaylistCooldown — Aviso de cooldown/penalty-box de Spotify.
 *
 * Este NO es un error. Spotify ha activado rate-limit; el job permanece en cola
 * server-side y se reanudará automáticamente. El polling continúa en segundo
 * plano con intervalo reducido.
 *
 * Diferenciación visual respecto a errores (danger): usa el sistema de warning
 * (ámbar) para comunicar "pausa temporal, no fallo". El countdown cuenta el
 * tiempo real restante hasta que Spotify reanude.
 *
 * Props:
 *   cooldownUntil  {string|null}  — ISO datetime hasta cuándo dura el cooldown
 *   onCancel       {() => void}   — cancela el job (acción voluntaria)
 */

import { useState, useEffect } from "react";

// ── Countdown ─────────────────────────────────────────────────────────────────

function useCountdown(until) {
  const [secsLeft, setSecsLeft] = useState(0);

  useEffect(() => {
    if (!until) { setSecsLeft(0); return; }

    function compute() {
      setSecsLeft(Math.max(0, Math.round((new Date(until) - Date.now()) / 1000)));
    }

    compute();
    const id = setInterval(compute, 1000);
    return () => clearInterval(id);
  }, [until]);

  return secsLeft;
}

function formatCountdown(secs) {
  if (secs <= 0) return null;
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = secs % 60;
  if (h > 0) return `${h} h ${m} min`;
  if (m > 0) return `${m} min ${String(s).padStart(2, "0")} s`;
  return `${s} s`;
}

// ── Iconos ────────────────────────────────────────────────────────────────────

function IconClock() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="10" />
      <polyline points="12 6 12 12 16 14" />
    </svg>
  );
}

// ── Componente ────────────────────────────────────────────────────────────────

export default function PlaylistCooldown({ cooldownUntil, onCancel }) {
  const secsLeft  = useCountdown(cooldownUntil);
  const countdown = formatCountdown(secsLeft);

  return (
    <div
      className="flex flex-col gap-4 p-5 rounded-xl animate-reveal"
      role="status"
      aria-live="polite"
      style={{
        background: "var(--color-warning-bg)",
        border:     "1px solid var(--color-warning-border)",
      }}
    >
      {/* Encabezado: icono + texto + botón cancelar */}
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span
            style={{
              color:     "var(--color-warning-text)",
              flexShrink: 0,
              marginTop:  2,
            }}
          >
            <IconClock />
          </span>
          <div>
            <p
              className="text-sm font-semibold"
              style={{ color: "var(--color-warning-text)" }}
            >
              Spotify está aplicando rate-limit
            </p>
            <p
              className="text-xs mt-1"
              style={{ color: "var(--color-warning-text)", opacity: 0.85, lineHeight: 1.5 }}
            >
              El proceso sigue activo — está en pausa hasta que Spotify levante
              la restricción. No hace falta hacer nada; el progreso no se pierde
              y se reanudará automáticamente.
            </p>
          </div>
        </div>

        <button
          type="button"
          onClick={onCancel}
          className="btn btn-secondary text-xs whitespace-nowrap"
          style={{ flexShrink: 0 }}
        >
          Cancelar
        </button>
      </div>

      {/* Countdown si hay fecha conocida */}
      {countdown && (
        <div
          className="flex items-center gap-3 px-3 py-2.5 rounded-lg"
          style={{
            background: "var(--color-surface)",
            border:     "1px solid var(--color-warning-border)",
          }}
        >
          <span
            className="text-sm font-semibold tabular-nums"
            style={{
              color:       "var(--color-warning-text)",
              fontFamily:  "var(--font-mono)",
              letterSpacing: "0.02em",
            }}
          >
            {countdown}
          </span>
          <span className="text-xs" style={{ color: "var(--color-warning-text)", opacity: 0.7 }}>
            restante antes de reanudar
          </span>
        </div>
      )}

      {/* Comprobando si ya pasó el cooldown */}
      {!countdown && cooldownUntil && (
        <p className="text-xs" style={{ color: "var(--color-warning-text)", opacity: 0.7 }}>
          Comprobando si Spotify está listo para continuar…
        </p>
      )}

      {/* Sin fecha conocida: cooldown indeterminado */}
      {!cooldownUntil && (
        <p className="text-xs" style={{ color: "var(--color-warning-text)", opacity: 0.7 }}>
          Duración del cooldown no disponible. Sigue comprobando en segundo plano.
        </p>
      )}
    </div>
  );
}
