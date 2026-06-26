"use client";

/**
 * BatchResults — Panel de resultados cuando el job está done o cancelled.
 *
 * Muestra:
 *   - Banner de estado (parcial si cancelled, completo si done)
 *   - Métricas resumen: ISRCs procesados, resueltos, playlists totales
 *   - Tabla @tanstack/react-table con filtros (tipo, suscriptores mínimos, búsqueda)
 *   - Botones de descarga directa (CSV, XLSX) vía URL del backend
 *   - Sección plegable de ISRCs sin resultado con motivo
 *
 * Props:
 *   estado          {"done"|"cancelled"}
 *   hechos          {number}
 *   total           {number}
 *   callsUsed       {number}
 *   notFoundCount   {number}
 *   result          {{ metaCount, playlists, notFound }}
 *   onReset         {() => void}
 *   downloadUrl     {(fmt) => string|null}
 */

import { useState, useMemo } from "react";
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getFilteredRowModel,
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

function formatNumber(n) {
  if (!n && n !== 0) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000)     return `${(n / 1_000).toFixed(0)}K`;
  return n.toLocaleString("es");
}

// ── Tipos de playlist ─────────────────────────────────────────────────────────

function classifyType(raw) {
  if (!raw) return "user";
  const t = raw.toLowerCase();
  if (t.includes("editorial") || t.includes("algotorial")) return "editorial";
  if (t.includes("algorithmic")) return "algorithmic";
  if (t.includes("chart")) return "charts";
  return "user";
}

const TYPE_LABELS = {
  editorial:    "Editorial",
  algorithmic:  "Algoritmo",
  charts:       "Charts",
  user:         "Usuario",
};

// ── Plataforma → color ────────────────────────────────────────────────────────

const PLAT_COLORS = {
  "spotify":     "#1a9e5c",
  "apple-music": "#fc3c44",
  "amazon":      "#e87c14",
  "deezer":      "#a066d3",
  "youtube":     "#dc2626",
  "soundcloud":  "#d44c00",
  "tidal":       "#1a1f2e",
  "audiomack":   "#f59e0b",
  "pandora":     "#005fa3",
};

const PLAT_LABELS = {
  "spotify":     "Spotify",
  "apple-music": "Apple",
  "amazon":      "Amazon",
  "deezer":      "Deezer",
  "youtube":     "YouTube",
  "soundcloud":  "SoundCloud",
  "tidal":       "Tidal",
  "audiomack":   "Audiomack",
  "pandora":     "Pandora",
};

// ── Iconos ────────────────────────────────────────────────────────────────────

function IconDownload({ size = 14 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M8 2v8M5 7l3 3 3-3" /><path d="M2 12h12" />
    </svg>
  );
}

function IconRefresh({ size = 13 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <polyline points="1 4 1 10 7 10" />
      <path d="M3.51 15a9 9 0 1 0 .49-4.5" />
    </svg>
  );
}

function IconChevron({ dir = "down", size = 12 }) {
  const rot = dir === "up" ? "rotate(180deg)" : undefined;
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.5" strokeLinecap="round" aria-hidden="true"
      style={{ transform: rot }}>
      <polyline points="6 9 12 15 18 9" />
    </svg>
  );
}

function SortIcon({ dir }) {
  if (!dir) return (
    <svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor"
      strokeWidth="1.5" strokeLinecap="round" aria-hidden="true">
      <path d="M3 4l2-2 2 2M3 6l2 2 2-2" />
    </svg>
  );
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor"
      strokeWidth="1.75" strokeLinecap="round" aria-hidden="true"
      style={{ transform: dir === "asc" ? "rotate(180deg)" : undefined }}>
      <path d="M5 2v6M2 5l3 3 3-3" />
    </svg>
  );
}

// ── Tarjeta de métrica ────────────────────────────────────────────────────────

function MetricCard({ label, value, sub, highlight }) {
  return (
    <div
      className="flex flex-col gap-0.5 px-5 py-4 rounded-xl"
      style={{
        background: highlight ? "var(--color-accent-bg)" : "var(--color-surface)",
        border: `1px solid ${highlight ? "var(--color-success-border)" : "var(--color-border)"}`,
        boxShadow: "var(--shadow-sm)",
      }}
    >
      <span className="text-xs" style={{ color: "var(--color-text-soft)" }}>
        {label}
      </span>
      <span
        className="text-2xl font-semibold leading-none"
        style={{
          fontFamily: "var(--font-mono)",
          color: highlight ? "var(--color-accent-hover)" : "var(--color-text)",
          letterSpacing: "-0.03em",
        }}
      >
        {value}
      </span>
      {sub && (
        <span className="text-xs" style={{ color: "var(--color-text-muted)" }}>
          {sub}
        </span>
      )}
    </div>
  );
}

