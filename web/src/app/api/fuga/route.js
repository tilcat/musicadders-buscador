/**
 * POST /api/fuga
 *
 * Proxy al backend FastAPI (http://127.0.0.1:8600/fuga).
 * - Revalida sesión iron-session (defensa en profundidad, lección B1).
 * - Valida y reenvía {date_from, date_to} como JSON al backend.
 * - Añade X-Internal-Token desde env servidor (NUNCA expuesto al browser).
 * - Timeout de 15s: la llamada solo crea el job, responde de inmediato.
 *
 * Respuesta 202: { job_id }
 * Respuesta 401: { error: "no_credentials", message: ... } — FUGA no configurado.
 * Respuesta 422: fecha inválida o date_from > date_to.
 */

import { NextResponse } from "next/server";
import { getIronSession } from "iron-session";
import { sessionOptions } from "@/lib/auth/session";

const BACKEND = "http://127.0.0.1:8600";

export async function POST(request) {
  // 1. Revalidar sesión (defensa en profundidad)
  const sessionResponse = new Response();
  const session = await getIronSession(request, sessionResponse, sessionOptions);
  if (!session?.user?.authenticated) {
    return NextResponse.json({ error: "No autenticado" }, { status: 401 });
  }

  // 2. Leer body JSON
  let body;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Cuerpo JSON inválido" }, { status: 400 });
  }

  const { date_from, date_to } = body ?? {};
  if (!date_from || !date_to) {
    return NextResponse.json(
      { error: "Se requieren date_from y date_to (YYYY-MM-DD)" },
      { status: 422 }
    );
  }

  // 3. Token de servidor
  const token = process.env.INTERNAL_TOKEN;
  if (!token) {
    return NextResponse.json(
      { error: "Backend no configurado (INTERNAL_TOKEN ausente)" },
      { status: 503 }
    );
  }

  // 4. Reenviar al backend
  let backendRes;
  try {
    backendRes = await fetch(`${BACKEND}/fuga`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Internal-Token": token,
      },
      body: JSON.stringify({ date_from, date_to }),
      signal: AbortSignal.timeout(15_000),
    });
  } catch (e) {
    if (e.name === "TimeoutError" || e.name === "AbortError") {
      return NextResponse.json(
        { error: "timeout", message: "El backend tardó demasiado en iniciar el job." },
        { status: 504 }
      );
    }
    return NextResponse.json(
      { error: "No se pudo conectar con el backend" },
      { status: 502 }
    );
  }

  const data = await backendRes.json().catch(() => ({}));
  return NextResponse.json(data, { status: backendRes.status });
}
