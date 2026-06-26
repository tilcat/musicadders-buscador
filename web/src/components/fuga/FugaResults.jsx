"use client";

/**
 * FugaResults — Panel de resultados del catálogo FUGA.
 *
 * Estructura (en orden):
 *   1. Banner resumen (verde si done, ámbar si cancelled) + botón Nueva búsqueda.
 *   2. Bloque de filtros de texto libre: Artista / Sello / Release (filtrado client-side).
 *   3. Botones de descarga: Excel completo · Excel solo ISRC · CSV completo.
 *      NOTA: las descargas apuntan al endpoint del servidor (resultado completo).
 *      El filtro es puramente client-side; ver notas de integración para desarrollo.
 *   4. Tabla densa (TanStack): ISRC / Release / Artista / Sello / Fecha — con sort +
 *      paginación. Filas zebrastripe, hover accent-subtle, igual que BatchResults.
 *
 * Props:
 *   estado      {"done" | "cancelled"}
 *   result      {{ rows, date_from, date_to, isrcs_total, releases_total } | null}
 *   downloadUrl {(fmt: string) => string | null}
 *   onReset     {() => void}
 */

import { useState, useMemo } from "react";
import * as XLSX from "xlsx";
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getPaginationRowModel,
  flexRender,
} from "@tanstack/react-table";

// ── Utilidades ────────────────────────────────────────────────────────────────

function normalizeStr(s) {
  return String(s ?? "")
    .toLowerCase()
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "");
}

// ── Iconos ────────────────────────────────────────────────────────────────────

