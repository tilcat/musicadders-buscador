"use client";

/**
 * Página: Catálogo FUGA (F3)
 *
 * Busca ISRCs por rango de fechas de lanzamiento paginando la API de FUGA.
 * Como la búsqueda es larga (segundos a minutos), reutiliza el patrón de job
 * de fondo de la Fase 1 (batch): POST para iniciar, polling de estado con
 * progreso, y al terminar tabla de resultados + descargas.
 *
 * Estados del ciclo:
 *   idle         → panel de fechas vacío
 *   submitting   → panel de fechas deshabilitado (iniciando job)
 *   running      → FugaProgress (polling activo)
 *   done         → FugaResults (tabla completa + descargas)
 *   cancelled    → FugaResults (resultado parcial)
 *   error        → panel de fechas + banner de error
 *   no_credentials → aviso de configuración
 */

import { useState, useRef, useEffect } from "react";
import FugaDatePanel        from "@/components/fuga/FugaDatePanel";
import FugaProgress         from "@/components/fuga/FugaProgress";
import FugaResults          from "@/components/fuga/FugaResults";
import { useFugaPolling }   from "@/components/fuga/useFugaPolling";

// ── Helpers de fecha (local, no UTC) ─────────────────────────────────────────

function isoToday() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function isoLastMonth() {
  const d = new Date();
  // Resetear al día 1 del mes actual antes de restar un mes para evitar
  // overflow (ej. 31 de marzo → 31 de febrero → 3 de marzo en algunos motores).
  d.setDate(1);
  d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function daysDiff(from, to) {
  if (!from || !to) return 0;
  return Math.round((new Date(to) - new Date(from)) / (1000 * 60 * 60 * 24));
}

// ── Icono credenciales ────────────────────────────────────────────────────────

function IconLock({ size = 20 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
      <path d="M7 11V7a5 5 0 0 1 10 0v4" />
    </svg>
  );
}

// ── Estado: sin credenciales FUGA ─────────────────────────────────────────────

function NoCredentialsBanner() {
  return (
    <div
      className="flex items-start gap-3 px-5 py-5 rounded-xl animate-reveal"
      role="alert"
      style={{
        background: "var(--color-danger-bg)",
        border: "1px solid var(--color-danger-border)",
      }}
    >
      <span style={{ color: "var(--color-danger-text)", flexShrink: 0, marginTop: 1 }}>
        <IconLock />
      </span>
      <div>
        <p className="text-sm font-semibold" style={{ color: "var(--color-danger-text)" }}>
          Credenciales FUGA no configuradas
        </p>
        <p className="text-xs mt-1" style={{ color: "var(--color-danger-text)", opacity: 0.8 }}>
          Falta configurar <code style={{ fontFamily: "var(--font-mono)" }}>FUGA_USER</code> y{" "}
          <code style={{ fontFamily: "var(--font-mono)" }}>FUGA_PASS</code> en el servidor.
          Pide ayuda al administrador.
        </p>
      </div>
    </div>
  );
}

// ── Banner de error ───────────────────────────────────────────────────────────

function ErrorBanner({ msg, onReset }) {
  return (
    <div
      className="flex items-start justify-between gap-4 px-4 py-4 rounded-xl animate-reveal"
      role="alert"
      style={{
        background: "var(--color-danger-bg)",
        border: "1px solid var(--color-danger-border)",
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

// ── Página principal ──────────────────────────────────────────────────────────

export default function FugaPage() {
  // Fechas con defaults: mes pasado → hoy (igual que Streamlit), usando fecha local
  const [dateFrom, setDateFrom] = useState(isoLastMonth);
  const [dateTo,   setDateTo]   = useState(isoToday);

  const startedAtRef = useRef(null);

  const {
    estado,
    pagesDone,
    pagesTotal,
    statusText,
    isrcsFound,
    releasesFound,
    errorMsg,
    result,
    submit,
    cancel,
    reset,
    downloadUrl,
  } = useFugaPolling();

  // Capturar timestamp de arranque para ETA
  useEffect(() => {
    if (estado === "running" && !startedAtRef.current) {
      startedAtRef.current = Date.now();
    }
    if (estado === "idle") {
      startedAtRef.current = null;
    }
  }, [estado]);

  const rangeError    = !!dateFrom && !!dateTo && dateFrom > dateTo;
  const rangeTooBig   = !rangeError && !!dateFrom && !!dateTo && daysDiff(dateFrom, dateTo) > 366;
  const showDatePanel = ["idle", "error", "submitting"].includes(estado);
  const isLoading     = estado === "submitting";

  function handleSubmit() {
    if (rangeError || rangeTooBig || !dateFrom || !dateTo) return;
    submit(dateFrom, dateTo);
  }

  function handleReset() {
    reset();
    startedAtRef.current = null;
  }

  return (
    <div className="flex flex-col gap-6 max-w-[900px] animate-reveal">

      {/* ── Encabezado de página ──────────────────────────────────────────── */}
      <div className="animate-reveal">
        <h1
          className="text-xl font-semibold leading-tight"
          style={{ color: "var(--color-text)", letterSpacing: "-0.01em" }}
        >
          Catálogo FUGA
        </h1>
        <p className="text-sm mt-1" style={{ color: "var(--color-text-soft)" }}>
          Busca todos los ISRCs lanzados en FUGA dentro de un rango de fechas
          de lanzamiento. La consulta es en vivo — sin caché.
        </p>
      </div>

      {/* ── Sin credenciales ─────────────────────────────────────────────── */}
      {estado === "no_credentials" && <NoCredentialsBanner />}

      {/* ── Banner de error (ANTES del panel de fechas para visibilidad) ─── */}
      {estado === "error" && (
        <ErrorBanner msg={errorMsg} onReset={handleReset} />
      )}

      {/* ── Panel de fechas (idle · error · submitting) ──────────────────── */}
      {showDatePanel && (
        <FugaDatePanel
          dateFrom={dateFrom}
          dateTo={dateTo}
          onDateFrom={setDateFrom}
          onDateTo={setDateTo}
          onSubmit={handleSubmit}
          loading={isLoading}
          rangeError={rangeError}
          rangeTooBig={rangeTooBig}
        />
      )}

      {/* ── Progreso ─────────────────────────────────────────────────────── */}
      {estado === "running" && (
        <div
          className="p-5 rounded-xl"
          style={{
            background: "var(--color-surface)",
            border: "1px solid var(--color-border)",
            boxShadow: "var(--shadow-sm)",
          }}
        >
          <FugaProgress
            pagesDone={pagesDone}
            pagesTotal={pagesTotal}
            statusText={statusText}
            isrcsFound={isrcsFound}
            releasesFound={releasesFound}
            startedAt={startedAtRef.current}
            onCancel={cancel}
          />
        </div>
      )}

      {/* ── Resultados ───────────────────────────────────────────────────── */}
      {(estado === "done" || estado === "cancelled") && (
        <FugaResults
          estado={estado}
          result={result}
          downloadUrl={downloadUrl}
          onReset={handleReset}
        />
      )}
    </div>
  );
}
