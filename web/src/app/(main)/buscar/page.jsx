"use client";

/**
 * Página: Buscar 1 ISRC (F2)
 *
 * Flujo de estados:
 *   idle        → sin ISRC todavía
 *   loading     → fetch en curso (spinner + skeleton)
 *   done        → resultado OK → TrackHeader + KPIs + filtros + PlaylistList
 *   not_found   → Soundcharts no encuentra el ISRC
 *   error       → error de red o servidor
 *
 * Todas las llamadas van a /api/buscar (proxy Next.js).
 * El INTERNAL_TOKEN nunca llega al browser.
 */

import { useState, useRef, useCallback } from "react";
import IsrcSearchBar  from "@/components/buscar/IsrcSearchBar";
import TrackHeader    from "@/components/buscar/TrackHeader";
import SingleKpis     from "@/components/buscar/SingleKpis";
import PlaylistFilters from "@/components/buscar/PlaylistFilters";
import PlaylistList   from "@/components/buscar/PlaylistList";
import EmptyState     from "@/components/buscar/EmptyState";
import { classifyType } from "@/lib/playlist-utils";

const ISRC_RE = /^[A-Za-z]{2}[A-Za-z0-9]{3}\d{7}$/;

// ── Skeleton de carga ─────────────────────────────────────────────────────────

function LoadingSkeleton() {
  return (
    <div className="flex flex-col gap-6 animate-reveal" aria-busy="true" aria-label="Cargando resultado…">
      {/* Track header */}
      <div
        className="px-5 py-4 rounded-xl"
        style={{
          background: "var(--color-surface)",
          border: "1px solid var(--color-border)",
        }}
      >
        <div className="fuga-skeleton h-5 rounded mb-2" style={{ width: "52%" }} />
        <div className="fuga-skeleton h-3.5 rounded" style={{ width: "36%" }} />
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div
            key={i}
            className="px-5 py-4 rounded-xl"
            style={{
              background: "var(--color-surface)",
              border: "1px solid var(--color-border)",
            }}
          >
            <div
              className="fuga-skeleton h-3 rounded mb-3"
              style={{ width: "60%", animationDelay: `${i * 40}ms` }}
            />
            <div
              className="fuga-skeleton h-7 rounded"
              style={{ width: "40%", animationDelay: `${i * 40 + 20}ms` }}
            />
          </div>
        ))}
      </div>

      {/* Cards */}
      <div className="flex flex-col gap-1.5">
        {[0, 1, 2, 3, 4, 5].map((i) => (
          <div
            key={i}
            className="fuga-skeleton rounded-lg"
            style={{
              height: "42px",
              animationDelay: `${i * 35}ms`,
            }}
          />
        ))}
      </div>
    </div>
  );
}

// ── Banner de ISRC inválido ───────────────────────────────────────────────────

function InvalidIsrcBanner({ isrc }) {
  return (
    <div
      className="flex items-start gap-2.5 px-4 py-3 rounded-xl animate-reveal"
      role="alert"
      style={{
        background: "var(--color-warning-bg)",
        border: "1px solid var(--color-warning-border)",
        color: "var(--color-warning-text)",
      }}
    >
      <svg
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
        style={{ flexShrink: 0, marginTop: 1 }}
      >
        <circle cx="12" cy="12" r="10" />
        <line x1="12" y1="8" x2="12" y2="12" />
        <line x1="12" y1="16" x2="12.01" y2="16" />
      </svg>
      <p className="text-sm">
        <code
          style={{
            fontFamily: "var(--font-mono)",
            letterSpacing: "0.04em",
          }}
        >
          {isrc}
        </code>{" "}
        no tiene el formato correcto — 2 letras de país + 3 alfanuméricos + 7
        dígitos (p. ej.{" "}
        <code style={{ fontFamily: "var(--font-mono)" }}>ES14H2600001</code>).
      </p>
    </div>
  );
}

// ── Estado vacío cuando los filtros eliminan todos los resultados ─────────────
// Fix 10: PlaylistList devuelve null cuando filtradas=0, mostramos esto en su lugar.

