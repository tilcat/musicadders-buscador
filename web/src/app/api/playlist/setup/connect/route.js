/**
 * GET /api/playlist/setup/connect
 *
 * Solo administradores (SPOTIFY_CENTRAL_ADMINS).
 * Pide al backend la URL de autorización OAuth de Spotify y redirige el
 * navegador hacia ella.  El callback de Spotify llegará a /api/playlist/setup/callback.
 *
 * Flujo:
 *   1. Verifica sesión iron-session + admin.
 *   2. Llama a GET /playlist/setup/connect en el backend (pasa X-User-Email).
 *   3. Redirige al login_url devuelto por el backend.
 */

import { NextResponse } from "next/server";
import { getIronSession } from "iron-session";
import { sessionOptions } from "@/lib/auth/session";
import { isSpotifyAdmin } from "@/lib/auth/spotify-admin";

const BACKEND = "http://127.0.0.1:8600";

export async function GET(request) {
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

  // La redirect_uri la fija el backend desde APP_BASE_URL (.env del svc).
  // GATE de despliegue: registrar {APP_BASE_URL}/api/playlist/setup/callback
  // en developer.spotify.com → App settings → Redirect URIs antes de poner en prod.

  let backendRes;
  try {
    backendRes = await fetch(`${BACKEND}/playlist/setup/connect`, {
      headers: {
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

  if (!backendRes.ok) {
    const data = await backendRes.json().catch(() => ({}));
    return NextResponse.json(data, { status: backendRes.status });
  }

  const { login_url } = await backendRes.json().catch(() => ({}));
  if (!login_url) {
    return NextResponse.json(
      { error: "El backend no devolvió login_url" },
      { status: 502 }
    );
  }

  // Redirigir al usuario a la URL de autorización de Spotify
  return NextResponse.redirect(login_url);
}
