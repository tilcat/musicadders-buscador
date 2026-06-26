import { NextResponse } from "next/server";
import { getIronSession } from "iron-session";
import { sessionOptions } from "@/lib/auth/session";

/**
 * Rutas que no requieren sesión.
 * IMPORTANTE: /api/auth/* es público a nivel de middleware.
 * CADA route handler de /api/* revalida la sesión internamente
 * (defensa en profundidad — lección B1 de revisor-fuga).
 */
function isPublicPath(pathname) {
  if (pathname === "/login") return true;
  if (pathname.startsWith("/api/auth/")) return true;
  return false;
}

export default async function middleware(request) {
  const { pathname } = request.nextUrl;

  // Dejar pasar assets estáticos y _next
  if (
    pathname.startsWith("/_next") ||
    pathname.startsWith("/_vercel") ||
    /\.(.+)$/.test(pathname)
  ) {
    return NextResponse.next();
  }

  // Rutas públicas
  if (isPublicPath(pathname)) {
    return NextResponse.next();
  }

  // Verificar sesión
  const response = NextResponse.next();
  const session = await getIronSession(request, response, sessionOptions);

  if (!session?.user?.authenticated) {
    // Rutas de API → 401
    if (pathname.startsWith("/api/")) {
      return NextResponse.json({ error: "No autenticado" }, { status: 401 });
    }
    // Páginas → redirect a login
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("from", pathname);
    return NextResponse.redirect(loginUrl);
  }

  return response;
}

export const config = {
  matcher: [
    "/((?!_next|_vercel|.*\\..*).*)",
    "/",
  ],
};
