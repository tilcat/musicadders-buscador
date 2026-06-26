"use client";

/**
 * useBatchPolling — hook que gestiona todo el ciclo de vida de un job de batch.
 *
 * Mantiene el estado completo (idle → processing → done/cancelled/error) y
 * PERSISTE el job_id en sessionStorage para sobrevivir recargas de página.
 *
 * IMPORTANTE: todas las llamadas van a /api/batch/* (proxy Next.js servidor),
 * NUNCA directamente al backend FastAPI. El INTERNAL_TOKEN nunca llega al browser.
 *
 * Estados:
 *   idle        → esperando archivo
 *   uploading   → subiendo archivo al proxy
 *   processing  → polling activo
 *   done        → job terminado, result.json cargado
 *   cancelled   → cancelado manualmente (resultado parcial disponible)
 *   error       → fallo irrecuperable (HTTP, timeout, server error)
 */

import { useState, useEffect, useRef, useCallback } from "react";

const POLL_INTERVAL_MS = 1500; // 1.5s entre polls de status
const POLL_TIMEOUT_MS  = 30 * 60 * 1000; // 30min máximo de espera

/**
 * @typedef {Object} BatchResult
 * @property {number} metaCount
 * @property {Array} playlists
 * @property {Array} notFound   — [[isrc, motivo], ...]
 */

