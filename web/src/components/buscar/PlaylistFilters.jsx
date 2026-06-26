"use client";

/**
 * PlaylistFilters — Controles de filtrado para la lista de playlists F2.
 *
 * - Chips de tipo (toggle): al hacer clic en un tipo se añade/quita del filtro.
 *   Cuando el Set está vacío = todos los tipos visibles (sin filtro activo).
 * - Selector de mínimo de seguidores.
 * - Botón "Ver todos" para limpiar el filtro de tipos.
 *
 * Props:
 *   availableTypes  {string[]}     — tipos presentes en el resultado (classifyType)
 *   typeFilter      {Set<string>}  — tipos activos; vacío = sin filtro
 *   onTypeFilter    {(Set) => void}
 *   minSubs         {number}
 *   onMinSubs       {(n: number) => void}
 *   className       {string}
 */

import { TYPE_LABELS } from "@/lib/playlist-utils";

const TYPE_ORDER = ["editorial", "algorithmic", "charts", "user"];

export default function PlaylistFilters({
  availableTypes = [],
  typeFilter,
  onTypeFilter,
  minSubs,
  onMinSubs,
  className = "",
}) {
  const sortedTypes = TYPE_ORDER.filter((t) => availableTypes.includes(t));
  const hasActiveFilter = typeFilter.size > 0;

  function toggleType(t) {
    const next = new Set(typeFilter);
    if (next.has(t)) next.delete(t);
    else next.add(t);
    onTypeFilter(next);
  }

  return (
    <div className={`flex items-center gap-3 flex-wrap ${className}`}>

      {/* Chips de tipo */}
      <div className="flex items-center gap-1.5 flex-wrap">
        <span
          className="text-xs font-medium"
          style={{ color: "var(--color-text-soft)" }}
        >
          Tipo:
        </span>

        {sortedTypes.map((t) => {
          const isActive = hasActiveFilter && typeFilter.has(t);
          const isExcluded = hasActiveFilter && !typeFilter.has(t);

          return (
            <button
              key={t}
              type="button"
              onClick={() => toggleType(t)}
              className="filter-chip"
              data-active={isActive ? "true" : undefined}
              aria-pressed={!hasActiveFilter || isActive}
              style={isExcluded ? { opacity: 0.45 } : undefined}
            >
              {TYPE_LABELS[t] ?? t}
            </button>
          );
        })}

        {hasActiveFilter && (
          <button
            type="button"
            onClick={() => onTypeFilter(new Set())}
            className="text-xs"
            style={{
              color: "var(--color-accent)",
              textDecoration: "underline",
              background: "none",
              border: "none",
              cursor: "pointer",
              padding: "2px 0",
            }}
          >
            Ver todos
          </button>
        )}
      </div>

      {/* Separador vertical */}
      <div
        style={{
          width: 1,
          height: 18,
          background: "var(--color-border)",
          flexShrink: 0,
        }}
        aria-hidden="true"
      />

      {/* Mínimo seguidores */}
      <div className="flex items-center gap-2">
        <span
          className="text-xs font-medium"
          style={{ color: "var(--color-text-soft)" }}
        >
          Mín. seguidores:
        </span>
        <select
          value={minSubs}
          onChange={(e) => onMinSubs(Number(e.target.value))}
          aria-label="Filtrar por mínimo de seguidores"
          className="text-xs rounded-lg"
          style={{
            padding: "6px 10px",
            border: "1px solid var(--color-border)",
            background: "var(--color-surface)",
            color: "var(--color-text)",
            fontFamily: "var(--font-sans)",
            outline: "none",
            cursor: "pointer",
          }}
        >
          <option value={0}>Todos</option>
          <option value={1_000}>1K+</option>
          <option value={10_000}>10K+</option>
          <option value={100_000}>100K+</option>
          <option value={1_000_000}>1M+</option>
        </select>
      </div>
    </div>
  );
}
