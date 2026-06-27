"use client";

/**
 * usePlaylistPolling — ciclo de vida de un job de creación de playlist Spotify.
 *
 * Patrón idéntico a useFugaPolling / useBatchPolling:
 *   idle → submitting → running|cooldown → done|cancelled|error|not_configured
 *
 * Estado "cooldown": Spotify ha activado rate-limit; el job sigue en cola
 * server-side esperando que el penalty-box expire. El polling continúa (a menor
 * frecuencia) para detectar cuándo Spotify reanuda. NO es un error terminal.
 *
 * Endpoints esperados (proxy Next.js — el token nunca llega al browser):
 *
 *   POST /api/playlist
 *     body: { isrcs: string[], name: string, description: string, public: boolean }
 *     → { job_id: string }
 *     error 401 / { error: "not_configured" } → estado not_configured
 *
 *   GET  /api/playlist/[id]/status
 *     → { estado: "running"|"cooldown"|"done"|"cancelled"|"error",
 *         phase: "resolving"|"creating"|"adding",
 *         resolved: number,       ← ISRCs resueltos a URIs de Spotify
 *         total: number,          ← total ISRCs de la solicitud
 *         added: number,          ← tracks añadidos a la playlist
 *         not_found: number,      ← ISRCs sin URI en Spotify
 *         progress_pct: number,   ← 0-100
 *         status_text: string,
 *         cooldown_until: string|null,  ← ISO datetime mientras en cooldown
 *         error_msg: string|null }
 *
 *   GET  /api/playlist/[id]/result/json
 *     → { playlist_url, playlist_name, tracks_added, not_found_isrcs, total_isrcs,
 *          errors_count }  ← ISRCs no resueltos + lotes de add que fallaron
 *
 *   GET  /api/playlist/[id]/result/not_found_csv
 *     → text/csv — una columna ISRC de los no encontrados
 *
 *   POST /api/playlist/[id]/cancel
 *     → 200 OK | 409 (ya terminó — tratar como done)
 */

import { useState, useEffect, useRef, useCallback } from "react";

const POLL_INTERVAL_MS       = 2000;
const POLL_INTERVAL_COOLDOWN = 5000;         // más lento en cooldown — sin urgencia
const POLL_TIMEOUT_MS        = 60 * 60 * 1000; // 1h máx (penalty-box de Spotify puede durar)

