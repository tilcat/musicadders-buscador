"use client";

/**
 * IsrcSearchBar — Barra de búsqueda de la vista F2 "Buscar 1 ISRC".
 *
 * Layout: [input ISRC mono] [selector scope] [Buscar] [Refrescar?]
 *
 * Props:
 *   isrc          {string}
 *   onIsrcChange  {(v: string) => void}
 *   scope         {string}   — "importantes" | "todas" | slug plataforma
 *   onScopeChange {(v: string) => void}
 *   onSearch      {() => void}
 *   onRefresh     {() => void}  — solo visible cuando hasResult=true
 *   loading       {boolean}
 *   hasResult     {boolean}
 *   isrcValid     {boolean}     — false solo si hay texto y no cumple regex
 */

const SCOPE_OPTIONS = [
  { value: "importantes",  label: "Importantes (4)" },
  { value: "todas",        label: "Todas (9)" },
  { value: "spotify",      label: "Spotify" },
  { value: "apple-music",  label: "Apple Music" },
  { value: "amazon",       label: "Amazon Music" },
  { value: "deezer",       label: "Deezer" },
  { value: "youtube",      label: "YouTube" },
  { value: "soundcloud",   label: "SoundCloud" },
  { value: "tidal",        label: "Tidal" },
  { value: "audiomack",    label: "Audiomack" },
  { value: "pandora",      label: "Pandora" },
];

// ── Iconos inline ─────────────────────────────────────────────────────────────

function IconSearch({ size = 13 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <circle cx="11" cy="11" r="8" />
      <path d="m21 21-4.35-4.35" />
    </svg>
  );
}

function IconRefresh({ size = 13 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <polyline points="1 4 1 10 7 10" />
      <path d="M3.51 15a9 9 0 1 0 .49-4.5" />
    </svg>
  );
}

function IconLoader({ size = 13 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
      aria-hidden="true" style={{ animation: "spin 0.8s linear infinite" }}>
      <path d="M21 12a9 9 0 1 1-6.219-8.56" />
    </svg>
  );
}

function IconX({ size = 13 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
      aria-hidden="true">
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}

// ── Componente ────────────────────────────────────────────────────────────────

export default function IsrcSearchBar({
  isrc, onIsrcChange,
  scope, onScopeChange,
  onSearch, onRefresh,
  loading = false,
  hasResult = false,
  isrcValid = true,
}) {
  function handleKeyDown(e) {
    if (e.key === "Enter" && isrcValid && isrc.trim()) onSearch();
  }

  const canSearch = !loading && !!isrc.trim() && isrcValid;

  return (
    <div
      className="flex flex-col gap-4 p-5 rounded-xl"
      style={{
        background: "var(--color-surface)",
        border: "1px solid var(--color-border)",
        boxShadow: "var(--shadow-sm)",
      }}
    >
      <div>
        <label
          htmlFor="isrc-input"
          className="block text-xs font-semibold uppercase tracking-wide mb-2"
          style={{ color: "var(--color-text-soft)" }}
        >
          Código ISRC
        </label>

        <div className="flex gap-2 flex-wrap items-center">

          {/* Input ISRC */}
          <div className="relative" style={{ flex: "1 1 180px", maxWidth: "260px" }}>
            <input
              id="isrc-input"
              type="text"
              value={isrc}
              onChange={(e) => onIsrcChange(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="ES14H2600001"
              maxLength={12}
              spellCheck={false}
              autoCapitalize="characters"
              autoComplete="off"
              disabled={loading}
              className="fuga-input"
              style={{
                fontFamily: "var(--font-mono)",
                letterSpacing: "0.06em",
                fontSize: "14px",
                textTransform: "uppercase",
                paddingRight: isrc ? "30px" : undefined,
              }}
              aria-label="ISRC del track a buscar"
              aria-describedby="isrc-hint"
            />
            {isrc && !loading && (
              <button
                type="button"
                onClick={() => onIsrcChange("")}
                className="absolute right-2.5 top-1/2 -translate-y-1/2"
                style={{ color: "var(--color-text-muted)", lineHeight: 0 }}
                aria-label="Borrar ISRC"
              >
                <IconX />
              </button>
            )}
          </div>

          {/* Scope selector */}
          <select
            value={scope}
            onChange={(e) => onScopeChange(e.target.value)}
            disabled={loading}
            aria-label="Plataformas a consultar"
            className="text-xs rounded-lg"
            style={{
              padding: "9px 10px",
              border: "1px solid var(--color-border)",
              background: "var(--color-surface)",
              color: "var(--color-text)",
              fontFamily: "var(--font-sans)",
              outline: "none",
              cursor: "pointer",
              boxShadow: "var(--shadow-inset)",
            }}
          >
            {SCOPE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>

          {/* Botón buscar */}
          <button
            type="button"
            onClick={onSearch}
            disabled={!canSearch}
            className="btn btn-primary"
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

          {/* Botón refrescar — solo con resultado activo */}
          {hasResult && !loading && (
            <button
              type="button"
              onClick={onRefresh}
              className="btn btn-secondary"
              title="Ignorar caché y refrescar resultado de Soundcharts"
              aria-label="Refrescar resultado ignorando caché"
            >
              <IconRefresh />
              Refrescar
            </button>
          )}
        </div>

        <p
          id="isrc-hint"
          className="text-xs mt-2"
          style={{ color: "var(--color-text-muted)" }}
        >
          12 caracteres: 2 letras de país + 3 alfanuméricos + 7 dígitos.
          Pulsa Enter o el botón para buscar.
        </p>
      </div>
    </div>
  );
}
