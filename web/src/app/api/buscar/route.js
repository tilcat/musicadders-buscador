/**
 * GET /api/buscar?isrc={isrc}&scope={scope}&bust={bust}
 *
 * Proxy al backend FastAPI (http://127.0.0.1:8600/search).
 * - Valida sesión (iron-session, defensa en profundidad).
 * - Valida formato ISRC (regex, antes de llamar al backend).
 * - Valida scope contra lista blanca.
 * - Añade X-Internal-Token desde env servidor (NUNCA expuesto al browser).
 * - Reenvía `bust` al backend para forzar cache-miss en Soundcharts.
 * - Timeout de 35s con AbortSignal.timeout (> 15+20s del svc) → 504.
 *
 * Contrato esperado de svc/ (GET /search):
 *   Query params: isrc, scope, bust
 *   200 OK:
 *   {
 *     meta: {
 *       song_name: string,
 *       credit_name: string,
 *       release_date: string,   // ISO "YYYY-MM-DD..."
 *     } | null,                 // null = ISRC no encontrado en Soundcharts
 *     playlists: [
 *       {
 *         platform: string,        // slug: "spotify", "apple-music", ...
 *         playlist_name: string,
 *         playlist_type: string,   // raw de Soundcharts: "Editorial", "Algorithmic"...
 *         subscriber_count: number | null,
 *         position: number | null,
 *       }
 *     ],
 *     calls_used: number,
 *     elapsed_ms: number,
 *     platforms_count: number,   // DSPs con ≥1 resultado
 *     total_platforms: number,   // DSPs consultadas según el scope
 *   }
 *   422 Unprocessable: { error: string }
 *   429 Rate limit: { error: "rate_limited" | "rate_limit_daily", message: string }
 *   502 Bad Gateway: { error: string }  ← svc caído
 *   503 Service Unavailable: { error: string }  ← token/credenciales no configurados
 *   504 Gateway Timeout: { error: "timeout", message: string }  ← svc >35s
 */

import { NextResponse } from "next/server";
import { getIronSession } from "iron-session";
import { sessionOptions } from "@/lib/auth/session";

const BACKEND = "http://127.0.0.1:8600";

const ISRC_RE = /^[A-Za-z]{2}[A-Za-z0-9]{3}\d{7}$/;

const VALID_SCOPES = new Set([
  "importantes", "todas",
  "spotify", "apple-music", "amazon", "deezer",
  "youtube", "soundcloud", "tidal", "audiomack", "pandora",
]);

export async function GET(request) {
  // 1. Revalidar sesión
  const sessionResponse = new Response();
  const session = await getIronSession(request, sessionResponse, sessionOptions);
  if (!session?.user?.authenticated) {
    return NextResponse.json({ error: "No autenticado" }, { status: 401 });
  }

  const { searchParams } = new URL(request.url);
  const isrc  = (searchParams.get("isrc") ?? "").trim().toUpperCase();
  const scope = (searchParams.get("scope") ?? "importantes").trim();
  const bust  = (searchParams.get("bust") ?? "").trim();

  // 2. Validar ISRC
  if (!ISRC_RE.test(isrc)) {
    return NextResponse.json(
      { error: "ISRC inválido — formato esperado: 2 letras + 3 alfanuméricos + 7 dígitos" },
      { status: 422 }
    );
  }

  // 3. Validar scope
  if (!VALID_SCOPES.has(scope)) {
    return NextResponse.json(
      { error: `Scope '${scope}' no válido` },
      { status: 422 }
    );
  }

  // 4. Token de servidor
  const token = process.env.INTERNAL_TOKEN;
  if (!token) {
    return NextResponse.json(
      { error: "Backend no configurado (INTERNAL_TOKEN ausente)" },
      { status: 503 }
    );
  }

  // 5. Llamada al backend FastAPI (timeout 35s > 15+20s del svc)
  let backendRes;
  const bustSuffix = bust ? `&bust=${encodeURIComponent(bust)}` : "";
  const url = `${BACKEND}/search?isrc=${encodeURIComponent(isrc)}&scope=${encodeURIComponent(scope)}${bustSuffix}`;

  try {
    backendRes = await fetch(url, {
      headers: { "X-Internal-Token": token },
      // No usamos cache de fetch de Next.js: cada búsqueda debe ser fresca
      cache: "no-store",
      signal: AbortSignal.timeout(35_000),
    });
  } catch (e) {
    // AbortSignal.timeout dispara "TimeoutError" en runtimes modernos (Node 18+/undici).
    // Por compatibilidad también manejamos "AbortError" que pueden emitir versiones antiguas.
    if (e.name === "TimeoutError" || e.name === "AbortError") {
      return NextResponse.json(
        {
          error: "timeout",
          message: "El backend tardó más de 35s en responder. Inténtalo de nuevo.",
        },
        { status: 504 }
      );
    }
    return NextResponse.json(
      { error: "No se pudo conectar con el backend — comprueba que el servidor está activo" },
      { status: 502 }
    );
  }

  const data = await backendRes.json().catch(() => ({}));
  return NextResponse.json(data, { status: backendRes.status });
}
