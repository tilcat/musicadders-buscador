/**
 * POST /api/batch
 *
 * Proxy al backend FastAPI (http://127.0.0.1:8600/batch).
 * - Añade X-Internal-Token desde env servidor (NUNCA expuesto al browser).
 * - Revalida sesión (defensa en profundidad, lección B1).
 * - Valida tamaño y extensión del archivo (lección B2).
 * - Convierte el array de plataformas del frontend al campo `scope` del backend.
 *
 * El navegador NUNCA ve INTERNAL_TOKEN.
 */

import { NextResponse } from "next/server";
import { getIronSession } from "iron-session";
import { sessionOptions } from "@/lib/auth/session";

const BACKEND = "http://127.0.0.1:8600";
const MAX_FILE_BYTES = 10 * 1024 * 1024; // 10 MB
const ALLOWED_EXTS = [".xlsx", ".xls", ".csv"];

/** Convierte array de plataformas a scope para el backend. */
function platformsToScope(platforms) {
  const MAIN = ["spotify", "apple-music", "amazon", "deezer"];
  const ALL  = [...MAIN, "youtube", "soundcloud", "tidal", "audiomack", "pandora"];

  if (!Array.isArray(platforms) || platforms.length === 0) return "importantes";

  const sorted = [...platforms].sort();
  const mainSorted = [...MAIN].sort();
  const allSorted  = [...ALL].sort();

  if (JSON.stringify(sorted) === JSON.stringify(mainSorted)) return "importantes";
  if (JSON.stringify(sorted) === JSON.stringify(allSorted))  return "todas";

  // Plataforma individual
  if (platforms.length === 1) return platforms[0];

  // Selección personalizada: usamos "todas" (el backend no soporta lista arbitraria)
  return "todas";
}

export async function POST(request) {
  // 1. Revalidar sesión (defensa en profundidad)
  const sessionResponse = new Response();
  const session = await getIronSession(request, sessionResponse, sessionOptions);
  if (!session?.user?.authenticated) {
    return NextResponse.json({ error: "No autenticado" }, { status: 401 });
  }

  // 2. Leer multipart
  let formData;
  try {
    formData = await request.formData();
  } catch {
    return NextResponse.json({ error: "Cuerpo multipart inválido" }, { status: 400 });
  }

  const file = formData.get("file");
  const platformsRaw = formData.get("platforms");

  if (!file || typeof file === "string") {
    return NextResponse.json({ error: "Campo 'file' requerido" }, { status: 400 });
  }

  // 3. Validar extensión (lección B2)
  const filename = file.name ?? "";
  const lower = filename.toLowerCase();
  if (!ALLOWED_EXTS.some((ext) => lower.endsWith(ext))) {
    return NextResponse.json(
      { error: "Solo se aceptan archivos .xlsx, .xls o .csv" },
      { status: 422 }
    );
  }

  // 4. Validar tamaño (lección B2)
  const fileBytes = await file.arrayBuffer();
  if (fileBytes.byteLength > MAX_FILE_BYTES) {
    return NextResponse.json(
      { error: `El archivo supera el límite de ${MAX_FILE_BYTES / 1024 / 1024} MB` },
      { status: 413 }
    );
  }

  // 5. Parsear plataformas y convertir a scope
  let platforms = [];
  try {
    platforms = platformsRaw ? JSON.parse(platformsRaw) : [];
  } catch {
    platforms = [];
  }
  const scope = platformsToScope(platforms);

  // 6. Construir FormData para el backend
  const backendForm = new FormData();
  backendForm.append(
    "file",
    new Blob([fileBytes], { type: file.type }),
    filename
  );
  backendForm.append("scope", scope);

  // 7. Reenviar al backend con token de servidor
  const token = process.env.INTERNAL_TOKEN;
  if (!token) {
    return NextResponse.json(
      { error: "Backend no configurado (INTERNAL_TOKEN ausente)" },
      { status: 503 }
    );
  }

  let backendRes;
  try {
    backendRes = await fetch(`${BACKEND}/batch`, {
      method: "POST",
      headers: { "X-Internal-Token": token },
      body: backendForm,
    });
  } catch (err) {
    return NextResponse.json(
      { error: "No se pudo conectar con el backend" },
      { status: 502 }
    );
  }

  const data = await backendRes.json().catch(() => ({}));
  return NextResponse.json(data, { status: backendRes.status });
}
