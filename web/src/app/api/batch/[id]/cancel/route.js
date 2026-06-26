/**
 * POST /api/batch/[id]/cancel
 * Proxy al backend con revalidación de sesión.
 */

import { NextResponse } from "next/server";
import { getIronSession } from "iron-session";
import { sessionOptions } from "@/lib/auth/session";

const BACKEND = "http://127.0.0.1:8600";
const JOB_ID_RE = /^[0-9a-f-]{36}$/;

export async function POST(request, { params }) {
  // Revalidar sesión (defensa en profundidad, lección B1)
  const sessionResponse = new Response();
  const session = await getIronSession(request, sessionResponse, sessionOptions);
  if (!session?.user?.authenticated) {
    return NextResponse.json({ error: "No autenticado" }, { status: 401 });
  }

  const { id } = await params;

  // Validar formato de job_id antes de reenviar al backend
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
    backendRes = await fetch(`${BACKEND}/batch/${id}/cancel`, {
      method: "POST",
      headers: { "X-Internal-Token": token },
    });
  } catch {
    return NextResponse.json(
      { error: "No se pudo conectar con el backend" },
      { status: 502 }
    );
  }

  const data = await backendRes.json().catch(() => ({}));
  return NextResponse.json(data, { status: backendRes.status });
}
