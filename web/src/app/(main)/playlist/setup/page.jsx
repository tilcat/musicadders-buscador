"use client";

/**
 * Página: Setup OAuth Spotify — /playlist/setup
 *
 * Conecta/desconecta la cuenta Spotify central que usa la herramienta para
 * crear playlists. Diseñada para ser discreta: no aparece en el Sidebar,
 * solo se accede desde el banner "no configurado" de /playlist.
 *
 * La protección de acceso (solo admin) debe hacerse en el servidor —
 * ver notas de integración para desarrollo.
 *
 * Endpoints:
 *   GET  /api/playlist/setup/status
 *     → { connected: boolean, account_name: string|null, expires_at: string|null }
 *
 *   GET  /api/playlist/setup/connect
 *     → redirect a Spotify OAuth (manejado server-side)
 *
 *   POST /api/playlist/setup/disconnect
 *     → revoke token y reset
 */

import { useState, useEffect, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";

// ── Iconos ────────────────────────────────────────────────────────────────────

function IconArrowLeft() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <line x1="19" y1="12" x2="5" y2="12" />
      <polyline points="12 19 5 12 12 5" />
    </svg>
  );
}

function IconCheck() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <polyline points="20 6 9 17 4 12" />
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

function IconMusic() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <path d="M9 18V5l12-2v13" />
      <circle cx="6" cy="18" r="3" />
      <circle cx="18" cy="16" r="3" />
    </svg>
  );
}

// ── Lógica con useSearchParams (debe estar dentro de Suspense) ────────────────

