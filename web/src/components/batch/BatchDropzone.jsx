"use client";

/**
 * BatchDropzone — Zona drag-and-drop para Excel/CSV de batch ISRC.
 *
 * Acepta .xlsx, .xls, .csv. Valida formato en cliente; errores claros sin jerga.
 *
 * Props:
 *   file    {File|null}      — archivo actualmente seleccionado
 *   onFile  {(File) => void} — callback cuando se elige un archivo válido
 *   onClear {() => void}     — callback para quitar el archivo
 */

import { useRef, useState } from "react";

const ACCEPTED = [".xlsx", ".xls", ".csv"];
const ACCEPT_ATTR = ".xlsx,.xls,.csv,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,text/csv";

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1048576).toFixed(1)} MB`;
}

function isValidExtension(name) {
  const lower = name.toLowerCase();
  return ACCEPTED.some((ext) => lower.endsWith(ext));
}

// ── Iconos inline (sin lucide-react para no añadir dependencia en este archivo) ─

function IconUpload() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="17 8 12 3 7 8" />
      <line x1="12" y1="3" x2="12" y2="15" />
    </svg>
  );
}

function IconFile() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <polyline points="14 2 14 8 20 8" />
      <line x1="16" y1="13" x2="8" y2="13" />
      <line x1="16" y1="17" x2="8" y2="17" />
    </svg>
  );
}

function IconX() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.5" strokeLinecap="round" aria-hidden="true">
      <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  );
}

function IconAlertTriangle() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
      <line x1="12" y1="9" x2="12" y2="13" />
      <line x1="12" y1="17" x2="12.01" y2="17" />
    </svg>
  );
}

// ── Componente ────────────────────────────────────────────────────────────────

export default function BatchDropzone({ file, onFile, onClear }) {
  const inputRef = useRef(null);
  const [dragging, setDragging] = useState(false);
  const [typeError, setTypeError] = useState(false);

  function handleFile(f) {
    if (!f) return;
    if (!isValidExtension(f.name)) {
      setTypeError(true);
      return;
    }
    setTypeError(false);
    onFile(f);
  }

  function handleDrop(e) {
    e.preventDefault();
    setDragging(false);
    const transferred = e.dataTransfer.files;
    if (!transferred?.length) return;
    if (transferred.length > 1) {
      setTypeError(true);
      return;
    }
    handleFile(transferred[0]);
  }

  // Estado: archivo seleccionado
  if (file) {
    return (
      <div
        className="flex items-center gap-3 px-4 py-3 rounded-xl"
        style={{
          background: "var(--color-accent-bg)",
          border: "1px solid var(--color-success-border)",
        }}
      >
        <span style={{ color: "var(--color-accent)", flexShrink: 0 }}>
          <IconFile />
        </span>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium truncate" style={{ color: "var(--color-text)" }}>
            {file.name}
          </p>
          <p className="text-xs" style={{ color: "var(--color-text-soft)" }}>
            {formatBytes(file.size)}
          </p>
        </div>
        <button
          type="button"
          onClick={() => { setTypeError(false); onClear(); }}
          className="flex items-center gap-1 text-xs px-2.5 py-1.5 rounded-md transition-colors"
          style={{
            border: "1px solid var(--color-border)",
            color: "var(--color-text-soft)",
            background: "var(--color-surface)",
            cursor: "pointer",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.color = "var(--color-danger)";
            e.currentTarget.style.borderColor = "var(--color-danger-border)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.color = "var(--color-text-soft)";
            e.currentTarget.style.borderColor = "var(--color-border)";
          }}
        >
          <IconX /> Quitar
        </button>
      </div>
    );
  }

  // Estado: vacío / error
  return (
    <div>
      <div
        role="button"
        tabIndex={0}
        aria-label="Zona de carga de archivo Excel o CSV"
        onClick={() => { setTypeError(false); inputRef.current?.click(); }}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") inputRef.current?.click(); }}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        className="flex flex-col items-center gap-2.5 cursor-pointer transition-all rounded-xl py-8 px-4"
        style={{
          border: `2px dashed ${
            typeError
              ? "var(--color-danger-border)"
              : dragging
              ? "var(--color-accent)"
              : "var(--color-border)"
          }`,
          background: typeError
            ? "var(--color-danger-bg)"
            : dragging
            ? "var(--color-accent-bg)"
            : "var(--color-surface)",
        }}
      >
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT_ATTR}
          className="sr-only"
          onChange={(e) => {
            handleFile(e.target.files?.[0] ?? null);
            e.target.value = "";
          }}
        />

        {/* Icono */}
        <span
          style={{
            color: typeError
              ? "var(--color-danger)"
              : dragging
              ? "var(--color-accent)"
              : "var(--color-text-muted)",
          }}
        >
          {typeError ? <IconAlertTriangle /> : <IconUpload />}
        </span>

        {/* Texto principal */}
        <p
          className="text-sm font-medium text-center"
          style={{
            color: typeError
              ? "var(--color-danger)"
              : dragging
              ? "var(--color-accent)"
              : "var(--color-text-soft)",
          }}
        >
          {typeError
            ? "Formato no válido — solo .xlsx, .xls o .csv"
            : "Arrastra el archivo Excel o CSV, o haz clic para seleccionar"}
        </p>

        {/* Hint */}
        {!typeError && (
          <p className="text-xs text-center" style={{ color: "var(--color-text-muted)" }}>
            Necesita una columna{" "}
            <code
              className="px-1 rounded"
              style={{
                fontFamily: "var(--font-mono)",
                background: "var(--color-surface-raised)",
                fontSize: "11px",
              }}
            >
              ISRC
            </code>{" "}
            (o una por línea en CSV) · máx. 500 ISRCs
          </p>
        )}
      </div>
    </div>
  );
}
