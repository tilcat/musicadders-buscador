import { getIronSession } from "iron-session";

// Fail-closed: si SESSION_SECRET no está definido o es demasiado corto,
// lanzamos en el momento de carga del módulo para que el servidor no arranque
// con una clave vacía/débil.
const _rawPassword = process.env.SESSION_SECRET ?? "";
if (_rawPassword.length < 32) {
  throw new Error(
    "SESSION_SECRET debe tener al menos 32 caracteres. " +
      "Configúrala en web/.env.local antes de arrancar."
  );
}

/** @type {import("iron-session").SessionOptions} */
export const sessionOptions = {
  cookieName: "mb_session",
  password: _rawPassword,
  cookieOptions: {
    // Secure por defecto (la app se expone en internet vía HTTPS).
    // Solo desactivar en local explícitamente con COOKIE_SECURE=false.
    secure: process.env.COOKIE_SECURE !== "false",
    httpOnly: true,
    sameSite: "lax",
  },
};

/**
 * Obtiene la sesión desde las cookies de Next.js (App Router / Server Components).
 * Solo callable en server context.
 */
export async function getSessionFromCookies() {
  const { cookies } = await import("next/headers");
  const cookieStore = await cookies();
  return getIronSession(cookieStore, sessionOptions);
}
