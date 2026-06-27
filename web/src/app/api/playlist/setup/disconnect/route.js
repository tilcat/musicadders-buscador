/**
 * POST /api/playlist/setup/disconnect
 *
 * Solo administradores (SPOTIFY_CENTRAL_ADMINS).
 * Elimina el refresh_token de la cuenta central de Spotify del servidor.
 * Tras esta llamada, la creación de playlists quedará deshabilitada hasta
 * que un admin conecte una cuenta nueva desde /playlist/setup.
 *
 * Flujo:
 *   1. Verifica sesión iron-session + admin (primera capa).
 *   2. Llama a POST /playlist/setup/disconnect en el backend.
 *   3. El backend también verifica admin (segunda capa).
 *
 * Respuesta 200: { ok: true, message: "..." }
 * Respuesta 403: no autenticado o no admin.
 */

import { NextResponse } from "next/server";
import { getIronSession } from "iron-session";
import { sessionOptions } from "@/lib/auth/session";
import { isSpotifyAdmin } from "@/lib/auth/spotify-admin";

const BACKEND = "http://127.0.0.1:8600";

export async function POST(request) {
  const sessionResponse = new Response();
  const session = await getIronSession(request, sessionResponse, sessionOptions);
  if (!session?.user?.authenticated) {
    return NextResponse.json({ error: "No autenticado" }, { status: 401 });
  }

  const email = session.user.email ?? "";
  if (!isSpotifyAdmin(email)) {
    return NextResponse.json({ error: "Acceso restringido a administradores." }, { status: 403 });
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
    backendRes = await fetch(`${BACKEND}/playlist/setup/disconnect`, {
      method:  "POST",
      headers: {
        "Content-Type":    "application/json",
        "X-Internal-Token": token,
        "X-User-Email":      email,
      },
      signal: AbortSignal.timeout(10_000),
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
