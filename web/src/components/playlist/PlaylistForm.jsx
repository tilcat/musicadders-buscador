"use client";

/**
 * PlaylistForm — Campos de la playlist: nombre, descripción, visibilidad.
 *
 * Componente controlado; el padre gestiona el estado. No contiene el botón
 * de envío (eso va en la page para tener acceso a todo el estado del formulario).
 *
 * Props:
 *   name        {string}
 *   description {string}
 *   isPublic    {boolean}
 *   onName      {(string) => void}
 *   onDesc      {(string) => void}
 *   onPublic    {(boolean) => void}
 */

const DESC_MAX = 300;

// ── Iconos ────────────────────────────────────────────────────────────────────

function IconGlobe() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="10" />
      <line x1="2" y1="12" x2="22" y2="12" />
      <path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z" />
    </svg>
  );
}

function IconLock() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
      <path d="M7 11V7a5 5 0 0 1 10 0v4" />
    </svg>
  );
}

// ── Toggle de visibilidad ─────────────────────────────────────────────────────

function VisibilityToggle({ isPublic, onPublic }) {
  const OPTIONS = [
    { key: false, label: "Privada", Icon: IconLock  },
    { key: true,  label: "Pública", Icon: IconGlobe },
  ];

  return (
    <div className="flex flex-col gap-1.5">
      <span
        className="text-xs font-semibold uppercase tracking-wide"
        style={{ color: "var(--color-text-soft)" }}
      >
        Visibilidad
      </span>
      <div className="flex gap-2" role="group" aria-label="Visibilidad de la playlist">
        {OPTIONS.map(({ key, label, Icon }) => {
          const active = isPublic === key;
          return (
            <button
              key={String(key)}
              type="button"
              onClick={() => onPublic(key)}
              aria-pressed={active}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all"
              style={{
                border:     `1px solid ${active ? "var(--color-accent)" : "var(--color-border)"}`,
                background: active ? "var(--color-accent-bg)" : "var(--color-surface)",
                color:      active ? "var(--color-accent-hover)" : "var(--color-text-soft)",
                cursor:     "pointer",
              }}
            >
              <span style={{ color: active ? "var(--color-accent)" : "var(--color-text-muted)" }}>
                <Icon />
              </span>
              {label}
            </button>
          );
        })}
      </div>
      <p className="text-xs" style={{ color: "var(--color-text-muted)" }}>
        {isPublic
          ? "Cualquiera con el enlace puede ver la playlist."
          : "La playlist solo será visible en la cuenta central."}
      </p>
    </div>
  );
}

// ── Componente principal ──────────────────────────────────────────────────────

export default function PlaylistForm({ name, description, isPublic, onName, onDesc, onPublic }) {
  const descLeft = DESC_MAX - description.length;
  const descNear = descLeft < 40;

  return (
    <div className="flex flex-col gap-4">

      {/* Nombre */}
      <div>
        <label
          htmlFor="pl-name"
          className="block text-xs font-semibold uppercase tracking-wide mb-1.5"
          style={{ color: "var(--color-text-soft)" }}
        >
          Nombre{" "}
          <span style={{ color: "var(--color-danger)" }} aria-label="requerido">*</span>
        </label>
        <input
          id="pl-name"
          type="text"
          className="fuga-input"
          placeholder="Ej. Nuevo catálogo Rapport — Junio 2026"
          value={name}
          onChange={(e) => onName(e.target.value)}
          maxLength={100}
          required
          autoComplete="off"
        />
      </div>

      {/* Descripción */}
      <div>
        <label
          htmlFor="pl-desc"
          className="block text-xs font-semibold uppercase tracking-wide mb-1.5"
          style={{ color: "var(--color-text-soft)" }}
        >
          Descripción{" "}
          <span style={{ color: "var(--color-text-muted)", textTransform: "none", fontWeight: 400 }}>
            (opcional)
          </span>
        </label>
        <textarea
          id="pl-desc"
          className="fuga-input"
          rows={2}
          placeholder="Descripción breve de la playlist…"
          value={description}
          onChange={(e) => onDesc(e.target.value.slice(0, DESC_MAX))}
          style={{ resize: "vertical" }}
        />
        <p
          className="text-xs mt-1 text-right tabular-nums"
          style={{
            color: descNear
              ? "var(--color-warning-text)"
              : "var(--color-text-muted)",
            fontFamily: "var(--font-mono)",
          }}
        >
          {descLeft}
        </p>
      </div>

      {/* Visibilidad */}
      <VisibilityToggle isPublic={isPublic} onPublic={onPublic} />
    </div>
  );
}
