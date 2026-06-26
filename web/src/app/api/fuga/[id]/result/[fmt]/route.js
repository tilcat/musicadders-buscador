/**
 * GET /api/fuga/[id]/result/[fmt]
 *
 * Proxy transparente para resultados FUGA en distintos formatos.
 * - Revalida sesión iron-session (defensa en profundidad, lección B1).
 * - Valida job_id y formato antes de reenviar al backend.
 * - Añade X-Internal-Token desde env servidor.
 * - Timeout de 60s: los xlsx pueden ser ficheros grandes (hasta ~55k filas).
 * - Reenvía la respuesta binaria directamente (Content-Type + Content-Disposition).
 *
 * Formatos aceptados y mapeo al backend:
 *   json       → GET /fuga/{id}/result.json
 *   csv        → GET /fuga/{id}/result.csv
 *   xlsx_full  → GET /fuga/{id}/result.xlsx                (todas las columnas)
 *   xlsx_isrc  → GET /fuga/{id}/result.xlsx?xlsx_type=isrc (solo columna ISRC)
 *
 * Decisión sobre descargas y filtros: este endpoint sirve SIEMPRE el resultado
 * COMPLETO del job. Cuando hay filtros activos (Artista/Sello/Release),
 * FugaResults.jsx NO usa este endpoint: genera el fichero client-side a partir
 * de las filas filtradas (Blob/SheetJS). Sin filtros, abre este endpoint para
 * el resultado completo. Así las descargas respetan los filtros sin necesidad
 * de duplicar la lógica de filtrado en el backend.
 */

import { NextResponse } from "next/server";
import { getIronSession } from "iron-session";
import { sessionOptions } from "@/lib/auth/session";

const BACKEND = "http://127.0.0.1:8600";

const ALLOWED_FMTS = ["json", "csv", "xlsx_full", "xlsx_isrc"];
const JOB_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/**
 * Convierte el formato del frontend a la URL del svc backend.
 * @param {string} id     — job_id
 * @param {string} fmt    — json | csv | xlsx_full | xlsx_isrc
 * @returns {string}      — URL completa del backend
 */
function backendUrl(id, fmt) {
  switch (fmt) {
    case "json":
      return `${BACKEND}/fuga/${id}/result.json`;
    case "csv":
      return `${BACKEND}/fuga/${id}/result.csv`;
    case "xlsx_full":
      return `${BACKEND}/fuga/${id}/result.xlsx`;
    case "xlsx_isrc":
      return `${BACKEND}/fuga/${id}/result.xlsx?xlsx_type=isrc`;
    default:
      throw new Error(`Formato desconocido: ${fmt}`);
  }
}

export async function GET(request, { params }) {
  // Revalidar sesión (defensa en profundidad)
  const sessionResponse = new Response();
  const session = await getIronSession(request, sessionResponse, sessionOptions);
  if (!session?.user?.authenticated) {
    return NextResponse.json({ error: "No autenticado" }, { status: 401 });
  }

  const { id, fmt } = await params;

  if (!JOB_ID_RE.test(id)) {
    return NextResponse.json({ error: "job_id no válido" }, { status: 400 });
  }

  if (!ALLOWED_FMTS.includes(fmt)) {
    return NextResponse.json(
      { error: `Formato no válido. Usa: ${ALLOWED_FMTS.join(", ")}` },
      { status: 400 }
    );
  }

  const token = process.env.INTERNAL_TOKEN;
  if (!token) {
    return NextResponse.json(
      { error: "Backend no configurado (INTERNAL_TOKEN ausente)" },
      { status: 503 }
    );
  }

  let backendRes;
  try {
    backendRes = await fetch(backendUrl(id, fmt), {
      headers: { "X-Internal-Token": token },
      signal: AbortSignal.timeout(60_000),
    });
  } catch (e) {
    if (e.name === "TimeoutError" || e.name === "AbortError") {
      return NextResponse.json(
        { error: "timeout", message: "El backend tardó demasiado en servir el resultado." },
        { status: 504 }
      );
    }
    return NextResponse.json(
      { error: "No se pudo conectar con el backend" },
      { status: 502 }
    );
  }

  if (!backendRes.ok) {
    const data = await backendRes.json().catch(() => ({}));
    return NextResponse.json(data, { status: backendRes.status });
  }

  // Reenviar respuesta binaria con los headers originales de Content-Type y Content-Disposition
  const body        = await backendRes.arrayBuffer();
  const contentType = backendRes.headers.get("content-type") ?? "application/octet-stream";
  const disposition = backendRes.headers.get("content-disposition") ?? "";

  const headers = new Headers();
  headers.set("content-type", contentType);
  if (disposition) headers.set("content-disposition", disposition);

  return new Response(body, { status: 200, headers });
}