export function useBatchPolling() {
  const [estado, setEstado]               = useState("idle");
  const [jobId, setJobId]                 = useState(null);
  const [hechos, setHechos]               = useState(0);
  const [total, setTotal]                 = useState(0);
  const [callsUsed, setCallsUsed]         = useState(0);
  const [notFoundCount, setNotFoundCount] = useState(0);
  const [errorMsg, setErrorMsg]           = useState(null);
  const [result, setResult]               = useState(/** @type {BatchResult|null} */ null);

  const pollRef      = useRef(null);
  const startTimeRef = useRef(null);
  const cancelledRef = useRef(false);

  // ── Restaurar job en curso al montar ─────────────────────────────────────────
  useEffect(() => {
    try {
      const saved = sessionStorage.getItem("batch_job_id");
      if (saved) {
        setJobId(saved);
        setEstado("processing");
      }
    } catch (_) {
      // sessionStorage no disponible — ignorar
    }
  }, []);

  // ── Cleanup ───────────────────────────────────────────────────────────────────
  function cleanup() {
    if (pollRef.current) {
      clearTimeout(pollRef.current);
      pollRef.current = null;
    }
    try { sessionStorage.removeItem("batch_job_id"); } catch (_) {}
  }

  // ── Polling ───────────────────────────────────────────────────────────────────
  const startPolling = useCallback((id) => {
    // Limpiar timer previo antes de arrancar (evita dos ciclos solapados)
    if (pollRef.current) {
      clearTimeout(pollRef.current);
      pollRef.current = null;
    }
    cancelledRef.current = false;
    startTimeRef.current = Date.now();

    async function tick() {
      if (cancelledRef.current) return;

      if (Date.now() - startTimeRef.current > POLL_TIMEOUT_MS) {
        setEstado("error");
        setErrorMsg("El proceso tardó demasiado. Inténtalo de nuevo.");
        cleanup();
        return;
      }

      try {
        // Llama al proxy Next.js — mismo origen, sin token en browser
        const res = await fetch(`/api/batch/${id}/status`);
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        const data = await res.json();

        setHechos(data.hechos ?? 0);
        setTotal(data.total ?? 0);
        setCallsUsed(data.calls_used ?? 0);
        setNotFoundCount(data.not_found_count ?? 0);

        if (data.estado === "done" || data.estado === "cancelled") {
          // Cargar resultado JSON
          try {
            const rRes = await fetch(`/api/batch/${id}/result/json`);
            if (rRes.ok) {
              const rData = await rRes.json();
              // El backend retorna not_found como [isrc_string, ...].
              // El componente BatchResults espera [[isrc, motivo], ...].
              const rawNotFound = rData.not_found ?? [];
              const notFoundPairs = rawNotFound.map((item) =>
                Array.isArray(item) ? item : [item, "no en Soundcharts"]
              );
              setResult({
                metaCount: rData.meta_count ?? 0,
                playlists: rData.playlists ?? [],
                notFound:  notFoundPairs,
              });
            }
          } catch (_) {
            // Resultado parcial sin JSON — seguir con contadores
          }
          setEstado(data.estado);
          cleanup();
          return;
        }

        if (data.estado === "error") {
          setEstado("error");
          setErrorMsg("El servidor reportó un error procesando el lote.");
          cleanup();
          return;
        }

        pollRef.current = setTimeout(tick, POLL_INTERVAL_MS);
      } catch (_err) {
        if (!cancelledRef.current) {
          pollRef.current = setTimeout(tick, POLL_INTERVAL_MS * 2);
        }
      }
    }

    pollRef.current = setTimeout(tick, POLL_INTERVAL_MS);
  }, []);

  // Si se recuperó un jobId del sessionStorage, arrancar polling
  useEffect(() => {
    if (estado === "processing" && jobId && !startTimeRef.current) {
      startPolling(jobId);
    }
  }, [estado, jobId, startPolling]);

  // ── API pública ───────────────────────────────────────────────────────────────

  /**
   * Sube archivo al proxy y arranca el job.
   * @param {File} file
   * @param {string[]} platforms — lista de slugs de plataforma
   */
  const submit = useCallback(async (file, platforms) => {
    // Limpiar cualquier polling previo (job restaurado de sessionStorage + job nuevo)
    cleanup();
    cancelledRef.current = false;
    startTimeRef.current = null;

    setEstado("uploading");
    setErrorMsg(null);
    setResult(null);
    setHechos(0);
    setTotal(0);
    setCallsUsed(0);
    setNotFoundCount(0);

    const fd = new FormData();
    fd.append("file", file);
    fd.append("platforms", JSON.stringify(platforms));

    try {
      // /api/batch — proxy servidor; el browser NUNCA ve INTERNAL_TOKEN
      const res = await fetch("/api/batch", { method: "POST", body: fd });
      if (!res.ok) {
        const body = await res.text().catch(() => "");
        throw new Error(body || `HTTP ${res.status}`);
      }
      const data = await res.json();
      const id = data.job_id;

      setJobId(id);
      setTotal(data.total ?? 0);
      setEstado("processing");

      try { sessionStorage.setItem("batch_job_id", id); } catch (_) {}

      startPolling(id);
    } catch (err) {
      setEstado("error");
      setErrorMsg(
        err.message?.includes("HTTP 4")
          ? "El archivo no es válido o no contiene una columna ISRC reconocible."
          : "No se pudo conectar con el servidor. Comprueba que está activo."
      );
    }
  }, [startPolling]);

  /** Cancela el job en curso */
  const cancel = useCallback(async () => {
    cancelledRef.current = true;
    cleanup();

    if (jobId) {
      try {
        await fetch(`/api/batch/${jobId}/cancel`, { method: "POST" });
      } catch (_) {}
    }

    setEstado("cancelled");
  }, [jobId]);

  /** Volver al estado inicial */
  const reset = useCallback(() => {
    cancelledRef.current = true;
    cleanup();
    startTimeRef.current = null;

    setEstado("idle");
    setJobId(null);
    setHechos(0);
    setTotal(0);
    setCallsUsed(0);
    setNotFoundCount(0);
    setErrorMsg(null);
    setResult(null);
  }, []);

  /**
   * URL de descarga (pasa por el proxy Next.js).
   * @param {"json"|"csv"|"xlsx"} fmt
   */
  const downloadUrl = useCallback((fmt) => {
    if (!jobId) return null;
    return `/api/batch/${jobId}/result/${fmt}`;
  }, [jobId]);

  return {
    estado,
    jobId,
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
  };
}
