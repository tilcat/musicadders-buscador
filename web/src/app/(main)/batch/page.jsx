"use client";

/**
 * Página: Procesar Excel (batch ISRC → Soundcharts)
 *
 * Gestiona los cuatro estados del flujo: idle / processing / done / cancelled.
 * Todas las llamadas al backend pasan por el proxy /api/batch/* (mismo origen).
 * El INTERNAL_TOKEN nunca llega al browser.
 */

import { useState, useRef, useEffect } from "react";
import BatchDropzone     from "@/components/batch/BatchDropzone";
import PlatformSelector  from "@/components/batch/PlatformSelector";
import BatchProgress     from "@/components/batch/BatchProgress";
import BatchResults      from "@/components/batch/BatchResults";
import { useBatchPolling } from "@/components/batch/useBatchPolling";

const DEFAULT_PLATFORMS = ["spotify", "apple-music", "amazon", "deezer"];

function IconInfo({ size = 14 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="10" />
      <line x1="12" y1="8" x2="12" y2="12" />
      <line x1="12" y1="16" x2="12.01" y2="16" />
    </svg>
  );
}

function LargeBatchNotice({ count, platforms }) {
  const estimatedMins = Math.ceil((count * platforms.length * 1.5) / 60);
  if (count < 50) return null;
  return (
    <div
      className="flex items-start gap-2.5 px-4 py-3 rounded-xl text-xs"
      style={{
        background: "var(--color-warning-bg)",
        border: "1px solid var(--color-warning-border)",
        color: "var(--color-warning-text)",
      }}
      role="status"
    >
      <span style={{ flexShrink: 0, marginTop: 1 }}><IconInfo size={13} /></span>
      <span>
        <strong>{count} ISRCs</strong> en {platforms.length} plataformas — estimado{" "}
        <strong>~{estimatedMins} min</strong>. El progreso persiste si recargas la página.
        {count >= 300 && (
          <> Lotes grandes consumen créditos de la API de Soundcharts: revisa tu cuota.</>
        )}
      </span>
    </div>
  );
}

function ErrorBanner({ msg, onReset }) {
  return (
    <div
      className="flex items-start justify-between gap-4 px-4 py-4 rounded-xl animate-reveal"
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
      <button type="button" onClick={onReset} className="btn btn-secondary text-xs whitespace-nowrap">
        Reintentar
      </button>
    </div>
  );
}

export default function BatchPage() {
  const [file, setFile]           = useState(null);
  const [platforms, setPlatforms] = useState(DEFAULT_PLATFORMS);
  const [isrcCount, setIsrcCount] = useState(0);
  const startedAtRef              = useRef(null);

  const {
    estado,
    hechos,
    total,
    callsUsed,
    notFoundCount,
    errorMsg,
    result,
    submit,
    cancel,
    reset,
    downloadUrl,
  } = useBatchPolling();

  useEffect(() => {
    if (estado === "processing" && !startedAtRef.current) {
      startedAtRef.current = Date.now();
    }
    if (estado === "idle") {
      startedAtRef.current = null;
    }
  }, [estado]);

  async function handleFile(f) {
    setFile(f);
    if (f.name.toLowerCase().endsWith(".csv")) {
      const text = await f.slice(0, 4096).text().catch(() => "");
      const lines = text.split("\n").length;
      setIsrcCount(Math.max(0, lines - 1));
    } else {
      setIsrcCount(0);
    }
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!file || !platforms.length) return;
    await submit(file, platforms);
  }

  function handleReset() {
    setFile(null);
    setIsrcCount(0);
    reset();
  }

  return (
    <div className="flex flex-col gap-6 max-w-[900px] animate-reveal">

      <div className="animate-reveal">
        <h1
          className="text-xl font-semibold leading-tight"
          style={{ color: "var(--color-text)", letterSpacing: "-0.01em" }}
        >
          Procesar Excel
        </h1>
        <p className="text-sm mt-1" style={{ color: "var(--color-text-soft)" }}>
          Sube un archivo con ISRCs y consulta en cuántas playlists aparecen en Soundcharts.
        </p>
      </div>

      {(estado === "idle" || estado === "uploading") && (
        <form onSubmit={handleSubmit} className="flex flex-col gap-5">
          <div
            className="flex flex-col gap-5 p-5 rounded-xl animate-reveal animate-reveal-delay-1"
            style={{
              background: "var(--color-surface)",
              border: "1px solid var(--color-border)",
              boxShadow: "var(--shadow-sm)",
            }}
          >
            <div>
              <label
                className="block text-xs font-semibold uppercase tracking-wide mb-2"
                style={{ color: "var(--color-text-soft)" }}
              >
                Archivo
              </label>
              <BatchDropzone
                file={file}
                onFile={handleFile}
                onClear={() => { setFile(null); setIsrcCount(0); }}
              />
            </div>

            <div style={{ height: 1, background: "var(--color-border)" }} />

            <div>
              <PlatformSelector value={platforms} onChange={setPlatforms} />
            </div>

            {file && <LargeBatchNotice count={isrcCount || total} platforms={platforms} />}

            <div className="flex items-center gap-3">
              <button
                type="submit"
                disabled={!file || platforms.length === 0 || estado === "uploading"}
                className="btn btn-primary"
              >
                {estado === "uploading" ? (
                  <>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                      stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
                      style={{ animation: "spin 0.8s linear infinite" }} aria-hidden="true">
                      <path d="M21 12a9 9 0 1 1-6.219-8.56" />
                    </svg>
                    Subiendo…
                  </>
                ) : (
                  "Procesar"
                )}
              </button>

              {!file && (
                <p className="text-xs" style={{ color: "var(--color-text-muted)" }}>
                  Selecciona un archivo para continuar
                </p>
              )}
            </div>
          </div>
        </form>
      )}

      {estado === "processing" && (
        <>
          {/* Aviso de lote grande visible también en Excel (total llega del primer poll) */}
          <LargeBatchNotice count={total} platforms={platforms} />
          <div
            className="p-5 rounded-xl"
            style={{
              background: "var(--color-surface)",
              border: "1px solid var(--color-border)",
              boxShadow: "var(--shadow-sm)",
            }}
          >
            <BatchProgress
              hechos={hechos}
              total={total}
              callsUsed={callsUsed}
              notFoundCount={notFoundCount}
              startedAt={startedAtRef.current}
              onCancel={cancel}
            />
          </div>
        </>
      )}

      {estado === "error" && (
        <ErrorBanner msg={errorMsg} onReset={handleReset} />
      )}

      {(estado === "done" || estado === "cancelled") && (
        <BatchResults
          estado={estado}
          hechos={hechos}
          total={total}
          callsUsed={callsUsed}
          notFoundCount={notFoundCount}
          result={result}
          onReset={handleReset}
          downloadUrl={downloadUrl}
        />
      )}
    </div>
  );
}
