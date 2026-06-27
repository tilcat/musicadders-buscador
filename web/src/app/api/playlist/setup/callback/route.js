/**
 * GET /api/playlist/setup/callback
 *
 * Receptor del callback OAuth de Spotify.
 *
 * Spotify redirige aquí con ?code=...&state=... tras la autorización del usuario.
 * - NO requiere sesión iron-session: el state contiene el email del admin
 *   y el backend lo verifica mediante HMAC.
 * - Llama a POST /playlist/setup/exchange en el backend con {code, state, redirect_uri}.
 * - El backend verifica el HMAC del state, confirma que el email es admin y
 *   que el user_id coincide con SPOTIFY_CENTRAL_EXPECTED_USER_ID (si está configurado).
 * - En caso de éxito redirige a /playlist/setup para que el admin vea el estado.
 * - En caso de error redirige a /playlist/setup?error=... (texto URL-encoded).
 *
 * Nota de seguridad: el `code` y el `state` se pasan directamente al backend
 * (loopback, X-Internal-Token) y nunca se exponen al frontend ni a logs.
 */

import { NextResponse } from "next/server";

const BACKEND = "http://127.0.0.1:8600";

export async function GET(request) {
  const { searchParams } = new URL(request.url);
  const code  = searchParams.get("code");
  const state = searchParams.get("state");
  const error = searchParams.get("error"); // Spotify envía esto si el usuario deniega

  const appBase = (process.env.APP_BASE_URL ?? "http://localhost:3000").replace(/\/$/, "");
  const setupPage = `${appBase}/playlist/setup`;

  // Si el usuario denegó el acceso en Spotify
  if (error) {
    const msg = encodeURIComponent(`Autorización denegada: ${error}`);
    return NextResponse.redirect(`${setupPage}?error=${msg}`);
  }

  if (!code || !state) {
    const msg = encodeURIComponent("Respuesta de Spotify incompleta (code/state ausentes)");
    return NextResponse.redirect(`${setupPage}?error=${msg}`);
  }

  const token = process.env.INTERNAL_TOKEN;
  if (!token) {
    const msg = encodeURIComponent("INTERNAL_TOKEN no configurado en el servidor");
    return NextResponse.redirect(`${setupPage}?error=${msg}`);
  }

  // redirect_uri debe ser idéntico al usado al generar el login_url
  const redirectUri = `${appBase}/api/playlist/setup/callback`;

  let backendRes;
  try {
    backendRes = await fetch(`${BACKEND}/playlist/setup/exchange`, {
      method:  "POST",
      headers: {
        "Content-Type":    "application/json",
        "X-Internal-Token": token,
      },
      body:   JSON.stringify({ code, state, redirect_uri: redirectUri }),
      signal: AbortSignal.timeout(20_000),
    });
  } catch (e) {
    const label = e.name === "TimeoutError" || e.name === "AbortError" ? "timeout" : "backend_error";
    const msg = encodeURIComponent(`Error al completar la autenticación (${label})`);
    return NextResponse.redirect(`${setupPage}?error=${msg}`);
  }

  if (!backendRes.ok) {
    let detail = "";
    try {
      const data = await backendRes.json();
      detail = data?.detail ?? data?.error ?? "";
    } catch {}
    const msg = encodeURIComponent(`Error del backend (${backendRes.status})${detail ? `: ${detail}` : ""}`);
    return NextResponse.redirect(`${setupPage}?error=${msg}`);
  }

  // Éxito: redirigir al panel de administración de la playlist
  return NextResponse.redirect(`${setupPage}?connected=1`);
}
