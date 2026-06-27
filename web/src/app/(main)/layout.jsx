import Sidebar from "@/components/layout/Sidebar";
import { getSessionFromCookies } from "@/lib/auth/session";
import { isSpotifyAdmin }        from "@/lib/auth/spotify-admin";

export default async function MainLayout({ children }) {
  // Leer sesión server-side para pasar isAdmin al Sidebar sin fetch extra.
  // Si la sesión no existe o falla, isAdmin = false (fail-closed).
  let isAdmin = false;
  try {
    const session = await getSessionFromCookies();
    if (session?.user?.authenticated) {
      isAdmin = isSpotifyAdmin(session.user.email ?? "");
    }
  } catch {
    // Session inválida o ausente — isAdmin permanece false
  }

  return (
    <div
      className="flex h-screen overflow-hidden"
      style={{ background: "var(--color-bg)" }}
    >
      <Sidebar isAdmin={isAdmin} />
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <main
          className="flex-1 overflow-auto p-6"
          style={{ background: "var(--color-bg)" }}
        >
          {children}
        </main>
      </div>
    </div>
  );
}