export function usePlaylistPolling() {
  const [estado,        setEstado]        = useState("idle");
  const [jobId,         setJobId]         = useState(null);
  const [phase,         setPhase]         = useState(null);      // "resolving"|"creating"|"adding"
  const [resolved,      setResolved]      = useState(0);
  const [total,         setTotal]         = useState(0);
  const [added,         setAdded]         = useState(0);
  const [notFound,      setNotFound]      = useState(0);
  const [progressPct,   setProgressPct]   = useState(0);
  const [statusText,    setStatusText]    = useState("");
  const [cooldownUntil, setCooldownUntil] = useState(null);      // ISO string | null
  const [errorMsg,      setErrorMsg]      = useState(null);
  const [result,        setResult]        = useState(null);

  const pollRef      = useRef(null);
  const startTimeRef = useRef(null);
  const cancelledRef = useRef(false);

  // ── Restaurar job en curso al montar / detectar no-configurado ───────────
  useEffect(() => {
    try {
      const saved = sessionStorage.getItem("playlist_job_id");
      if (saved) {
        setJobId(saved);
        setEstado("running");
        return;  // hay un job activo — no comprobamos setup
      }
    } catch (_) {}

    // Fix 10: comprobar setup status al montar para mostrar banner inmediato
    // en lugar de esperar al primer intento de submit.
    fetch("/api/playlist/setup/status")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d && d.connected === false) setEstado("not_configured"); })
      .catch(() => {});
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Cleanup ───────────────────────────────────────────────────────────────
  function cleanup() {
    if (pollRef.current) { clearTimeout(pollRef.current); pollRef.current = null; }
    try { sessionStorage.removeItem("playlist_job_id"); } catch (_) {}
  }

  // ── Polling ───────────────────────────────────────────────────────────────
  const startPolling = useCallback((id) => {
    if (pollRef.current) { clearTimeout(pollRef.current); pollRef.current = null; }
    cancelledRef.current = false;
    startTimeRef.current = Date.now();

    async function tick() {
      if (cancelledRef.current) return;

      if (Date.now() - startTimeRef.current > POLL_TIMEOUT_MS) {
        setEstado("error");
        setErrorMsg("El job tardó demasiado. Comprueba el estado del servidor.");
        cleanup();
        return;
      }

      try {
        const res = await fetch(`/api/playlist/${id}/status`);
        // Fix 1: guard tras cada await — reset()/cancel() en vuelo no deben
        // revertir el estado que el usuario ya solicitó (idle/cancelled).
        if (cancelledRef.current) return;

        if (res.status === 404) {
          setEstado("error");
          setErrorMsg("Job no encontrado. Puede que haya expirado o el servidor se reiniciara.");
          cleanup();
          return;
        }

        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (cancelledRef.current) return;

        setPhase(data.phase ?? null);
        setResolved(data.resolved ?? 0);
        setTotal(data.total ?? 0);
        setAdded(data.added ?? 0);
        setNotFound(data.not_found ?? 0);
        setProgressPct(data.progress_pct ?? 0);
        setStatusText(data.status_text ?? "");
        setCooldownUntil(data.cooldown_until ?? null);

        if (data.estado === "done" || data.estado === "cancelled") {
          try {
            const rRes = await fetch(`/api/playlist/${id}/result/json`);
            if (cancelledRef.current) return;
            if (rRes.ok) {
              const resultData = await rRes.json();
              if (cancelledRef.current) return;
              setResult(resultData);
            }
          } catch (_) {}
          if (cancelledRef.current) return;
          setEstado(data.estado);
          cleanup();
          return;
        }

        if (data.estado === "error") {
          setEstado("error");
          setErrorMsg(data.error_msg ?? "Error creando la playlist. Inténtalo de nuevo.");
          cleanup();
          return;
        }

        // running o cooldown — ambos continúan en el backend
        const isCooldown =
          data.estado === "cooldown" ||
          (data.cooldown_until && new Date(data.cooldown_until) > new Date());

        // Fix 2: durante cooldown el penalty-box de Spotify puede durar 2h.
        // Resetear el reloj de timeout para que no expire mientras el job está
        // activo en el backend — el timeout solo cuenta inactividad real.
        if (isCooldown) startTimeRef.current = Date.now();

        setEstado(isCooldown ? "cooldown" : "running");

        const interval = isCooldown ? POLL_INTERVAL_COOLDOWN : POLL_INTERVAL_MS;
        pollRef.current = setTimeout(tick, interval);
      } catch (_err) {
        if (!cancelledRef.current) {
          pollRef.current = setTimeout(tick, POLL_INTERVAL_MS * 2);
        }
      }
    }

    pollRef.current = setTimeout(tick, POLL_INTERVAL_MS);
  }, []);

  // Arrancar polling cuando se recupera un jobId del sessionStorage
  useEffect(() => {
    if ((estado === "running" || estado === "cooldown") && jobId && !startTimeRef.current) {
      startPolling(jobId);
    }
    return () => {
      // Fix 9: poner cancelledRef=true ANTES de clearTimeout para que un tick
      // en vuelo no reprograme el polling tras el desmontaje del componente.
      cancelledRef.current = true;
      if (pollRef.current) { clearTimeout(pollRef.current); pollRef.current = null; }
    };
  }, [estado, jobId, startPolling]);

  // ── API pública ───────────────────────────────────────────────────────────

  /**
   * Inicia el job de creación de playlist.
   * @param {{ isrcs: string[], name: string, description: string, isPublic: boolean }} params
   */
  const submit = useCallback(async ({ isrcs, name, description, isPublic }) => {
    cleanup();
    cancelledRef.current = false;
    startTimeRef.current = null;

    setEstado("submitting");
    setErrorMsg(null);
    setResult(null);
    setPhase(null);
    setResolved(0);
    setTotal(isrcs.length);
    setAdded(0);
    setNotFound(0);
    setProgressPct(0);
    setStatusText("Iniciando…");
    setCooldownUntil(null);

    try {
      const res = await fetch("/api/playlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ isrcs, name, description, public: isPublic }),
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        if (res.status === 401 || body.error === "not_configured") {
          setEstado("not_configured");
          return;
        }
        throw new Error(body.message ?? body.error ?? `HTTP ${res.status}`);
      }

      const data = await res.json();
      const id = data.job_id;

      setJobId(id);
      setEstado("running");
      try { sessionStorage.setItem("playlist_job_id", id); } catch (_) {}
      startPolling(id);
    } catch (err) {
      setEstado("error");
      setErrorMsg(
        err.name === "TypeError"
          ? "No se pudo conectar con el servidor."
          : (err.message || "Error inesperado.")
      );
    }
  }, [startPolling]);

  /**
   * Cancela el job en curso.
   * Si POST /cancel devuelve 409 (ya terminó), carga el resultado y pasa a "done".
   */
  const cancel = useCallback(async () => {
    cancelledRef.current = true;
    cleanup();

    if (!jobId) { setEstado("cancelled"); return; }

    try {
      const res = await fetch(`/api/playlist/${jobId}/cancel`, { method: "POST" });
      if (res.status === 409) {
        try {
          const rRes = await fetch(`/api/playlist/${jobId}/result/json`);
          if (rRes.ok) setResult(await rRes.json());
        } catch (_) {}
        setEstado("done");
        return;
      }
    } catch (_) {}

    // Esperar a que el worker materialice el resultado parcial
    await new Promise((r) => setTimeout(r, 1000));
    try {
      const rRes = await fetch(`/api/playlist/${jobId}/result/json`);
      if (rRes.ok) setResult(await rRes.json());
    } catch (_) {}

    setEstado("cancelled");
  }, [jobId]);

  /** Vuelve al estado inicial. */
  const reset = useCallback(() => {
    cancelledRef.current = true;
    cleanup();
    startTimeRef.current = null;

    setEstado("idle");
    setJobId(null);
    setPhase(null);
    setResolved(0);
    setTotal(0);
    setAdded(0);
    setNotFound(0);
    setProgressPct(0);
    setStatusText("");
    setCooldownUntil(null);
    setErrorMsg(null);
    setResult(null);
  }, []);

  /** URL de descarga vía proxy Next.js. */
  const downloadUrl = useCallback((fmt) => {
    if (!jobId) return null;
    return `/api/playlist/${jobId}/result/${fmt}`;
  }, [jobId]);

  return {
    estado, jobId,
    phase, resolved, total, added, notFound, progressPct, statusText, cooldownUntil,
    errorMsg, result,
    submit, cancel, reset, downloadUrl,
  };
}
