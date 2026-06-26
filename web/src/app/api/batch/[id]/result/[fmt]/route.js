/**
 * GET /api/batch/[id]/result/[fmt]
 *
 * Proxy transparente para result.json, result.csv y result.xlsx.
 * Reenvía la respuesta binaria del backend directamente, incluyendo
 * Content-Type y Content-Disposition.
 *
 * Revalida sesión (defensa en profundidad, lección B1).
 */

import { NextResponse } from "next/server";
import { getIronSession } from "iron-session";
import { sessionOptions } from "@/lib/auth/session";

const BACKEND = "http://127.0.0.1:8600";

const ALLOWED_FMTS = ["json", "csv", "xlsx"];
const JOB_ID_RE = /^[0-9a-f-]{36}$/;

export async function GET(request, { params }) {
  // Revalidar sesión (defensa en profundidad)
  const sessionResponse = new Response();
  const session = await getIronSession(request, sessionResponse, sessionOptions);
  if (!session?.user?.authenticated) {
    return NextResponse.json({ error: "No autenticado" }, { status: 401 });
  }

  const { id, fmt } = await params;

  // Validar formato de job_id antes de reenviar al backend
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
    backendRes = await fetch(`${BACKEND}/batch/${id}/result.${fmt}`, {
      headers: { "X-Internal-Token": token },
    });
  } catch {
    return NextResponse.json(
      { error: "No se pudo conectar con el backend" },
      { status: 502 }
    );
  }

  if (!backendRes.ok) {
    const data = await backendRes.json().catch(() => ({}));
    return NextResponse.json(data, { status: backendRes.status });
  }

  // Reenviar respuesta binaria
  const body = await backendRes.arrayBuffer();
  const contentType = backendRes.headers.get("content-type") ?? "application/octet-stream";
  const disposition = backendRes.headers.get("content-disposition") ?? "";

  const headers = new Headers();
  headers.set("content-type", contentType);
  if (disposition) headers.set("content-disposition", disposition);

  return new Response(body, { status: 200, headers });
}
