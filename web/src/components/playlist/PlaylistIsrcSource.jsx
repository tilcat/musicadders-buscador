"use client";

/**
 * PlaylistIsrcSource — Selector de ISRCs: pegar a mano O subir Excel.
 *
 * Dos pestañas con selector pill:
 *   "Pegar ISRCs" — textarea con parseo live de ISRCs.
 *   "Subir Excel"  — BatchDropzone reutilizado; parsea XLSX client-side.
 *
 * El texto del textarea persiste al cambiar de tab (no se pierde si el usuario
 * va a Excel y vuelve). El archivo de Excel se limpia al pasar a "pegar".
 *
 * Props:
 *   isrcs   {string[]}           — array actual de ISRCs (gestionado en el padre)
 *   onIsrcs {(string[]) => void} — callback cuando cambia el conjunto
 */

import { useState } from "react";
import BatchDropzone from "@/components/batch/BatchDropzone";

// ── Parseo de ISRCs ───────────────────────────────────────────────────────────

// ISRC normalizado (sin guiones): 2 letras + 3 alfanum + 2 dígitos + 5 dígitos = 12 chars.
// La regex se aplica DESPUÉS de quitar guiones para aceptar "AA-BBB-12-34567" y "AABBB1234567".
const ISRC_RE = /\b([A-Z]{2}[A-Z0-9]{3}[0-9]{7})\b/gi;

// parseIsrcsFromText solo para compatibilidad con la llamada inicial de handleTabChange.
// La lógica real de parseo (con raw count) está inlined en los handlers.
function _parseIsrcsRaw(text) {
  const normalized = text.replace(/-/g, "");
  return [...normalized.matchAll(ISRC_RE)].map((m) => m[1].toUpperCase());
}

// ── Iconos ────────────────────────────────────────────────────────────────────

function IconEdit() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
      <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
    </svg>
  );
}

function IconUpload() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <polyline points="17 8 12 3 7 8" />
      <line x1="12" y1="3" x2="12" y2="15" />
    </svg>
  );
}

// ── Componente ────────────────────────────────────────────────────────────────

