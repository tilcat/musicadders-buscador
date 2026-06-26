"use client";

/**
 * useFugaPolling — ciclo de vida de un job de catálogo FUGA.
 *
 * Patrón idéntico a useBatchPolling: idle → submitting → running → done/cancelled/error.
 * El job_id se persiste en sessionStorage para sobrevivir recargas.
 *
 * Endpoints esperados (proxy Next.js — el token nunca llega al browser):
 *   POST /api/fuga                      { date_from, date_to } → { job_id }
 *   GET  /api/fuga/[id]/status          → { estado, pages_done, pages_total, status_text,
 *                                           isrcs_found, releases_found, error_msg? }
 *   GET  /api/fuga/[id]/result/[fmt]    fmt: json | csv | xlsx_full | xlsx_isrc
 *   POST /api/fuga/[id]/cancel
 *
 * Forma del resultado JSON (fmt=json):
 *   { rows: [{ isrc, product_name, artist_name, label, release_date }],
 *     date_from, date_to, isrcs_total, releases_total }
 */

import { useState, useEffect, useRef, useCallback } from "react";

const POLL_INTERVAL_MS = 2000;           // 2s — FUGA es lento, no saturar
const POLL_TIMEOUT_MS  = 30 * 60 * 1000; // 30 min máximo por job

export function useFugaPolling() {
  const [estado, setEstado]               = useState("idle");
  const [jobId, setJobId]                 = useState(null);
  const [pagesDone, setPagesDone]         = useState(0);
  const [pagesTotal, setPagesTotal]       = useState(null);
  const [statusText, setStatusText]       = useState("");
  const [isrcsFound, setIsrcsFound]       = useState(0);
  const [releasesFound, setReleasesFound] = useState(0);
  const [errorMsg, setErrorMsg]           = useState(null);
  const [result, setResult]               = useState(null);

  const pollRef      = useRef(null);
  const startTimeRef = useRef(null);
  const cancelledRef = useRef(false);

  // ── Restaurar job en curso al montar ──────────────────────────────────────
  useEffect(() => {
    try {
      const saved = sessionStorage.getItem("fuga_job_id");
      if (saved) {
        setJobId(saved);
        setEstado("running");
      }
    } catch (_) {}
  }, []);

  // ── Cleanup interno ───────────────────────────────────────────────────────
  function cleanup() {
    if (pollRef.current) {
      clearTimeout(pollRef.current);
      pollRef.current = null;
    }
    try { sessionStorage.removeItem("fuga_job_id"); } catch (_) {}
  }

  // ── Polling ───────────────────────────────────────────────────────────────
  const startPolling = useCallback((id) => {
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
        setErrorMsg("La búsqueda tardó demasiado. Inténtalo de nuevo con un rango más corto.");
        cleanup();
        return;
      }

      try {
        const res = await fetch(`/api/fuga/${id}/status`);

        // Job expirado o eliminado del servidor (ej. reinicio de svc)
        if (res.status === 404) {
          setEstado("error");
          setErrorMsg("Job no encontrado. Puede que haya expirado o el servidor se haya reiniciado.");
          cleanup();
          return;
        }

        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();

        setPagesDone(data.pages_done ?? 0);
        setPagesTotal(data.pages_total ?? null);
        setStatusText(data.status_text ?? "");
        setIsrcsFound(data.isrcs_found ?? 0);
        setReleasesFound(data.releases_found ?? 0);

        if (data.estado === "done" || data.estado === "cancelled") {
          // Cargar resultado completo
          try {
            const rRes = await fetch(`/api/fuga/${id}/result/json`);
            if (rRes.ok) setResult(await rRes.json());
          } catch (_) {}
          setEstado(data.estado);
          cleanup();
          return;
        }

        if (data.estado === "error") {
          setEstado("error");
          setErrorMsg(data.error_msg ?? "Error consultando FUGA. Inténtalo de nuevo.");
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

  // Si se recuperó un jobId del sessionStorage, arrancar polling.
  // El return del cleanup previene que un tick en vuelo siga corriendo
  // si el componente se desmonta antes de que el job termine.
  useEffect(() => {
    if (estado === "running" && jobId && !startTimeRef.current) {
      startPolling(jobId);
    }
    return () => {
      if (pollRef.current) {
        clearTimeout(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [estado, jobId, startPolling]);

  // ── API pública ───────────────────────────────────────────────────────────

  /**
   * Inicia un job de búsqueda FUGA.
   * @param {string} dateFrom — "YYYY-MM-DD"
   * @param {string} dateTo   — "YYYY-MM-DD"
   */
  const submit = useCallback(async (dateFrom, dateTo) => {
    cleanup();
    cancelledRef.current = false;
    startTimeRef.current = null;

    setEstado("submitting");
    setErrorMsg(null);
    setResult(null);
    setPagesDone(0);
    setPagesTotal(null);
    setStatusText("Iniciando búsqueda…");
    setIsrcsFound(0);
    setReleasesFound(0);

    try {
      const res = await fetch("/api/fuga", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ date_from: dateFrom, date_to: dateTo }),
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        // Caso especial: credenciales FUGA no configuradas en el servidor
        if (res.status === 401 || body.error === "no_credentials") {
          setEstado("no_credentials");
          return;
        }
        throw new Error(body.message ?? body.error ?? `HTTP ${res.status}`);
      }

      const data = await res.json();
      const id = data.job_id;

      setJobId(id);
      setEstado("running");

      try { sessionStorage.setItem("fuga_job_id", id); } catch (_) {}

      startPolling(id);
    } catch (err) {
      setEstado("error");
      setErrorMsg(
        err.name === "TypeError"
          ? "No se pudo conectar con el servidor. Comprueba que está activo."
          : (err.message || "Error inesperado. Inténtalo de nuevo.")
      );
    }
  }, [startPolling]);

  /**
   * Cancela el job en curso.
   *
   * Si POST /cancel devuelve 409 (job ya terminado), trata el estado como 'done'
   * y carga el resultado completo — evita que el usuario vea "cancelado" cuando
   * el job terminó a tiempo en la race window.
   *
   * Si cancela exitosamente, espera 1 segundo para que el worker materialice el
   * resultado parcial y luego lo carga antes de pasar a 'cancelled'.
   */
  const cancel = useCallback(async () => {
    cancelledRef.current = true;
    cleanup();

    if (!jobId) {
      setEstado("cancelled");
      return;
    }

    try {
      const res = await fetch(`/api/fuga/${jobId}/cancel`, { method: "POST" });

      if (res.status === 409) {
        // Race condition: el job terminó justo antes de cancelar → tratar como done
        try {
          const rRes = await fetch(`/api/fuga/${jobId}/result/json`);
          if (rRes.ok) setResult(await rRes.json());
        } catch (_) {}
        setEstado("done");
        return;
      }
    } catch (_) {}

    // Cancelación aceptada: esperar a que el worker materialice el resultado parcial
    await new Promise((resolve) => setTimeout(resolve, 1000));
    try {
      const rRes = await fetch(`/api/fuga/${jobId}/result/json`);
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
    setPagesDone(0);
    setPagesTotal(null);
    setStatusText("");
    setIsrcsFound(0);
    setReleasesFound(0);
    setErrorMsg(null);
    setResult(null);
  }, []);

  /**
   * URL de descarga vía proxy Next.js.
   * @param {"json"|"csv"|"xlsx_full"|"xlsx_isrc"} fmt
   */
  const downloadUrl = useCallback((fmt) => {
    if (!jobId) return null;
    return `/api/fuga/${jobId}/result/${fmt}`;
  }, [jobId]);

  return {
    estado,
    jobId,
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
  };
}
