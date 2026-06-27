/**
 * /api/admin/users — Gestión de usuarios (solo admin)
 *
 * GET    → { users: string[] }          — solo emails, NUNCA hashes
 * POST   body { email, password }
 *        → 201 { ok: true }
 *        → 400 { error: "email_invalid" | "password_too_short" | "user_exists" }
 * DELETE body { email }
 *        → 200 { ok: true }
 *        → 400 { error: "user_not_found" | "self_delete" | "last_user" }
 *
 * Gates comunes: 401 sin sesión, 403 si no admin (isSpotifyAdmin).
 * Fail-closed: si SPOTIFY_CENTRAL_ADMINS no está configurado → 403 para todos.
 *
 * Mutex de escritura: POST y DELETE serializan read→(hash)→write con una
 * promise-chain a nivel de módulo. La verificación de user_exists / user_not_found
 * ocurre DENTRO de la sección crítica para evitar race conditions.
 *
 * Escritura atómica: write a users.json.<pid>.<rand>.tmp → rename (POSIX atomic).
 *
 * Catch de lectura discriminado: ENOENT → {} (normal); otros errores (EACCES,
 * JSON corrupto) → lanza → 500 explícito para no silenciar corrupción.
 *
 * La contraseña y el hash NUNCA aparecen en logs ni viajan al cliente.
 */

import { NextResponse }         from "next/server";
import { getIronSession }       from "iron-session";
import { writeFileSync, readFileSync, renameSync } from "node:fs";
import { resolve }              from "node:path";
import bcrypt                   from "bcryptjs";
import { sessionOptions }       from "@/lib/auth/session";
import { isSpotifyAdmin }       from "@/lib/auth/spotify-admin";
import { invalidateUsersCache } from "@/lib/auth/users";
import { EMAIL_RE }             from "@/lib/auth/email-re";

// ── Rutas ─────────────────────────────────────────────────────────────────────

const USERS_PATH = resolve(process.cwd(), "users.json");

// ── Mutex de escritura ────────────────────────────────────────────────────────
//
// Node.js es single-threaded pero el event loop puede intercalar múltiples
// requests mientras `await bcrypt.hash()` (≈1–2s con cost 12) está en curso.
// Serializar todas las escrituras con una promise-chain evita que dos POST/DELETE
// simultáneos lean el mismo estado y que la última escritura pise a la anterior.

let _writeLock = Promise.resolve();

/**
 * Ejecuta `fn` dentro de la sección crítica de escritura.
 * Garantiza exclusión mutua: las llamadas concurrentes se encolan.
 */
function withWriteLock(fn) {
  const next = _writeLock.then(fn);
  // Actualizar la cola ignorando el resultado (éxito o error) para que la
  // cadena no se rompa ante un fallo puntual.
  _writeLock = next.then(() => {}, () => {});
  return next;
}

// ── Helpers de fichero ────────────────────────────────────────────────────────

/**
 * Lee users.json y devuelve el objeto parseado.
 *
 * - ENOENT → {} (el fichero no existe todavía — normal en instalación nueva)
 * - Cualquier otro error (EACCES, EROFS, JSON inválido/corrupto) → LANZA
 *   para que el llamador pueda devolver 500 explícito en lugar de silenciar
 *   la corrupción y sobrescribir con un {} vacío.
 */
function readUsersFile() {
  let raw;
  try {
    raw = readFileSync(USERS_PATH, "utf8");
  } catch (err) {
    if (err.code === "ENOENT") return {};
    throw err; // EACCES, EROFS, etc. — no silenciar
  }
  // JSON.parse lanza SyntaxError si el fichero está corrupto — también propagar.
  return JSON.parse(raw);
}

/**
 * Escritura atómica con nombre de tmp único.
 * Escribe a un fichero temporal en el mismo directorio y renombra.
 * En POSIX (misma partición) el rename es atómico — nunca hay estado corrupto.
 * El nombre único evita colisiones si varios procesos Node coexistieran.
 */
function writeUsersAtomically(usersObj) {
  const rand    = Math.random().toString(36).slice(2, 8);
  const tmpPath = resolve(process.cwd(), `users.json.${process.pid}.${rand}.tmp`);
  const content = JSON.stringify(usersObj, null, 2) + "\n";
  writeFileSync(tmpPath, content, { encoding: "utf8", flush: true });
  renameSync(tmpPath, USERS_PATH);
}

// ── Gate de autenticación + autorización ──────────────────────────────────────

/**
 * Comprueba:
 *   1. Sesión iron-session válida y autenticada (401 si no).
 *   2. Email del admin en SPOTIFY_CENTRAL_ADMINS (403 si no).
 *   3. Email del admin sigue existiendo en users.json (401 si fue eliminado).
 *      → defensa contra sesiones huérfanas de cuentas borradas.
 *
 * Devuelve { adminEmail } en éxito o { errorResponse } en fallo (fail-closed).
 *
 * NOTA: el check de existencia en users.json aplica solo a /api/admin/*.
 * El resto de rutas protegidas no validan esto todavía (deuda documentada).
 */