// ── Chip de plataforma ────────────────────────────────────────────────────────

function PlatChip({ plat }) {
  const color = PLAT_COLORS[plat] ?? "#9ba3af";
  const label = PLAT_LABELS[plat] ?? plat;
  return (
    <span
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[11px] font-semibold"
      style={{ background: `${color}18`, color }}
    >
      <span style={{ width: 6, height: 6, borderRadius: "50%", background: color, flexShrink: 0, display: "inline-block" }} />
      {label}
    </span>
  );
}

// ── Chip de tipo ──────────────────────────────────────────────────────────────

function TypeChip({ type }) {
  return (
    <span className="type-chip" data-type={type}>
      {TYPE_LABELS[type] ?? type}
    </span>
  );
}

// ── Filtros de la tabla ───────────────────────────────────────────────────────

function TableFilters({ search, onSearch, typeFilter, onTypeFilter, minSubs, onMinSubs }) {
  return (
    <div className="flex items-center gap-3 flex-wrap">
      {/* Búsqueda */}
      <div className="relative flex items-center" style={{ flex: "1 1 180px", maxWidth: "260px" }}>
        <span className="absolute left-2.5 pointer-events-none" style={{ color: "var(--color-text-muted)" }}>
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
          </svg>
        </span>
        <input
          type="search"
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          placeholder="Buscar playlist, ISRC…"
          aria-label="Buscar en resultados"
          className="w-full text-xs rounded-lg py-[7px] pl-[28px] pr-3"
          style={{
            border: "1px solid var(--color-border)",
            background: "var(--color-surface)",
            color: "var(--color-text)",
            fontFamily: "var(--font-sans)",
            outline: "none",
          }}
          onFocus={(e) => { e.target.style.borderColor = "var(--color-accent)"; }}
          onBlur={(e) => { e.target.style.borderColor = "var(--color-border)"; }}
        />
      </div>

      {/* Tipo */}
      <select
        value={typeFilter}
        onChange={(e) => onTypeFilter(e.target.value)}
        className="text-xs rounded-lg py-[7px] px-2.5"
        style={{
          border: "1px solid var(--color-border)",
          background: "var(--color-surface)",
          color: "var(--color-text)",
          fontFamily: "var(--font-sans)",
          outline: "none",
          cursor: "pointer",
        }}
      >
        <option value="">Todos los tipos</option>
        <option value="editorial">Editorial</option>
        <option value="algorithmic">Algoritmo</option>
        <option value="charts">Charts</option>
        <option value="user">Usuario</option>
      </select>

      {/* Suscriptores mínimos */}
      <select
        value={minSubs}
        onChange={(e) => onMinSubs(Number(e.target.value))}
        className="text-xs rounded-lg py-[7px] px-2.5"
        style={{
          border: "1px solid var(--color-border)",
          background: "var(--color-surface)",
          color: "var(--color-text)",
          fontFamily: "var(--font-sans)",
          outline: "none",
          cursor: "pointer",
        }}
      >
        <option value={0}>Todos los tamaños</option>
        <option value={1000}>Mín. 1K seguidores</option>
        <option value={10000}>Mín. 10K seguidores</option>
        <option value={100000}>Mín. 100K seguidores</option>
        <option value={1000000}>Mín. 1M seguidores</option>
      </select>
    </div>
  );
}

// ── Tabla de resultados con TanStack ─────────────────────────────────────────