function EmptyFilterResults({ onClear }) {
  return (
    <div
      className="flex flex-col items-center gap-3 px-5 py-8 rounded-xl text-center animate-reveal"
      role="status"
      style={{
        background: "var(--color-surface)",
        border: "1px solid var(--color-border)",
      }}
    >
      <p className="text-sm font-medium" style={{ color: "var(--color-text-soft)" }}>
        Ningún resultado con los filtros actuales.
      </p>
      <button
        type="button"
        onClick={onClear}
        className="btn btn-secondary text-xs"
      >
        Limpiar filtros
      </button>
    </div>
  );
}

// ── Página principal ──────────────────────────────────────────────────────────

export default function BuscarPage() {
  const [isrc, setIsrc]     = useState("");
  const [scope, setScope]   = useState("importantes");

  const [estado, setEstado]     = useState("idle"); // idle | loading | done | not_found | error
  const [result, setResult]     = useState(null);
  const [errorMsg, setErrorMsg] = useState(null);

  // Filtros locales (solo aplican sobre resultado en memoria)
  const [typeFilter, setTypeFilter] = useState(new Set());
  const [minSubs, setMinSubs]       = useState(0);

  const abortRef = useRef(null);

  // ISRC normalizado y validado
  const isrcNorm  = isrc.trim().toUpperCase();
  const isrcValid = !isrcNorm || ISRC_RE.test(isrcNorm);

  // ── Limpiar filtros ───────────────────────────────────────────────────────

  function clearFilters() {
    setTypeFilter(new Set());
    setMinSubs(0);
  }

  // ── Fetch ─────────────────────────────────────────────────────────────────
  //
  // `bust`: string opcional para forzar cache-miss en el svc (Soundcharts TTL 1h).
  // Vacío → el svc sirve desde cache; no vacío → la clave de cache cambia → re-fetch.

  const buscar = useCallback(async (isrcVal, scopeVal, bust = "") => {
    if (!isrcVal || !ISRC_RE.test(isrcVal)) return;

    // Cancelar fetch anterior si hay uno en vuelo
    if (abortRef.current) abortRef.current.abort();
    abortRef.current = new AbortController();

    setEstado("loading");
    setResult(null);
    setErrorMsg(null);
    setTypeFilter(new Set());
    setMinSubs(0);

    try {
      // Fix 5: incluir bust en la URL para que el proxy lo reenvíe al svc
      const bustSuffix = bust ? `&bust=${encodeURIComponent(bust)}` : "";
      const res = await fetch(
        `/api/buscar?isrc=${encodeURIComponent(isrcVal)}&scope=${encodeURIComponent(scopeVal)}${bustSuffix}`,
        { signal: abortRef.current.signal }
      );

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        // Fix 6: usar body.message (detalle humano del servidor) como primer candidato;
        // body.error como fallback (slug técnico); HTTP status como último recurso.
        throw new Error(body.message ?? body.error ?? `HTTP ${res.status}`);
      }

      const data = await res.json();

      if (!data.meta) {
        setEstado("not_found");
        return;
      }

      setResult(data);
      setEstado("done");

      // Fix 3: paridad con Streamlit — inicializar filtro excluyendo tipo "user"
      // ("Curators & Listeners") que Streamlit oculta por defecto (app.py:1293-1296).
      // Se re-aplica en cada resultado nuevo (incluyendo tras Refrescar).
      const typesPresentes = [
        ...new Set((data.playlists ?? []).map((p) => classifyType(p.playlist_type))),
      ];
      setTypeFilter(new Set(typesPresentes.filter((t) => t !== "user")));

    } catch (err) {
      if (err.name === "AbortError") return; // cancelado intencionalmente por AbortController
      setEstado("error");
      // Fix 6: TypeError = fetch no llegó a contactar el servidor (red caída, svc apagado).
      // Cualquier otro error proviene de una respuesta del servidor con mensaje legible.
      if (err.name === "TypeError") {
        setErrorMsg("No se pudo conectar con el servidor. Comprueba que está activo.");
      } else {
        setErrorMsg(err.message || "Error inesperado. Inténtalo de nuevo.");
      }
    }
  }, []);

  function handleSearch() {
    buscar(isrcNorm, scope);
  }

  // Fix 5: Refresh usa timestamp como bust para forzar cache-miss en el svc.
  function handleRefresh() {
    buscar(isrcNorm, scope, String(Date.now()));
  }

  // Fix 4: cambio de scope relanza búsqueda si hay ISRC válido introducido.
  // Unifica el comportamiento del selector del header y del botón "Buscar en todas"
  // del EmptyState, igual que hacía el Streamlit original.
  function handleScopeChange(newScope) {
    setScope(newScope);
    if (isrcNorm && ISRC_RE.test(isrcNorm)) {
      buscar(isrcNorm, newScope);
    }
  }

  // ── Datos filtrados ───────────────────────────────────────────────────────

  const playlists = result?.playlists ?? [];

  const filteredPlaylists = playlists.filter((p) => {
    if (typeFilter.size > 0 && !typeFilter.has(classifyType(p.playlist_type))) return false;
    if (minSubs && (p.subscriber_count ?? 0) < minSubs) return false;
    return true;
  });

  const availableTypes = [
    ...new Set(playlists.map((p) => classifyType(p.playlist_type))),
  ];

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col gap-6 max-w-[900px] animate-reveal">

      {/* Encabezado de página */}
      <div>
        <h1
          className="text-xl font-semibold leading-tight"
          style={{ color: "var(--color-text)", letterSpacing: "-0.01em" }}
        >
          Buscar 1 ISRC
        </h1>
        <p className="text-sm mt-1" style={{ color: "var(--color-text-soft)" }}>
          Consulta en qué playlists aparece un track por su ISRC en las DSPs vía Soundcharts.
        </p>
      </div>

      {/* Barra de búsqueda — Fix 4: onScopeChange → handleScopeChange */}
      <div className="animate-reveal animate-reveal-delay-1">
        <IsrcSearchBar
          isrc={isrc}
          onIsrcChange={setIsrc}
          scope={scope}
          onScopeChange={handleScopeChange}
          onSearch={handleSearch}
          onRefresh={handleRefresh}
          loading={estado === "loading"}
          hasResult={estado === "done"}
          isrcValid={isrcValid}
        />
      </div>

      {/* Banner de formato ISRC inválido */}
      {isrcNorm && !isrcValid && estado !== "loading" && (
        <InvalidIsrcBanner isrc={isrcNorm} />
      )}

      {/* Estado: loading */}
      {estado === "loading" && <LoadingSkeleton />}

      {/* Estado: idle */}
      {estado === "idle" && <EmptyState type="idle" />}

      {/* Estado: no encontrado en Soundcharts */}
      {estado === "not_found" && (
        <EmptyState
          type="not_found"
          isrc={isrcNorm}
          onRetry={handleSearch}
        />
      )}

      {/* Estado: error */}
      {estado === "error" && (
        <EmptyState
          type="error"
          msg={errorMsg}
          onRetry={handleSearch}
        />
      )}

      {/* Estado: resultado OK */}
      {estado === "done" && result && (
        <>
          {/* Cabecera del track */}
          <TrackHeader
            meta={result.meta}
            isrc={isrcNorm}
            className="animate-reveal animate-reveal-delay-1"
          />

          {/* KPIs — Fix 4: totalPlatforms desde result.total_platforms (backend) */}
          <SingleKpis
            playlists={playlists}
            elapsedMs={result.elapsed_ms}
            callsUsed={result.calls_used}
            platformsCount={result.platforms_count}
            totalPlatforms={result.total_platforms ?? 4}
            className="animate-reveal animate-reveal-delay-2"
          />

          {/* Con placements */}
          {playlists.length > 0 ? (
            <>
              <PlaylistFilters
                availableTypes={availableTypes}
                typeFilter={typeFilter}
                onTypeFilter={setTypeFilter}
                minSubs={minSubs}
                onMinSubs={setMinSubs}
                className="animate-reveal animate-reveal-delay-3"
              />
              {/* Fix 10: filtros vacían la lista → mensaje + limpiar en vez de null */}
              {filteredPlaylists.length > 0 ? (
                <PlaylistList
                  playlists={filteredPlaylists}
                  allCount={playlists.length}
                  className="animate-reveal animate-reveal-delay-4"
                />
              ) : (
                <EmptyFilterResults
                  onClear={clearFilters}
                />
              )}
            </>
          ) : (
            /* Track encontrado pero sin placements */
            <EmptyState
              type="no_placements"
              onChangeScope={() => handleScopeChange("todas")}
            />
          )}
        </>
      )}
    </div>
  );
}
