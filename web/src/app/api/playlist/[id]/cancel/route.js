/**
 * POST /api/playlist/[id]/cancel
 *
 * Proxy al backend FastAPI (http://127.0.0.1:8600/playlist/{id}/cancel).
 * - Revalida sesión iron-session.
 * - Valida UUID4 del job_id.
 * - Añade X-Internal-Token desde env servidor.
 * - Timeout de 10s: la cancelación solo activa un flag en memoria + SQLite.
 *
 * Respuesta 200: { ok: true, job_id }
 * Respuesta 409: el job ya está en estado terminal.
 */

import { NextResponse } from "next/server";
import { getIronSession } from "iron-session";
import { sessionOptions } from "@/lib/auth/session";

const BACKEND   = "http://127.0.0.1:8600";
const JOB_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export async function POST(request, { params }) {
  const sessionResponse = new Response();
  const session = await getIronSession(request, sessionResponse, sessionOptions);
  if (!session?.user?.authenticated) {
    return NextResponse.json({ error: "No autenticado" }, { status: 401 });
  }

  const { id } = await params;
  if (!JOB_ID_RE.test(id)) {
    return NextResponse.json({ error: "job_id no válido" }, { status: 400 });
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
    backendRes = await fetch(`${BACKEND}/playlist/${id}/cancel`, {
      method:  "POST",
      headers: { "X-Internal-Token": token },
      signal:  AbortSignal.timeout(10_000),
    });
  } catch (e) {
    if (e.name === "TimeoutError" || e.name === "AbortError") {
      return NextResponse.json(
        { error: "timeout", message: "El backend tardó demasiado en responder." },
        { status: 504 }
      );
    }
    return NextResponse.json({ error: "No se pudo conectar con el backend" }, { status: 502 });
  }

  const data = await backendRes.json().catch(() => ({}));
  return NextResponse.json(data, { status: backendRes.status });
}
