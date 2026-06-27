"use client";

/**
 * Página: Crear playlist Spotify (F4)
 *
 * Ciclo de vida de estados:
 *   idle           → formulario completo (fuente ISRCs + campos playlist)
 *   submitting     → formulario deshabilitado, iniciando job
 *   running        → PlaylistProgress con polling activo
 *   cooldown       → PlaylistCooldown (Spotify en penalty-box, job sigue en cola)
 *   done           → PlaylistResult (playlist creada, enlace + KPIs)
 *   cancelled      → PlaylistResult (resultado parcial)
 *   error          → banner de error + formulario para reintentar
 *   not_configured → banner "cuenta no conectada" con link a /playlist/setup
 *
 * Modo: CUENTA CENTRAL. El usuario NO hace OAuth con Spotify; la playlist se
 * crea en la cuenta configurada en el servidor. La pantalla de setup está en
 * /playlist/setup y no aparece en el nav — solo se accede desde el banner
 * de "no configurado".
 */

import { useState, useRef, useEffect } from "react";
import Link from "next/link";
import PlaylistIsrcSource    from "@/components/playlist/PlaylistIsrcSource";
import PlaylistForm          from "@/components/playlist/PlaylistForm";
import PlaylistProgress      from "@/components/playlist/PlaylistProgress";
import PlaylistCooldown      from "@/components/playlist/PlaylistCooldown";
import PlaylistResult        from "@/components/playlist/PlaylistResult";
import { usePlaylistPolling } from "@/components/playlist/usePlaylistPolling";

// ── Iconos ────────────────────────────────────────────────────────────────────

