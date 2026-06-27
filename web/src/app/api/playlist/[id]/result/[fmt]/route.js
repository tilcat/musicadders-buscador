/**
 * GET /api/playlist/[id]/result/[fmt]
 *
 * Proxy para resultados de playlist en distintos formatos.
 * - Revalida sesión iron-session.
 * - Valida job_id (UUID4) y formato antes de reenviar al backend.
 * - Añade X-Internal-Token desde env servidor.
 * - Timeout de 30s: los ficheros son ligeros pero el backend puede estar ocupado.
 * - Reenvía respuesta binaria con Content-Type y Content-Disposition originales.
 *
 * Formatos aceptados y mapeo al backend:
 *   json           → GET /playlist/{id}/result.json
 *   not_found_csv  → GET /playlist/{id}/result/not_found.csv
 */

import { NextResponse } from "next/server";
import { getIronSession } from "iron-session";
import { sessionOptions } from "@/lib/auth/session";

const BACKEND      = "http://127.0.0.1:8600";
const ALLOWED_FMTS = ["json", "not_found_csv"];
const JOB_ID_RE    = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function backendUrl(id, fmt) {
  switch (fmt) {
    case "json":
      return `${BACKEND}/playlist/${id}/result.json`;
    case "not_found_csv":
      return `${BACKEND}/playlist/${id}/result/not_found.csv`;
    default:
      throw new Error(`Formato desconocido: ${fmt}`);
  }
}

export async function GET(request, { params }) {
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
      signal:  AbortSignal.timeout(30_000),
    });
  } catch (e) {
    if (e.name === "TimeoutError" || e.name === "AbortError") {
      return NextResponse.json(
        { error: "timeout", message: "El backend tardó demasiado en servir el resultado." },
        { status: 504 }
      );
    }
    return NextResponse.json({ error: "No se pudo conectar con el backend" }, { status: 502 });
  }

  if (!backendRes.ok) {
    const data = await backendRes.json().catch(() => ({}));
    return NextResponse.json(data, { status: backendRes.status });
  }

  // Reenviar respuesta binaria con los headers de Content-Type y Content-Disposition
  const body        = await backendRes.arrayBuffer();
  const contentType = backendRes.headers.get("content-type") ?? "application/octet-stream";
  const disposition = backendRes.headers.get("content-disposition") ?? "";

  const headers = new Headers();
  headers.set("content-type", contentType);
  if (disposition) headers.set("content-disposition", disposition);

  return new Response(body, { status: 200, headers });
}