export default function PlaylistIsrcSource({ isrcs, onIsrcs }) {
  const [tab,         setTab]         = useState("paste");
  const [text,        setText]        = useState("");
  const [file,        setFile]        = useState(null);
  const [xlsxLoading, setXlsxLoading] = useState(false);
  const [rawCount,    setRawCount]    = useState(0);  // total antes de dedup

  const count = isrcs.length;
  const duplicates = rawCount > count ? rawCount - count : 0;

  function handleTextChange(e) {
    const val = e.target.value;
    setText(val);
    const all = _parseIsrcsRaw(val);
    setRawCount(all.length);
    onIsrcs([...new Set(all)]);
  }

  async function handleExcelFile(f) {
    setFile(f);
    setXlsxLoading(true);
    try {
      const buf  = await f.arrayBuffer();
      const wb   = (await import("xlsx")).read(buf, { type: "array" });
      const ws   = wb.Sheets[wb.SheetNames[0]];
      if (!ws) { onIsrcs([]); setRawCount(0); return; }

      const rows   = (await import("xlsx")).utils.sheet_to_json(ws, { header: 1, defval: "" });
      const header = (rows[0] ?? []).map((h) => String(h).trim().toLowerCase());
      const col    = Math.max(0, header.findIndex((h) => h === "isrc"));

      const all = rows
        .slice(1)
        .map((row) => String(row[col] ?? "").trim().replace(/-/g, "").toUpperCase())
        .filter((v) => /^[A-Z]{2}[A-Z0-9]{3}[0-9]{7}$/.test(v));

      setRawCount(all.length);
      onIsrcs([...new Set(all)]);
    } finally {
      setXlsxLoading(false);
    }
  }

  function handleExcelClear() {
    setFile(null);
    setRawCount(0);
    onIsrcs([]);
  }

  function handleTabChange(newTab) {
    if (newTab === tab) return;
    setTab(newTab);
    if (newTab === "paste") {
      // Quitar archivo, recuperar ISRCs del texto (que sigue en el textarea)
      setFile(null);
      setRawCount(0);
      const all = _parseIsrcsRaw(text);
      setRawCount(all.length);
      onIsrcs([...new Set(all)]);
    } else {
      // Pasar a Excel: limpiar ISRCs hasta que se suba un archivo.
      // El texto del textarea NO se borra — si el usuario vuelve a "pegar", lo recupera.
      setRawCount(0);
      onIsrcs([]);
    }
  }

  return (
    <div className="flex flex-col gap-3">

      {/* Selector de tab — pill doble */}
      <div
        className="flex gap-1 p-1 rounded-lg"
        style={{
          background: "var(--color-surface-raised)",
          border: "1px solid var(--color-border)",
        }}
      >
        {[
          { key: "paste", label: "Pegar ISRCs",  Icon: IconEdit   },
          { key: "excel", label: "Subir Excel",   Icon: IconUpload },
        ].map(({ key, label, Icon }) => {
          const active = tab === key;
          return (
            <button
              key={key}
              type="button"
              onClick={() => handleTabChange(key)}
              className="flex-1 flex items-center justify-center gap-1.5 py-1.5 text-xs font-medium rounded-md transition-all"
              style={{
                background:  active ? "var(--color-surface)" : "transparent",
                color:       active ? "var(--color-text)" : "var(--color-text-soft)",
                border:      active ? "1px solid var(--color-border)" : "1px solid transparent",
                boxShadow:   active ? "var(--shadow-sm)" : "none",
                cursor:      "pointer",
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

      {/* Panel: Pegar ISRCs */}
      {tab === "paste" && (
        <div className="flex flex-col gap-2">
          <textarea
            className="fuga-input"
            rows={5}
            placeholder={"Pega los ISRCs aquí, uno por línea o separados por comas.\nEj: GBAHS0000001\n    GBAHS0000002, GBAHS0000003"}
            value={text}
            onChange={handleTextChange}
            style={{
              fontFamily: "var(--font-mono)",
              fontSize: "12px",
              resize: "vertical",
              lineHeight: 1.7,
            }}
          />
          <p className="text-xs" style={{ color: "var(--color-text-muted)" }}>
            {count > 0 ? (
              <>
                <span style={{ color: "var(--color-accent)", fontWeight: 600 }}>{count}</span>
                {" ISRC"}{count !== 1 ? "s" : ""} válidos detectados
                {duplicates > 0 && (
                  <span style={{ marginLeft: 6, opacity: 0.7 }}>
                    · Se ignoraron {duplicates} duplicado{duplicates !== 1 ? "s" : ""}
                  </span>
                )}
              </>
            ) : (
              "Pega uno por línea, separados por comas o punto y coma"
            )}
          </p>
        </div>
      )}

      {/* Panel: Subir Excel */}
      {tab === "excel" && (
        <div className="flex flex-col gap-2">
          <BatchDropzone file={file} onFile={handleExcelFile} onClear={handleExcelClear} />
          {xlsxLoading && (
            <p className="text-xs" style={{ color: "var(--color-text-muted)" }}>
              Leyendo archivo…
            </p>
          )}
          {!xlsxLoading && file && count > 0 && (
            <p className="text-xs" style={{ color: "var(--color-text-muted)" }}>
              <span style={{ color: "var(--color-accent)", fontWeight: 600 }}>{count}</span>
              {" ISRC"}{count !== 1 ? "s" : ""} extraídos de la columna{" "}
              <code style={{ fontFamily: "var(--font-mono)", fontSize: "11px" }}>ISRC</code>
              {duplicates > 0 && (
                <span style={{ marginLeft: 6, opacity: 0.7 }}>
                  · Se ignoraron {duplicates} duplicado{duplicates !== 1 ? "s" : ""}
                </span>
              )}
            </p>
          )}
          {!xlsxLoading && file && count === 0 && (
            <p className="text-xs" style={{ color: "var(--color-warning-text)" }}>
              No se encontraron ISRCs válidos. Asegúrate de que el archivo tiene una columna{" "}
              <code style={{ fontFamily: "var(--font-mono)", fontSize: "11px" }}>ISRC</code>.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