const COLUMNS_DEF = [
  {
    id: "isrc",
    header: "ISRC",
    accessorKey: "isrc",
    cell: (info) => (
      <span className="cell-code" style={{ fontFamily: "var(--font-mono)", fontSize: "12px", letterSpacing: "0.02em" }}>
        {info.getValue()}
      </span>
    ),
  },
  {
    id: "song_name",
    header: "Canción",
    accessorKey: "song_name",
    cell: (info) => (
      <span style={{ fontSize: "12px", display: "block", maxWidth: "200px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
        title={info.getValue()}>
        {info.getValue() || <span style={{ color: "var(--color-text-muted)" }}>—</span>}
      </span>
    ),
  },
  {
    id: "platform",
    header: "DSP",
    accessorKey: "platform",
    cell: (info) => <PlatChip plat={info.getValue()} />,
  },
  {
    id: "playlist_name",
    header: "Playlist",
    accessorKey: "playlist_name",
    cell: (info) => (
      <span style={{ fontSize: "12px", display: "block", maxWidth: "260px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
        title={info.getValue()}>
        {info.getValue() || <span style={{ color: "var(--color-text-muted)" }}>—</span>}
      </span>
    ),
  },
  {
    id: "playlist_type",
    header: "Tipo",
    accessorKey: "playlist_type",
    cell: (info) => <TypeChip type={classifyType(info.getValue())} />,
    sortingFn: (a, b) => {
      const order = { editorial: 0, algorithmic: 1, charts: 2, user: 3 };
      return (order[classifyType(a.original.playlist_type)] ?? 9)
           - (order[classifyType(b.original.playlist_type)] ?? 9);
    },
  },
  {
    id: "subscriber_count",
    header: "Seguidores",
    accessorKey: "subscriber_count",
    meta: { align: "right" },
    cell: (info) => (
      <span style={{ fontFamily: "var(--font-mono)", fontSize: "12px", color: "var(--color-text)" }}>
        {formatNumber(info.getValue())}
      </span>
    ),
  },
  {
    id: "position",
    header: "Pos.",
    accessorKey: "position",
    meta: { align: "right" },
    cell: (info) => {
      const v = info.getValue();
      return v != null
        ? <span style={{ fontFamily: "var(--font-mono)", fontSize: "12px" }}>#{v}</span>
        : <span style={{ color: "var(--color-text-muted)", fontSize: "12px" }}>—</span>;
    },
  },
];

function ResultsTable({ rows }) {
  const [search, setSearch]           = useState("");
  const [typeFilter, setTypeFilter]   = useState("");
  const [minSubs, setMinSubs]         = useState(0);
  const [sorting, setSorting]         = useState([{ id: "subscriber_count", desc: true }]);
  const [pagination, setPagination]   = useState({ pageIndex: 0, pageSize: 50 });

  // Filtrado manual (búsqueda + tipo + suscriptores) antes de TanStack
  const filtered = useMemo(() => {
    const q = normalizeStr(search);
    return rows.filter((row) => {
      // Búsqueda global
      if (q) {
        const haystack = normalizeStr(
          [row.isrc, row.song_name, row.playlist_name, row.platform].join(" ")
        );
        if (!haystack.includes(q)) return false;
      }
      // Tipo
      if (typeFilter && classifyType(row.playlist_type) !== typeFilter) return false;
      // Suscriptores mínimos
      if (minSubs && (row.subscriber_count ?? 0) < minSubs) return false;
      return true;
    });
  }, [rows, search, typeFilter, minSubs]);

  const table = useReactTable({
    data: filtered,
    columns: COLUMNS_DEF,
    state: { sorting, pagination },
    onSortingChange: setSorting,
    onPaginationChange: setPagination,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
  });

  const { pageIndex, pageSize } = table.getState().pagination;
  const totalFiltered = filtered.length;
  const from = totalFiltered === 0 ? 0 : pageIndex * pageSize + 1;
  const to   = Math.min((pageIndex + 1) * pageSize, totalFiltered);

  return (
    <div className="flex flex-col gap-3">
      <TableFilters
        search={search}
        onSearch={(v) => { setSearch(v); setPagination((p) => ({ ...p, pageIndex: 0 })); }}
        typeFilter={typeFilter}
        onTypeFilter={(v) => { setTypeFilter(v); setPagination((p) => ({ ...p, pageIndex: 0 })); }}
        minSubs={minSubs}
        onMinSubs={(v) => { setMinSubs(v); setPagination((p) => ({ ...p, pageIndex: 0 })); }}
      />

      {/* Tabla */}
      <div style={{ borderRadius: "var(--radius-lg)", overflow: "hidden", border: "1px solid var(--color-border)" }}>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px", minWidth: "640px" }}>
            <thead style={{ position: "sticky", top: 0, zIndex: 10, background: "var(--color-surface)" }}>
              {table.getHeaderGroups().map((hg) => (
                <tr key={hg.id}>
                  {hg.headers.map((header) => {
                    const meta   = header.column.columnDef.meta ?? {};
                    const canSort = header.column.getCanSort();
                    const sorted  = header.column.getIsSorted();
                    return (
                      <th
                        key={header.id}
                        scope="col"
                        onClick={canSort ? header.column.getToggleSortingHandler() : undefined}
                        style={{
                          padding: "9px 12px",
                          textAlign: meta.align ?? "left",
                          fontSize: "11px",
                          fontWeight: 600,
                          textTransform: "uppercase",
                          letterSpacing: "0.05em",
                          color: "var(--color-text-soft)",
                          borderBottom: "1px solid var(--color-border-strong)",
                          whiteSpace: "nowrap",
                          cursor: canSort ? "pointer" : "default",
                          userSelect: "none",
                        }}
                      >
                        <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
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
                  <td colSpan={COLUMNS_DEF.length} style={{ padding: "48px 16px", textAlign: "center", color: "var(--color-text-muted)", fontSize: "13px" }}>
                    Sin resultados para estos filtros
                  </td>
                </tr>
              ) : (
                table.getRowModel().rows.map((row, i) => (
                  <tr
                    key={row.id}
                    style={{
                      borderBottom: "1px solid var(--color-border)",
                      background: i % 2 !== 0 ? "var(--color-surface-raised)" : "var(--color-surface)",
                      transition: "background 120ms ease",
                    }}
                    onMouseEnter={(e) => { e.currentTarget.style.background = "var(--color-accent-subtle)"; }}
                    onMouseLeave={(e) => { e.currentTarget.style.background = i % 2 !== 0 ? "var(--color-surface-raised)" : "var(--color-surface)"; }}
                  >
                    {row.getVisibleCells().map((cell) => {
                      const meta = cell.column.columnDef.meta ?? {};
                      return (
                        <td key={cell.id} style={{ padding: "7px 12px", verticalAlign: "middle", textAlign: meta.align ?? "left" }}>
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

        {/* Footer */}
        <div
          className="flex items-center justify-between gap-3 px-4 py-2.5 text-xs flex-wrap"
          style={{ borderTop: "1px solid var(--color-border)", background: "var(--color-surface)", color: "var(--color-text-soft)" }}
        >
          <span>
            {totalFiltered === 0
              ? "Sin resultados"
              : `Mostrando ${from}–${to} de ${totalFiltered.toLocaleString("es")} placements`}
          </span>
          <div className="flex items-center gap-3">
            <label className="flex items-center gap-1.5" style={{ color: "var(--color-text-muted)" }}>
              <span>Filas:</span>
              <select
                value={pageSize}
                onChange={(e) => { table.setPageSize(Number(e.target.value)); table.setPageIndex(0); }}
                className="text-xs rounded py-[3px] px-1.5"
                style={{ border: "1px solid var(--color-border)", background: "var(--color-surface)", color: "var(--color-text)", fontFamily: "var(--font-sans)", outline: "none", cursor: "pointer" }}
              >
                {[25, 50, 100, 200].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
            <div className="flex items-center gap-2">
              <button onClick={() => table.previousPage()} disabled={!table.getCanPreviousPage()}
                className="px-2.5 py-1 rounded transition-colors disabled:opacity-40"
                style={{ border: "1px solid var(--color-border)", color: "var(--color-text-soft)", background: "transparent", cursor: table.getCanPreviousPage() ? "pointer" : "default" }}>
                ‹ Anterior
              </button>
              <span style={{ fontFamily: "var(--font-mono)", color: "var(--color-text-muted)", whiteSpace: "nowrap" }}>
                {pageIndex + 1} / {table.getPageCount() || 1}
              </span>
              <button onClick={() => table.nextPage()} disabled={!table.getCanNextPage()}
                className="px-2.5 py-1 rounded transition-colors disabled:opacity-40"
                style={{ border: "1px solid var(--color-border)", color: "var(--color-text-soft)", background: "transparent", cursor: table.getCanNextPage() ? "pointer" : "default" }}>
                Siguiente ›
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Sección de ISRCs sin resultado ────────────────────────────────────────────

function NotFoundSection({ notFound }) {
  const [open, setOpen] = useState(false);
  if (!notFound?.length) return null;

  return (
    <div
      className="rounded-xl overflow-hidden"
      style={{ border: "1px solid var(--color-warning-border)" }}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-4 py-3 text-sm font-medium text-left transition-colors"
        style={{
          background: "var(--color-warning-bg)",
          color: "var(--color-warning-text)",
          cursor: "pointer",
        }}
      >
        <span>{notFound.length.toLocaleString("es")} ISRCs sin resultado en Soundcharts</span>
        <IconChevron dir={open ? "up" : "down"} />
      </button>

      {open && (
        <div style={{ background: "var(--color-surface)" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "12px" }}>
            <thead>
              <tr>
                <th style={{ padding: "8px 16px", textAlign: "left", fontWeight: 600, fontSize: "11px", textTransform: "uppercase", letterSpacing: "0.04em", color: "var(--color-text-soft)", borderBottom: "1px solid var(--color-border)" }}>
                  ISRC
                </th>
                <th style={{ padding: "8px 16px", textAlign: "left", fontWeight: 600, fontSize: "11px", textTransform: "uppercase", letterSpacing: "0.04em", color: "var(--color-text-soft)", borderBottom: "1px solid var(--color-border)" }}>
                  Motivo
                </th>
              </tr>
            </thead>
            <tbody>
              {notFound.map(([isrc, motivo], i) => (
                <tr
                  key={i}
                  style={{ borderBottom: i < notFound.length - 1 ? "1px solid var(--color-border)" : "none" }}
                >
                  <td style={{ padding: "6px 16px", fontFamily: "var(--font-mono)", fontSize: "12px", letterSpacing: "0.02em", color: "var(--color-text)" }}>
                    {isrc}
                  </td>
                  <td style={{ padding: "6px 16px", color: "var(--color-text-soft)", fontSize: "12px" }}>
                    {motivo === "no en Soundcharts"
                      ? "No encontrado en Soundcharts"
                      : motivo}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── Componente principal ──────────────────────────────────────────────────────

export default function BatchResults({
  estado,
  hechos,
  total,
  callsUsed,
  notFoundCount,
  result,
  onReset,
  downloadUrl,
}) {
  const isCancelled = estado === "cancelled";
  const playlists = result?.playlists ?? [];
  const notFound  = result?.notFound  ?? [];
  const encontrados = hechos - notFoundCount;

  return (
    <div className="flex flex-col gap-6 animate-reveal">

      {/* Banner de estado */}
      <div
        className="flex items-center justify-between gap-4 px-4 py-3 rounded-xl"
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
              ? "Proceso cancelado — resultado parcial"
              : "Proceso completado"}
          </p>
          {isCancelled && (
            <p className="text-xs mt-0.5" style={{ color: "var(--color-warning-text)" }}>
              Los datos disponibles hasta el momento se pueden descargar igualmente.
            </p>
          )}
        </div>
        <button
          type="button"
          onClick={onReset}
          className="btn btn-secondary flex items-center gap-1.5 text-xs"
        >
          <IconRefresh size={12} /> Nuevo lote
        </button>
      </div>

      {/* Métricas resumen */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 animate-reveal animate-reveal-delay-1">
        <MetricCard label="ISRCs procesados"   value={hechos.toLocaleString("es")} highlight={false} />
        <MetricCard
          label="ISRCs resueltos"
          value={encontrados.toLocaleString("es")}
          sub={total ? `${Math.round((encontrados / total) * 100)}% del lote` : undefined}
          highlight={true}
        />
        <MetricCard label="Playlists / placements" value={playlists.length.toLocaleString("es")} />
        <MetricCard label="Llamadas API usadas" value={callsUsed.toLocaleString("es")}
          sub="créditos Soundcharts" />
      </div>

      {/* Descarga */}
      <div className="flex items-center gap-3 animate-reveal animate-reveal-delay-2">
        <p className="text-sm font-medium" style={{ color: "var(--color-text)" }}>
          Descargar resultado:
        </p>
        <a
          href={downloadUrl("csv") ?? "#"}
          download
          className="btn btn-secondary flex items-center gap-1.5"
          style={{ fontSize: "12px" }}
          aria-disabled={!downloadUrl("csv")}
        >
          <IconDownload size={13} /> CSV
        </a>
        <a
          href={downloadUrl("xlsx") ?? "#"}
          download
          className="btn btn-secondary flex items-center gap-1.5"
          style={{ fontSize: "12px" }}
          aria-disabled={!downloadUrl("xlsx")}
        >
          <IconDownload size={13} /> Excel
        </a>
        {isCancelled && (
          <span className="text-xs" style={{ color: "var(--color-text-muted)" }}>
            (resultado parcial — {hechos} ISRCs de {total})
          </span>
        )}
      </div>

      {/* Tabla */}
      {playlists.length > 0 ? (
        <div className="animate-reveal animate-reveal-delay-3">
          <p className="text-sm font-medium mb-3" style={{ color: "var(--color-text)" }}>
            Tabla de placements
          </p>
          <ResultsTable rows={playlists} />
        </div>
      ) : (
        <div
          className="px-4 py-8 rounded-xl text-center animate-reveal animate-reveal-delay-3"
          style={{ background: "var(--color-surface)", border: "1px solid var(--color-border)" }}
        >
          <p className="text-sm" style={{ color: "var(--color-text-muted)" }}>
            No se encontraron placements para los ISRCs procesados.
          </p>
        </div>
      )}

      {/* ISRCs sin resultado */}
      {notFound.length > 0 && (
        <div className="animate-reveal animate-reveal-delay-4">
          <NotFoundSection notFound={notFound} />
        </div>
      )}
    </div>
  );
}
