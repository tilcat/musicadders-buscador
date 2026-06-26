"use client";

/**
 * EmptyState — Estados vacíos y de error para la vista F2.
 *
 * Tipos:
 *   "idle"          — pantalla inicial, sin ISRC introducido
 *   "not_found"     — Soundcharts no reconoce el ISRC
 *   "error"         — error de red o servidor
 *   "no_placements" — track encontrado pero sin placements en el scope
 *
 * Props:
 *   type          {"idle"|"not_found"|"error"|"no_placements"}
 *   isrc          {string}   — para not_found
 *   msg           {string}   — para error
 *   onRetry       {()=>void} — botón reintentar (not_found, error)
 *   onChangeScope {()=>void} — botón "Buscar en todas" (no_placements)
 */

// ── Iconos ────────────────────────────────────────────────────────────────────

function IconSearch({ size = 36 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <circle cx="11" cy="11" r="8" />
      <path d="m21 21-4.35-4.35" />
    </svg>
  );
}

function IconAlertCircle({ size = 18 }) {
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

function IconInbox({ size = 28 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <polyline points="22 12 16 12 14 15 10 15 8 12 2 12" />
      <path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" />
    </svg>
  );
}

// ── Componente ────────────────────────────────────────────────────────────────

export default function EmptyState({
  type,
  isrc,
  msg,
  onRetry,
  onChangeScope,
}) {
  // ── Pantalla inicial ──────────────────────────────────────────────────────
  if (type === "idle") {
    return (
      <div
        className="flex flex-col items-center justify-center gap-4 py-16 rounded-xl animate-reveal"
        role="status"
        style={{
          background: "var(--color-surface)",
          border: "1px solid var(--color-border)",
        }}
      >
        <span style={{ color: "var(--color-border-strong)" }}>
          <IconSearch />
        </span>
        <div className="text-center">
          <p
            className="text-sm font-medium"
            style={{ color: "var(--color-text-soft)" }}
          >
            Introduce un ISRC para buscar sus playlists
          </p>
          <p
            className="text-xs mt-1"
            style={{ color: "var(--color-text-muted)" }}
          >
            Ejemplo:{" "}
            <code
              style={{
                fontFamily: "var(--font-mono)",
                letterSpacing: "0.04em",
              }}
            >
              ES14H2600001
            </code>
          </p>
        </div>
      </div>
    );
  }

  // ── No encontrado en Soundcharts ──────────────────────────────────────────
  // Fix 13: "no encontrado" es un resultado rutinario, no un error de sistema.
  // Usa colores warning (ámbar) en lugar de danger (rojo). El rojo se reserva
  // para el estado "error" real (red caída, servidor no disponible).
  if (type === "not_found") {
    return (
      <div
        className="px-5 py-5 rounded-xl animate-reveal"
        role="alert"
        style={{
          background: "var(--color-warning-bg)",
          border: "1px solid var(--color-warning-border)",
        }}
      >
        <div className="flex items-start gap-3">
          <span
            style={{ color: "var(--color-warning)", flexShrink: 0, marginTop: 1 }}
          >
            <IconAlertCircle />
          </span>
          <div>
            <p
              className="text-sm font-semibold"
              style={{ color: "var(--color-warning-text)" }}
            >
              Soundcharts no reconoce{" "}
              <code
                style={{
                  fontFamily: "var(--font-mono)",
                  letterSpacing: "0.04em",
                }}
              >
                {isrc}
              </code>
            </p>
            <ul
              className="text-xs mt-2 flex flex-col gap-1"
              style={{ color: "var(--color-warning-text)" }}
            >
              <li>· ISRC mal escrito — verifica carácter a carácter.</li>
              <li>· Track recién publicado: Soundcharts tarda 24-48 h en indexarlo.</li>
              <li>· ISRC registrado pero aún sin distribución en DSPs.</li>
            </ul>
            {onRetry && (
              <button
                type="button"
                onClick={onRetry}
                className="btn btn-secondary text-xs mt-3"
              >
                Reintentar
              </button>
            )}
          </div>
        </div>
      </div>
    );
  }

  // ── Error de red o servidor ───────────────────────────────────────────────
  if (type === "error") {
    return (
      <div
        className="flex items-start justify-between gap-4 px-5 py-4 rounded-xl animate-reveal"
        role="alert"
        style={{
          background: "var(--color-danger-bg)",
          border: "1px solid var(--color-danger-border)",
        }}
      >
        <div>
          <p
            className="text-sm font-semibold"
            style={{ color: "var(--color-danger-text)" }}
          >
            Se produjo un problema
          </p>
          <p
            className="text-xs mt-1"
            style={{ color: "var(--color-danger-text)", opacity: 0.8 }}
          >
            {msg ?? "Error inesperado. Inténtalo de nuevo."}
          </p>
        </div>
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="btn btn-secondary text-xs whitespace-nowrap"
          >
            Reintentar
          </button>
        )}
      </div>
    );
  }

  // ── Track encontrado pero sin placements ──────────────────────────────────
  if (type === "no_placements") {
    return (
      <div
        className="flex flex-col items-center gap-3 px-5 py-8 rounded-xl text-center animate-reveal"
        role="status"
        style={{
          background: "var(--color-warning-bg)",
          border: "1px solid var(--color-warning-border)",
        }}
      >
        <span style={{ color: "var(--color-warning)" }}>
          <IconInbox />
        </span>
        <div>
          <p
            className="text-sm font-medium"
            style={{ color: "var(--color-warning-text)" }}
          >
            Sin placements en las plataformas consultadas
          </p>
          <p
            className="text-xs mt-1"
            style={{ color: "var(--color-warning-text)", opacity: 0.8 }}
          >
            Cambia a "Todas (9)" para ampliar la búsqueda a más DSPs.
          </p>
        </div>
        {onChangeScope && (
          <button
            type="button"
            onClick={onChangeScope}
            className="btn btn-secondary text-xs"
          >
            Buscar en todas las plataformas
          </button>
        )}
      </div>
    );
  }

  return null;
}