function IconSettings({ size = 14 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

function IconChevronRight({ size = 11 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <polyline points="9 18 15 12 9 6" />
    </svg>
  );
}

// ── Estados especiales ────────────────────────────────────────────────────────

function NotConfiguredBanner() {
  return (
    <div
      className="flex items-start gap-3 px-5 py-5 rounded-xl animate-reveal"
      role="alert"
      style={{
        background: "var(--color-warning-bg)",
        border:     "1px solid var(--color-warning-border)",
      }}
    >
      <span
        style={{
          color:     "var(--color-warning-text)",
          flexShrink: 0,
          marginTop:  2,
        }}
      >
        <IconSettings size={18} />
      </span>
      <div className="flex flex-col gap-2">
        <p className="text-sm font-semibold" style={{ color: "var(--color-warning-text)" }}>
          Cuenta Spotify central no configurada
        </p>
        <p className="text-xs" style={{ color: "var(--color-warning-text)", opacity: 0.85 }}>
          Para crear playlists es necesario conectar una cuenta Spotify. Solo hay que
          hacerlo una vez.
        </p>
        <Link
          href="/playlist/setup"
          className="inline-flex items-center gap-1 text-xs font-semibold self-start mt-0.5"
          style={{
            color:             "var(--color-warning-text)",
            textDecoration:    "underline",
            textUnderlineOffset: "2px",
          }}
        >
          Configurar cuenta Spotify
          <IconChevronRight />
        </Link>
      </div>
    </div>
  );
}

function ErrorBanner({ msg, onReset }) {
  return (
    <div
      className="flex items-start justify-between gap-4 px-4 py-4 rounded-xl animate-reveal"
      role="alert"
      style={{
        background: "var(--color-danger-bg)",
        border:     "1px solid var(--color-danger-border)",
      }}
    >
      <div>
        <p className="text-sm font-semibold" style={{ color: "var(--color-danger-text)" }}>
          Se produjo un problema
        </p>
        <p className="text-xs mt-1" style={{ color: "var(--color-danger-text)", opacity: 0.8 }}>
          {msg ?? "Error inesperado. Inténtalo de nuevo."}
        </p>
      </div>
      <button
        type="button"
        onClick={onReset}
        className="btn btn-secondary text-xs whitespace-nowrap"
      >
        Reintentar
      </button>
    </div>
  );
}

// ── Página ────────────────────────────────────────────────────────────────────

export default function PlaylistPage() {
  const [isrcs,       setIsrcs]       = useState([]);
  const [name,        setName]        = useState(() => {
    // Fix 18: nombre por defecto con la fecha de hoy
    const d = new Date();
    const yyyy = d.getFullYear();
    const mm   = String(d.getMonth() + 1).padStart(2, "0");
    const dd   = String(d.getDate()).padStart(2, "0");
    return `Musicadders selección · ${yyyy}-${mm}-${dd}`;
  });
  const [description, setDescription] = useState("");
  const [isPublic,    setIsPublic]    = useState(false);   // privada por defecto

  const startedAtRef = useRef(null);

  const {
    estado,
    phase,
    resolved,
    total,
    added,
    notFound,
    progressPct,
    statusText,
    cooldownUntil,
    errorMsg,
    result,
    submit,
    cancel,
    reset,
    downloadUrl,
  } = usePlaylistPolling();

  // Capturar timestamp de arranque para ETA en PlaylistProgress
  useEffect(() => {
    if ((estado === "running" || estado === "cooldown") && !startedAtRef.current) {
      startedAtRef.current = Date.now();
    }
    if (estado === "idle") {
      startedAtRef.current = null;
    }
  }, [estado]);

  const canSubmit = isrcs.length > 0 && name.trim().length > 0;
  const isLoading = estado === "submitting";
  const showForm  = ["idle", "error", "submitting"].includes(estado);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!canSubmit || isLoading) return;
    await submit({ isrcs, name: name.trim(), description: description.trim(), isPublic });
  }

  function handleReset() {
    reset();
    startedAtRef.current = null;
  }

  return (
    <div className="flex flex-col gap-6 max-w-[760px]">

      {/* ── Encabezado ───────────────────────────────────────────────────── */}
      <div className="animate-reveal">
        <h1
          className="text-xl font-semibold leading-tight"
          style={{ color: "var(--color-text)", letterSpacing: "-0.01em" }}
        >
          Crear playlist Spotify
        </h1>
        <p className="text-sm mt-1" style={{ color: "var(--color-text-soft)" }}>
          Pega ISRCs o sube un Excel, ponle nombre y la playlist aparecerá en la
          cuenta Spotify central.
        </p>
      </div>

      {/* ── No configurado ───────────────────────────────────────────────── */}
      {estado === "not_configured" && <NotConfiguredBanner />}

      {/* ── Error (encima del formulario para visibilidad) ───────────────── */}
      {estado === "error" && (
        <ErrorBanner msg={errorMsg} onReset={handleReset} />
      )}

      {/* ── Formulario (idle · error · submitting) ───────────────────────── */}
      {showForm && (
        <form onSubmit={handleSubmit} className="flex flex-col gap-5">
          <div
            className="flex flex-col gap-5 p-5 rounded-xl animate-reveal animate-reveal-delay-1"
            style={{
              background: "var(--color-surface)",
              border:     "1px solid var(--color-border)",
              boxShadow:  "var(--shadow-sm)",
            }}
          >
            {/* Fuente de ISRCs */}
            <div>
              <p
                className="text-xs font-semibold uppercase tracking-wide mb-2"
                style={{ color: "var(--color-text-soft)" }}
              >
                ISRCs
              </p>
              <PlaylistIsrcSource isrcs={isrcs} onIsrcs={setIsrcs} />
            </div>

            <div style={{ height: 1, background: "var(--color-border)" }} />

            {/* Datos de la playlist */}
            <PlaylistForm
              name={name}
              description={description}
              isPublic={isPublic}
              onName={setName}
              onDesc={setDescription}
              onPublic={setIsPublic}
            />

            <div style={{ height: 1, background: "var(--color-border)" }} />

            {/* Enviar */}
            <div className="flex items-center gap-3 flex-wrap">
              <button
                type="submit"
                disabled={!canSubmit || isLoading}
                className="btn btn-primary"
              >
                {isLoading ? (
                  <>
                    <svg
                      width="14" height="14" viewBox="0 0 24 24" fill="none"
                      stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
                      style={{ animation: "spin 0.8s linear infinite" }}
                      aria-hidden="true"
                    >
                      <path d="M21 12a9 9 0 1 1-6.219-8.56" />
                    </svg>
                    Iniciando…
                  </>
                ) : isrcs.length > 0 ? (
                  `Crear playlist · ${isrcs.length.toLocaleString("es")} ISRC${isrcs.length !== 1 ? "s" : ""}`
                ) : (
                  "Crear playlist"
                )}
              </button>

              {!canSubmit && !isLoading && (
                <p className="text-xs" style={{ color: "var(--color-text-muted)" }}>
                  {isrcs.length === 0
                    ? "Añade al menos 1 ISRC"
                    : "Introduce un nombre para la playlist"}
                </p>
              )}
            </div>
          </div>
        </form>
      )}

      {/* ── Progreso ─────────────────────────────────────────────────────── */}
      {estado === "running" && (
        <div
          className="p-5 rounded-xl"
          style={{
            background: "var(--color-surface)",
            border:     "1px solid var(--color-border)",
            boxShadow:  "var(--shadow-sm)",
          }}
        >
          <PlaylistProgress
            phase={phase}
            resolved={resolved}
            total={total}
            added={added}
            notFound={notFound}
            progressPct={progressPct}
            statusText={statusText}
            startedAt={startedAtRef.current}
            onCancel={cancel}
          />
        </div>
      )}

      {/* ── Cooldown (Spotify rate-limit, estado propio no error) ─────────── */}
      {estado === "cooldown" && (
        <PlaylistCooldown
          cooldownUntil={cooldownUntil}
          onCancel={cancel}
        />
      )}

      {/* ── Resultado ────────────────────────────────────────────────────── */}
      {(estado === "done" || estado === "cancelled") && (
        <PlaylistResult
          estado={estado}
          result={result}
          downloadUrl={downloadUrl}
          onReset={handleReset}
        />
      )}
    </div>
  );
}
