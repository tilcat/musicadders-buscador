"use client";

/**
 * PlatformSelector — Selector de plataformas DSP para el batch.
 *
 * Tres modos rápidos: "Importantes (4)", "Todas (9)", "Personalizado".
 * En personalizado se muestran checkboxes por plataforma.
 *
 * Props:
 *   value    {string[]}         — slugs seleccionados
 *   onChange {(slugs) => void}
 */

import { useState } from "react";

const PLATFORMS_MAIN = [
  { slug: "spotify",     label: "Spotify",      color: "#1a9e5c" },
  { slug: "apple-music", label: "Apple Music",  color: "#fc3c44" },
  { slug: "amazon",      label: "Amazon Music", color: "#e87c14" },
  { slug: "deezer",      label: "Deezer",       color: "#a066d3" },
];

const PLATFORMS_EXTRA = [
  { slug: "youtube",    label: "YouTube",    color: "#dc2626" },
  { slug: "soundcloud", label: "SoundCloud", color: "#d44c00" },
  { slug: "tidal",      label: "Tidal",      color: "#1a1f2e" },
  { slug: "audiomack",  label: "Audiomack",  color: "#f59e0b" },
  { slug: "pandora",    label: "Pandora",    color: "#005fa3" },
];

const ALL_PLATFORMS = [...PLATFORMS_MAIN, ...PLATFORMS_EXTRA];
const MAIN_SLUGS    = PLATFORMS_MAIN.map((p) => p.slug);
const ALL_SLUGS     = ALL_PLATFORMS.map((p) => p.slug);

function arraysEqual(a, b) {
  if (a.length !== b.length) return false;
  const sa = [...a].sort();
  const sb = [...b].sort();
  return sa.every((v, i) => v === sb[i]);
}

function getMode(value) {
  if (arraysEqual(value, MAIN_SLUGS)) return "main";
  if (arraysEqual(value, ALL_SLUGS))  return "all";
  return "custom";
}

// ── Dot de color de plataforma ────────────────────────────────────────────────

function PlatDot({ color }) {
  return (
    <span
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

// ── Botón de modo rápido ──────────────────────────────────────────────────────

function ModeButton({ active, onClick, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="px-3 py-1.5 text-xs font-medium rounded-md transition-colors"
      style={{
        border: `1px solid ${active ? "var(--color-accent)" : "var(--color-border)"}`,
        background: active ? "var(--color-accent-bg)" : "var(--color-surface)",
        color: active ? "var(--color-accent-hover)" : "var(--color-text-soft)",
        cursor: "pointer",
      }}
    >
      {children}
    </button>
  );
}

// ── Checkbox de plataforma ────────────────────────────────────────────────────

function PlatCheckbox({ plat, checked, onChange }) {
  return (
    <label
      className="flex items-center gap-2 cursor-pointer select-none px-2.5 py-1.5 rounded-md transition-colors"
      style={{
        background: checked ? "var(--color-accent-bg)" : "transparent",
        border: `1px solid ${checked ? "var(--color-success-border)" : "var(--color-border)"}`,
      }}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(plat.slug, e.target.checked)}
        className="sr-only"
      />
      {/* Custom checkbox */}
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          justifyContent: "center",
          width: 14,
          height: 14,
          borderRadius: 3,
          border: `1.5px solid ${checked ? "var(--color-accent)" : "var(--color-border-strong)"}`,
          background: checked ? "var(--color-accent)" : "var(--color-surface)",
          flexShrink: 0,
          transition: "background 150ms, border-color 150ms",
        }}
      >
        {checked && (
          <svg width="9" height="7" viewBox="0 0 9 7" fill="none" aria-hidden="true">
            <path d="M1 3.5l2.5 2.5 5-5" stroke="white" strokeWidth="1.5"
              strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        )}
      </span>
      <PlatDot color={plat.color} />
      <span className="text-xs font-medium" style={{ color: "var(--color-text)" }}>
        {plat.label}
      </span>
    </label>
  );
}

// ── Componente principal ──────────────────────────────────────────────────────

export default function PlatformSelector({ value, onChange }) {
  const mode = getMode(value);
  const [showCustom, setShowCustom] = useState(mode === "custom");

  function setMode(m) {
    if (m === "main")   { onChange(MAIN_SLUGS); setShowCustom(false); }
    if (m === "all")    { onChange(ALL_SLUGS);  setShowCustom(false); }
    if (m === "custom") { setShowCustom(true); }
  }

  function togglePlat(slug, checked) {
    const next = checked
      ? [...value, slug]
      : value.filter((s) => s !== slug);
    onChange(next);
  }

  return (
    <div className="flex flex-col gap-3">
      {/* Botones de modo rápido */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs font-medium" style={{ color: "var(--color-text-soft)" }}>
          Plataformas:
        </span>
        <ModeButton active={mode === "main" && !showCustom} onClick={() => setMode("main")}>
          Principales (4)
        </ModeButton>
        <ModeButton active={mode === "all" && !showCustom} onClick={() => setMode("all")}>
          Todas (9)
        </ModeButton>
        <ModeButton active={showCustom} onClick={() => setMode("custom")}>
          Personalizar
        </ModeButton>
      </div>

      {/* Grid de checkboxes en modo personalizado */}
      {showCustom && (
        <div>
          <p className="text-xs mb-2" style={{ color: "var(--color-text-muted)" }}>
            Principales
          </p>
          <div className="grid grid-cols-2 gap-1.5 mb-3" style={{ maxWidth: "360px" }}>
            {PLATFORMS_MAIN.map((p) => (
              <PlatCheckbox
                key={p.slug}
                plat={p}
                checked={value.includes(p.slug)}
                onChange={togglePlat}
              />
            ))}
          </div>

          <p className="text-xs mb-2" style={{ color: "var(--color-text-muted)" }}>
            Adicionales
          </p>
          <div className="grid grid-cols-2 gap-1.5" style={{ maxWidth: "360px" }}>
            {PLATFORMS_EXTRA.map((p) => (
              <PlatCheckbox
                key={p.slug}
                plat={p}
                checked={value.includes(p.slug)}
                onChange={togglePlat}
              />
            ))}
          </div>

          {value.length === 0 && (
            <p className="text-xs mt-2" style={{ color: "var(--color-danger)" }}>
              Selecciona al menos una plataforma.
            </p>
          )}
        </div>
      )}

      {/* Resumen de selección (en modos no-custom) */}
      {!showCustom && (
        <div className="flex items-center gap-1.5 flex-wrap">
          {ALL_PLATFORMS.filter((p) => value.includes(p.slug)).map((p) => (
            <span
              key={p.slug}
              className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium"
              style={{
                background: "var(--color-surface)",
                border: "1px solid var(--color-border)",
                color: "var(--color-text-soft)",
              }}
            >
              <PlatDot color={p.color} />
              {p.label}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