function SetupPageInner() {
  const searchParams = useSearchParams();

  const [status,        setStatus]        = useState(null);
  const [loading,       setLoading]       = useState(true);
  const [disconnecting, setDisconnecting] = useState(false);
  const [confirming,    setConfirming]    = useState(false);   // Fix 14: inline confirm
  const [errorMsg,      setErrorMsg]      = useState(null);
  const [isAdminError,  setIsAdminError]  = useState(false);   // Fix 11: acceso denegado

  // Fix 4: leer ?connected=1 y ?error= de los params OAuth callback
  const connectedParam = searchParams.get("connected");
  const errorParam     = searchParams.get("error");

  useEffect(() => {
    fetch("/api/playlist/setup/status")
      .then((r) => {
        if (r.status === 403) {
          // Fix 11: 403 → "Solo administradores"
          setIsAdminError(true);
          throw new Error("403");
        }
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((d) => setStatus(d))
      .catch((e) => {
        if (e.message !== "403") {
          setErrorMsg("No se pudo cargar el estado de la cuenta.");
        }
      })
      .finally(() => setLoading(false));
  }, []);

  // Fix 14: inline confirm — primer clic pide confirmación, segundo ejecuta
  function handleDisconnectClick() {
    if (!confirming) {
      setConfirming(true);
      return;
    }
    handleDisconnect();
  }

  function handleDisconnectCancel() {
    setConfirming(false);
  }

  async function handleDisconnect() {
    setConfirming(false);
    setDisconnecting(true);
    setErrorMsg(null);
    try {
      const res = await fetch("/api/playlist/setup/disconnect", { method: "POST" });
      if (!res.ok) throw new Error("No se pudo desconectar. Inténtalo de nuevo.");
      setStatus({ connected: false, account_name: null, expires_at: null });
    } catch (e) {
      setErrorMsg(e.message);
    } finally {
      setDisconnecting(false);
    }
  }

  return (
    // Fix 15: animate-reveal solo en hijos, no en el div raíz
    <div className="flex flex-col gap-6 max-w-[520px]">

      {/* ── Cabecera con breadcrumb ──────────────────────────────────────── */}
      <div className="animate-reveal">
        <Link
          href="/playlist"
          className="inline-flex items-center gap-1.5 text-xs mb-5"
          style={{ color: "var(--color-text-muted)", textDecoration: "none" }}
        >
          <IconArrowLeft />
          Volver a Crear playlist
        </Link>
        <h1
          className="text-xl font-semibold leading-tight"
          style={{ color: "var(--color-text)", letterSpacing: "-0.01em" }}
        >
          Cuenta Spotify central
        </h1>
        <p className="text-sm mt-1" style={{ color: "var(--color-text-soft)" }}>
          Esta cuenta se usa para crear todas las playlists desde la herramienta.
          Solo hay que configurarla una vez.
        </p>
      </div>

      {/* ── Fix 4: Banner de éxito OAuth ────────────────────────────────── */}
      {connectedParam === "1" && (
        <div
          className="flex items-center gap-3 px-4 py-3 rounded-xl animate-reveal"
          role="status"
          style={{
            background: "var(--color-accent-bg)",
            border:     "1px solid var(--color-success-border)",
          }}
        >
          <span style={{ color: "var(--color-accent)" }}><IconCheck /></span>
          <p className="text-sm font-medium" style={{ color: "var(--color-accent-hover)" }}>
            Cuenta Spotify conectada correctamente.
          </p>
        </div>
      )}

      {/* ── Fix 4: Banner de error OAuth ────────────────────────────────── */}
      {errorParam && (
        <div
          className="px-4 py-3 rounded-xl animate-reveal"
          role="alert"
          style={{
            background: "var(--color-danger-bg)",
            border:     "1px solid var(--color-danger-border)",
          }}
        >
          <p className="text-sm font-semibold" style={{ color: "var(--color-danger-text)" }}>
            Error al conectar la cuenta
          </p>
          <p className="text-xs mt-1" style={{ color: "var(--color-danger-text)", opacity: 0.85 }}>
            {decodeURIComponent(errorParam)}
          </p>
        </div>
      )}

      {/* ── Card de estado ───────────────────────────────────────────────── */}
      <div
        className="flex flex-col gap-5 p-5 rounded-xl animate-reveal animate-reveal-delay-1"
        style={{
          background: "var(--color-surface)",
          border:     "1px solid var(--color-border)",
          boxShadow:  "var(--shadow-sm)",
        }}
      >

        {/* Cargando */}
        {loading && (
          <div
            className="flex items-center gap-2 py-2 text-sm"
            style={{ color: "var(--color-text-muted)" }}
          >
            <IconLoader />
            Comprobando estado de la cuenta…
          </div>
        )}

        {/* Fix 11: Error de acceso — 403 */}
        {!loading && isAdminError && (
          <div
            className="flex flex-col gap-1 py-2"
            role="alert"
          >
            <p className="text-sm font-semibold" style={{ color: "var(--color-danger-text)" }}>
              Acceso restringido
            </p>
            <p className="text-sm" style={{ color: "var(--color-danger-text)", opacity: 0.85 }}>
              Solo administradores pueden acceder a esta página. Contacta a{" "}
              <a
                href="mailto:victor.gimenez@musicadders.com"
                style={{ color: "var(--color-danger-text)", fontWeight: 600 }}
              >
                victor.gimenez@musicadders.com
              </a>{" "}
              si necesitas acceso.
            </p>
          </div>
        )}

        {/* Error de carga (genérico) */}
        {!loading && !isAdminError && errorMsg && (
          <p className="text-sm" style={{ color: "var(--color-danger-text)" }}>
            {errorMsg}
          </p>
        )}

        {/* Cuenta conectada */}
        {!loading && !isAdminError && !errorMsg && status?.connected && (
          <>
            <div className="flex items-start gap-3">
              <div
                className="flex items-center justify-center rounded-full flex-shrink-0"
                style={{
                  width:      32,
                  height:     32,
                  background: "var(--color-accent-bg)",
                  border:     "1px solid var(--color-success-border)",
                }}
              >
                <span style={{ color: "var(--color-accent)" }}>
                  <IconCheck />
                </span>
              </div>
              <div>
                <p className="text-sm font-semibold" style={{ color: "var(--color-text)" }}>
                  Cuenta conectada
                </p>
                {status.account_name && (
                  <p className="text-xs mt-0.5" style={{ color: "var(--color-text-soft)" }}>
                    {status.account_name}
                  </p>
                )}
                {status.expires_at && (
                  <p
                    className="text-xs mt-1"
                    style={{ color: "var(--color-text-muted)", fontFamily: "var(--font-mono)" }}
                  >
                    Token válido hasta{" "}
                    {new Date(status.expires_at).toLocaleDateString("es", {
                      day: "2-digit", month: "short", year: "numeric",
                    })}
                  </p>
                )}
              </div>
            </div>

            <div style={{ height: 1, background: "var(--color-border)" }} />

            <div className="flex flex-col gap-3">
              <p className="text-xs" style={{ color: "var(--color-text-muted)" }}>
                Para usar otra cuenta, desconecta esta y vuelve a autenticar.
              </p>

              {/* Fix 14: inline confirm en lugar de window.confirm() */}
              {!confirming ? (
                <button
                  type="button"
                  onClick={handleDisconnectClick}
                  disabled={disconnecting}
                  className="btn btn-danger self-start"
                >
                  {disconnecting
                    ? <><IconLoader /> Desconectando…</>
                    : "Desconectar cuenta"}
                </button>
              ) : (
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-xs" style={{ color: "var(--color-danger-text)" }}>
                    ¿Seguro? Las playlists ya creadas no se eliminarán.
                  </span>
                  <button
                    type="button"
                    onClick={handleDisconnect}
                    disabled={disconnecting}
                    className="btn btn-danger"
                  >
                    Confirmar desconexión
                  </button>
                  <button
                    type="button"
                    onClick={handleDisconnectCancel}
                    className="btn btn-secondary"
                  >
                    Cancelar
                  </button>
                </div>
              )}
            </div>
          </>
        )}

        {/* No conectada */}
        {!loading && !isAdminError && !errorMsg && !status?.connected && (
          <>
            <div>
              <p className="text-sm font-medium" style={{ color: "var(--color-text)" }}>
                No hay cuenta conectada
              </p>
              <p className="text-xs mt-1.5" style={{ color: "var(--color-text-muted)" }}>
                Al hacer clic serás redirigido a Spotify para autorizar el acceso.
                Solo es necesario hacerlo una vez y el token se renueva automáticamente.
              </p>
            </div>

            <a
              href="/api/playlist/setup/connect"
              className="btn btn-primary self-start flex items-center gap-2"
            >
              <IconMusic />
              Conectar con Spotify
            </a>
          </>
        )}
      </div>

      {/* ── Nota de privacidad ───────────────────────────────────────────── */}
      {!isAdminError && (
        <p
          className="text-xs animate-reveal animate-reveal-delay-2"
          style={{ color: "var(--color-text-muted)", lineHeight: 1.6 }}
        >
          El token OAuth se almacena de forma segura en el servidor y nunca llega
          al navegador. Las playlists creadas pertenecerán a la cuenta autorizada.
        </p>
      )}
    </div>
  );
}

// ── Wrapper con Suspense (requerido por useSearchParams en Next.js App Router) ─

export default function PlaylistSetupPage() {
  return (
    <Suspense fallback={null}>
      <SetupPageInner />
    </Suspense>
  );
}