function IconDownload({ size = 14 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none"
      stroke="currentColor" strokeWidth="1.75" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <path d="M8 2v8M5 7l3 3 3-3" />
      <path d="M2 12h12" />
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

function SortIcon({ dir }) {
  if (!dir) return (
    <svg width="10" height="10" viewBox="0 0 10 10" fill="none"
      stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
      <path d="M3 4l2-2 2 2M3 6l2 2 2-2" />
    </svg>
  );
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" fill="none"
      stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" aria-hidden="true"
      style={{ transform: dir === "asc" ? "rotate(180deg)" : undefined }}>
      <path d="M5 2v6M2 5l3 3 3-3" />
    </svg>
  );
}

// ── Columnas TanStack ─────────────────────────────────────────────────────────
//
// meta.cellClass → className del <td> en el render de la tabla.
// meta.align     → "right" para columnas numéricas/fecha (único inline que queda).
// Los estilos tipográficos van en globals.css (.cell-code, .cell-text, .fuga-col-soft).

const FUGA_COLUMNS = [
  {
    id: "isrc",
    header: "ISRC",
    accessorKey: "isrc",
    meta: { cellClass: "cell-code" },
    cell: (info) =>
      info.getValue() || <span style={{ color: "var(--color-text-muted)" }}>—</span>,
  },
  {
    id: "product_name",
    header: "Release",
    accessorKey: "product_name",
    meta: { cellClass: "cell-text" },
    cell: (info) => (
      <span title={info.getValue() ?? ""}>
        {info.getValue() || <span style={{ color: "var(--color-text-muted)" }}>—</span>}
      </span>
    ),
  },
  {
    id: "artist_name",
    header: "Artista",
    accessorKey: "artist_name",
    meta: { cellClass: "cell-text" },
    cell: (info) => (
      <span title={info.getValue() ?? ""}>
        {info.getValue() || <span style={{ color: "var(--color-text-muted)" }}>—</span>}
      </span>
    ),
  },
  {
    id: "label",
    header: "Sello",
    accessorKey: "label",
    meta: { cellClass: "cell-text fuga-col-soft" },
    cell: (info) => (
      <span title={info.getValue() ?? ""}>
        {info.getValue() || <span style={{ color: "var(--color-text-muted)" }}>—</span>}
      </span>
    ),
  },
  {
    id: "release_date",
    header: "Fecha lanzamiento",
    accessorKey: "release_date",
    sortingFn: "alphanumeric",
    meta: { cellClass: "cell-text fuga-col-soft", align: "right" },
    cell: (info) => info.getValue() || "—",
  },
];

// ── Tabla FUGA (TanStack) ─────────────────────────────────────────────────────
//
// Usa las clases CSS de globals.css:
//   .fuga-table-wrapper   — borde + radio + clip overflow
//   .fuga-table-scroll    — overflow-x: auto (separado del wrapper para que el footer
//                           no scrolle lateralmente con la tabla)
//   .fuga-table           — tabla base (colapso, font-size, min-width)
//   .fuga-table thead     — sticky header (CSS)
//   .fuga-table thead th  — padding, tipografía, borde inferior (CSS)
//   .is-sortable          — cursor: pointer sobre th (CSS)
//   .fuga-th-inner        — inline-flex para texto + icono de sort (CSS)
//   .fuga-row-even        — background zebra par (CSS, en lugar de nth-child)
//   .fuga-table tbody tr:hover — hover accent (CSS, reemplaza onMouseEnter/Leave)
//   .cell-code            — mono, letter-spacing, user-select:all (ISRC)
//   .cell-text            — truncado con ellipsis (Release, Artista, Sello, Fecha)
//   .fuga-col-soft        — color text-soft para columnas secundarias
//   .fuga-empty-cell      — celda "sin resultados"
//   .fuga-table-footer    — footer paginación

function FugaResultsTable({ rows }) {
  const [sorting, setSorting]       = useState([{ id: "release_date", desc: true }]);
  const [pagination, setPagination] = useState({ pageIndex: 0, pageSize: 100 });

  const table = useReactTable({
    data: rows,
    columns: FUGA_COLUMNS,
    state: { sorting, pagination },
    onSortingChange: (updater) => {
      setSorting(updater);
      setPagination((p) => ({ ...p, pageIndex: 0 }));
    },
    onPaginationChange: setPagination,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
  });

  const { pageIndex, pageSize } = table.getState().pagination;
  const total = rows.length;
  const from  = total === 0 ? 0 : pageIndex * pageSize + 1;
  const to    = Math.min((pageIndex + 1) * pageSize, total);

  return (
    <div className="fuga-table-wrapper">
      <div className="fuga-table-scroll">
        <table className="fuga-table">
          <thead>
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((header) => {
                  const meta    = header.column.columnDef.meta ?? {};
                  const canSort = header.column.getCanSort();
                  const sorted  = header.column.getIsSorted();
                  return (
                    <th
                      key={header.id}
                      scope="col"
                      className={canSort ? "is-sortable" : undefined}
                      onClick={canSort ? header.column.getToggleSortingHandler() : undefined}
                      style={meta.align ? { textAlign: meta.align } : undefined}
                    >
                      <span className="fuga-th-inner">
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        {canSort && <SortIcon dir={sorted || null} />}
                      </span>
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>

          <tbody>
            {table.getRowModel().rows.length === 0 ? (
              <tr>
                <td colSpan={FUGA_COLUMNS.length} className="fuga-empty-cell">
                  Sin resultados para estos filtros
                </td>
              </tr>
            ) : (
              table.getRowModel().rows.map((row, i) => (
                <tr
                  key={row.id}
                  className={i % 2 !== 0 ? "fuga-row-even" : undefined}
                >
                  {row.getVisibleCells().map((cell) => {
                    const meta = cell.column.columnDef.meta ?? {};
                    return (
                      <td
                        key={cell.id}
                        className={meta.cellClass ?? undefined}
                        style={meta.align ? { textAlign: meta.align } : undefined}
                      >
                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                      </td>
                    );
                  })}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {/* Footer: paginación */}
      <div className="fuga-table-footer">
        <span>
          {total === 0
            ? "Sin resultados"
            : `Mostrando ${from}–${to} de ${total.toLocaleString("es")} ISRCs`}
        </span>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-1.5" style={{ color: "var(--color-text-muted)" }}>
            <span>Filas:</span>
            <select
              value={pageSize}
              onChange={(e) => { table.setPageSize(Number(e.target.value)); table.setPageIndex(0); }}
              className="text-xs rounded py-[3px] px-1.5"
              style={{
                border: "1px solid var(--color-border)",
                background: "var(--color-surface)",
                color: "var(--color-text)",
                fontFamily: "var(--font-sans)",
                outline: "none",
                cursor: "pointer",
              }}
            >
              {[50, 100, 200, 500].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </label>
          <div className="flex items-center gap-2">
            <button
              onClick={() => table.previousPage()}
              disabled={!table.getCanPreviousPage()}
              className="px-2.5 py-1 rounded transition-colors disabled:opacity-40"
              style={{
                border: "1px solid var(--color-border)",
                color: "var(--color-text-soft)",
                background: "transparent",
                cursor: table.getCanPreviousPage() ? "pointer" : "default",
              }}
            >
              ‹ Anterior
            </button>
            <span style={{ fontFamily: "var(--font-mono)", color: "var(--color-text-muted)", whiteSpace: "nowrap" }}>
              {pageIndex + 1} / {table.getPageCount() || 1}
            </span>
            <button
              onClick={() => table.nextPage()}
              disabled={!table.getCanNextPage()}
              className="px-2.5 py-1 rounded transition-colors disabled:opacity-40"
              style={{
                border: "1px solid var(--color-border)",
                color: "var(--color-text-soft)",
                background: "transparent",
                cursor: table.getCanNextPage() ? "pointer" : "default",
              }}
            >
              Siguiente ›
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Componente principal ──────────────────────────────────────────────────────

// ── Descarga client-side (cuando hay filtros activos) ────────────────────────

const DOWNLOAD_COLS = ["isrc", "product_name", "artist_name", "label", "release_date"];

function _triggerBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a   = document.createElement("a");
  a.href     = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function downloadFilteredCsv(rows, filename) {
  const header = DOWNLOAD_COLS.join(",");
  const lines  = rows.map((row) =>
    DOWNLOAD_COLS.map((col) => {
      const v = String(row[col] ?? "").replace(/"/g, '""');
      return `"${v}"`;
    }).join(",")
  );
  const blob = new Blob([[header, ...lines].join("\r\n")], {
    type: "text/csv;charset=utf-8;",
  });
  _triggerBlob(blob, filename);
}

function downloadFilteredXlsx(rows, filename, colsFilter = DOWNLOAD_COLS) {
  const data = rows.map((row) =>
    Object.fromEntries(colsFilter.map((col) => [col, row[col] ?? ""]))
  );
  const ws = XLSX.utils.json_to_sheet(data, { header: colsFilter });
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, "ISRCs");
  XLSX.writeFile(wb, filename);
}

// ── Componente principal ──────────────────────────────────────────────────────

export default function FugaResults({ estado, result, downloadUrl, onReset }) {
  const isCancelled = estado === "cancelled";
  const rows        = result?.rows ?? [];
  const dateFrom    = result?.date_from ?? "";
  const dateTo      = result?.date_to   ?? "";
  const totalIsrcs  = result?.isrcs_total   ?? rows.length;
  const totalRels   = result?.releases_total ?? 0;

  // Filtros de texto libre — puramente client-side
  const [qArtist,  setQArtist]  = useState("");
  const [qLabel,   setQLabel]   = useState("");
  const [qRelease, setQRelease] = useState("");

  const filteredRows = useMemo(() => {
    const a = normalizeStr(qArtist);
    const l = normalizeStr(qLabel);
    const r = normalizeStr(qRelease);
    if (!a && !l && !r) return rows;
    return rows.filter((row) => {
      if (a && !normalizeStr(row.artist_name).includes(a))   return false;
      if (l && !normalizeStr(row.label).includes(l))          return false;
      if (r && !normalizeStr(row.product_name).includes(r))   return false;
      return true;
    });
  }, [rows, qArtist, qLabel, qRelease]);

  const hasFilters = !!(qArtist || qLabel || qRelease);

  return (
    <div className="flex flex-col gap-6 animate-reveal">

      {/* ── 1. Banner de estado ─────────────────────────────────────────────── */}
      <div
        className="flex items-start justify-between gap-4 px-4 py-3 rounded-xl"
        style={{
          background: isCancelled ? "var(--color-warning-bg)" : "var(--color-accent-bg)",
          border: `1px solid ${isCancelled ? "var(--color-warning-border)" : "var(--color-success-border)"}`,
        }}
      >
        <div>
          <p
            className="text-sm font-semibold"
            style={{ color: isCancelled ? "var(--color-warning-text)" : "var(--color-accent-hover)" }}
          >
            {isCancelled
              ? `Búsqueda cancelada — ${totalIsrcs.toLocaleString("es")} ISRCs obtenidos hasta el momento`
              : `Encontrados ${totalIsrcs.toLocaleString("es")} ISRCs únicos en ${totalRels.toLocaleString("es")} releases`}
          </p>
          {(dateFrom || dateTo) && (
            <p
              className="text-xs mt-0.5"
              style={{
                color: isCancelled ? "var(--color-warning-text)" : "var(--color-text-soft)",
                opacity: 0.85,
              }}
            >
              {dateFrom && dateTo ? `Lanzados entre ${dateFrom} y ${dateTo}` : ""}
              {isCancelled && " · Los datos disponibles se pueden descargar igualmente."}
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={onReset}
          className="btn btn-secondary flex items-center gap-1.5 text-xs"
          style={{ flexShrink: 0 }}
        >
          <IconRefresh size={12} /> Nueva búsqueda
        </button>
      </div>

      {/* ── 2. Filtros de texto libre ────────────────────────────────────────── */}
      {rows.length > 0 && (
        <div
          className="flex flex-col gap-3 p-4 rounded-xl animate-reveal animate-reveal-delay-1"
          style={{
            background: "var(--color-surface)",
            border: "1px solid var(--color-border)",
            boxShadow: "var(--shadow-sm)",
          }}
        >
          <p
            className="text-xs font-semibold uppercase tracking-wide"
            style={{ color: "var(--color-text-soft)" }}
          >
            Filtrar resultados
          </p>
          <div className="fuga-filter-grid">
            <div>
              <label
                htmlFor="fuga-q-artist"
                className="block text-xs mb-1"
                style={{ color: "var(--color-text-soft)" }}
              >
                Artista contiene
              </label>
              <input
                id="fuga-q-artist"
                type="search"
                placeholder="ej. Pure Negga"
                value={qArtist}
                onChange={(e) => setQArtist(e.target.value)}
                className="fuga-input"
              />
            </div>
            <div>
              <label
                htmlFor="fuga-q-label"
                className="block text-xs mb-1"
                style={{ color: "var(--color-text-soft)" }}
              >
                Sello contiene
              </label>
              <input
                id="fuga-q-label"
                type="search"
                placeholder="ej. Rapport"
                value={qLabel}
                onChange={(e) => setQLabel(e.target.value)}
                className="fuga-input"
              />
            </div>
            <div>
              <label
                htmlFor="fuga-q-release"
                className="block text-xs mb-1"
                style={{ color: "var(--color-text-soft)" }}
              >
                Release contiene
              </label>
              <input
                id="fuga-q-release"
                type="search"
                placeholder="ej. Bora Bora"
                value={qRelease}
                onChange={(e) => setQRelease(e.target.value)}
                className="fuga-input"
              />
            </div>
          </div>
          <p className="text-xs" style={{ color: "var(--color-text-muted)" }}>
            {hasFilters
              ? `Mostrando ${filteredRows.length.toLocaleString("es")} de ${rows.length.toLocaleString("es")} ISRCs filtrados`
              : `${rows.length.toLocaleString("es")} ISRCs · escribe para filtrar`}
          </p>
        </div>
      )}

      {/* ── 3. Descargas ────────────────────────────────────────────────────── */}
      {rows.length > 0 && (
        <div className="flex flex-col gap-2 animate-reveal animate-reveal-delay-2">
          <div className="flex items-center gap-3 flex-wrap">
            <p className="text-sm font-medium" style={{ color: "var(--color-text)" }}>
              {hasFilters ? "Descargar (filtrados):" : "Descargar:"}
            </p>

            {/* Excel completo */}
            <button
              type="button"
              className="btn btn-secondary flex items-center gap-1.5"
              style={{ fontSize: "12px" }}
              onClick={() => {
                if (hasFilters) {
                  downloadFilteredXlsx(
                    filteredRows,
                    `fuga_isrcs_filtrado_${dateFrom}_${dateTo}.xlsx`,
                    DOWNLOAD_COLS
                  );
                } else {
                  const url = downloadUrl("xlsx_full");
                  if (url) window.open(url, "_self");
                }
              }}
            >
              <IconDownload size={13} /> Excel completo
            </button>

            {/* Excel solo ISRC */}
            <button
              type="button"
              className="btn btn-secondary flex items-center gap-1.5"
              style={{ fontSize: "12px" }}
              onClick={() => {
                if (hasFilters) {
                  downloadFilteredXlsx(
                    filteredRows,
                    `fuga_solo_isrc_filtrado_${dateFrom}_${dateTo}.xlsx`,
                    ["isrc"]
                  );
                } else {
                  const url = downloadUrl("xlsx_isrc");
                  if (url) window.open(url, "_self");
                }
              }}
            >
              <IconDownload size={13} /> Excel solo ISRC
            </button>

            {/* CSV completo */}
            <button
              type="button"
              className="btn btn-secondary flex items-center gap-1.5"
              style={{ fontSize: "12px" }}
              onClick={() => {
                if (hasFilters) {
                  downloadFilteredCsv(
                    filteredRows,
                    `fuga_isrcs_filtrado_${dateFrom}_${dateTo}.csv`
                  );
                } else {
                  const url = downloadUrl("csv");
                  if (url) window.open(url, "_self");
                }
              }}
            >
              <IconDownload size={13} /> CSV completo
            </button>
          </div>

          {/* Nota de ayuda para "Excel solo ISRC" */}
          <p className="text-xs" style={{ color: "var(--color-text-muted)" }}>
            <strong>Excel solo ISRC</strong> — lista compacta para subir en{" "}
            <em>Procesar Excel</em> / <em>Crear playlist</em>.
            {hasFilters && (
              <span className="ml-1">
                · las descargas incluyen solo los {filteredRows.length.toLocaleString("es")} ISRCs filtrados.
              </span>
            )}
          </p>
        </div>
      )}

      {/* ── 4. Tabla ────────────────────────────────────────────────────────── */}
      {rows.length > 0 ? (
        <div className="animate-reveal animate-reveal-delay-3">
          <FugaResultsTable rows={filteredRows} />
        </div>
      ) : (
        <div
          className="px-4 py-10 rounded-xl text-center animate-reveal animate-reveal-delay-2"
          style={{
            background: "var(--color-surface)",
            border: "1px solid var(--color-border)",
          }}
        >
          <p className="text-sm" style={{ color: "var(--color-text-muted)" }}>
            No se encontraron ISRCs lanzados
            {dateFrom && dateTo ? ` entre ${dateFrom} y ${dateTo}` : " en el rango seleccionado"}.
          </p>
        </div>
      )}
    </div>
  );
}