async function requireAdmin(request) {
  const sessionResponse = new Response();
  const session = await getIronSession(request, sessionResponse, sessionOptions);

  if (!session?.user?.authenticated) {
    return { errorResponse: NextResponse.json({ error: "No autenticado" }, { status: 401 }) };
  }

  const adminEmail = (session.user.email ?? "").toLowerCase().trim();
  if (!isSpotifyAdmin(adminEmail)) {
    return { errorResponse: NextResponse.json({ error: "Acceso restringido a administradores." }, { status: 403 }) };
  }

  // Verificar que la cuenta todavía existe en users.json.
  // Un admin podría haber sido eliminado por otro admin; su sesión iron-session
  // sigue siendo válida (stateless, TTL largo) pero no debería tener acceso.
  let usersOnDisk;
  try {
    usersOnDisk = readUsersFile();
  } catch (err) {
    console.error("[admin/users] No se pudo leer users.json en gate:", err.code ?? err.message);
    return { errorResponse: NextResponse.json({ error: "Error interno al verificar sesión." }, { status: 500 }) };
  }

  if (usersOnDisk[adminEmail] === undefined) {
    return { errorResponse: NextResponse.json({ error: "Cuenta eliminada o sin acceso." }, { status: 401 }) };
  }

  return { adminEmail };
}

// ── GET /api/admin/users ──────────────────────────────────────────────────────

export async function GET(request) {
  const { errorResponse, adminEmail } = await requireAdmin(request);
  if (errorResponse) return errorResponse;
  void adminEmail; // gate superado

  let users;
  try {
    users = readUsersFile();
  } catch (err) {
    console.error("[admin/users GET] Error al leer users.json:", err.code ?? err.message);
    return NextResponse.json({ error: "storage_read_failed" }, { status: 500 });
  }

  // Solo emails — los hashes NUNCA salen del servidor
  return NextResponse.json({ users: Object.keys(users) });
}

// ── POST /api/admin/users ─────────────────────────────────────────────────────

export async function POST(request) {
  const { errorResponse, adminEmail } = await requireAdmin(request);
  if (errorResponse) return errorResponse;
  void adminEmail;

  let body;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "body_invalid" }, { status: 400 });
  }

  const email    = (body?.email    ?? "").toLowerCase().trim();
  const password =  body?.password ?? "";

  // Validaciones previas al lock (sin coste de I/O)
  if (!EMAIL_RE.test(email)) {
    return NextResponse.json({ error: "email_invalid" }, { status: 400 });
  }
  if (typeof password !== "string" || password.length < 8) {
    return NextResponse.json({ error: "password_too_short" }, { status: 400 });
  }

  // Hashear fuera del lock: es costoso (~1–2 s) y no accede al fichero.
  // La verificación de user_exists ocurre DENTRO del lock para que sea atómica.
  // Hash bcrypt cost 12 — la contraseña NO se almacena en claro ni se loguea.
  const hash = await bcrypt.hash(password, 12);

  return withWriteLock(async () => {
    // Re-leer dentro de la sección crítica para ver el estado más reciente
    let users;
    try {
      users = readUsersFile();
    } catch (err) {
      console.error("[admin/users POST] Error al leer users.json:", err.code ?? err.message);
      return NextResponse.json({ error: "storage_read_failed" }, { status: 500 });
    }

    // Re-validar user_exists dentro del lock (podría haber sido añadido mientras hasheábamos)
    if (users[email] !== undefined) {
      return NextResponse.json({ error: "user_exists" }, { status: 400 });
    }

    users[email] = hash;

    try {
      writeUsersAtomically(users);
    } catch (err) {
      console.error("[admin/users POST] Error al escribir users.json:", err.code ?? err.message);
      return NextResponse.json({ error: "storage_write_failed" }, { status: 500 });
    }

    invalidateUsersCache();
    return NextResponse.json({ ok: true }, { status: 201 });
  });
}

// ── DELETE /api/admin/users ───────────────────────────────────────────────────

export async function DELETE(request) {
  const { errorResponse, adminEmail } = await requireAdmin(request);
  if (errorResponse) return errorResponse;

  let body;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "body_invalid" }, { status: 400 });
  }

  const email = (body?.email ?? "").toLowerCase().trim();

  // Validar formato del email antes de tocar el fichero (evita acceso a __proto__
  // u otras cadenas que no pasan como un email legítimo).
  if (!EMAIL_RE.test(email)) {
    return NextResponse.json({ error: "email_invalid" }, { status: 400 });
  }

  // Guardrail: un admin no puede borrarse a sí mismo (antes del lock, sin I/O)
  if (email === adminEmail) {
    return NextResponse.json({ error: "self_delete" }, { status: 400 });
  }

  return withWriteLock(async () => {
    let users;
    try {
      users = readUsersFile();
    } catch (err) {
      console.error("[admin/users DELETE] Error al leer users.json:", err.code ?? err.message);
      return NextResponse.json({ error: "storage_read_failed" }, { status: 500 });
    }

    if (users[email] === undefined) {
      return NextResponse.json({ error: "user_not_found" }, { status: 400 });
    }

    // Guardrail: no se puede eliminar el último usuario (quedaría sin acceso nadie)
    if (Object.keys(users).length <= 1) {
      return NextResponse.json({ error: "last_user" }, { status: 400 });
    }

    delete users[email];

    try {
      writeUsersAtomically(users);
    } catch (err) {
      console.error("[admin/users DELETE] Error al escribir users.json:", err.code ?? err.message);
      return NextResponse.json({ error: "storage_write_failed" }, { status: 500 });
    }

    invalidateUsersCache();
    return NextResponse.json({ ok: true });
  });
}
