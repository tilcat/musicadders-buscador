"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

// ── Iconos inline ─────────────────────────────────────────────────────────────

function IconTable(props) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
      <line x1="3" y1="9" x2="21" y2="9" />
      <line x1="3" y1="15" x2="21" y2="15" />
      <line x1="9" y1="3" x2="9" y2="21" />
    </svg>
  );
}

function IconSearch(props) {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <circle cx="11" cy="11" r="8" />
      <path d="m21 21-4.35-4.35" />
    </svg>
  );
}

function IconLogOut(props) {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <polyline points="16 17 21 12 16 7" />
      <line x1="21" y1="12" x2="9" y2="12" />
    </svg>
  );
}

function IconChevronRight(props) {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <polyline points="9 18 15 12 9 6" />
    </svg>
  );
}

// ── Nav items ─────────────────────────────────────────────────────────────────

const NAV_ITEMS = [
  {
    key: "buscar",
    segment: "buscar",
    label: "Buscar 1 ISRC",
    description: "Un ISRC → playlists en DSPs",
    Icon: IconSearch,
  },
  {
    key: "batch",
    segment: "batch",
    label: "Procesar Excel",
    description: "Batch de ISRCs → playlists Soundcharts",
    Icon: IconTable,
  },
];

function NavItem({ href, label, description, Icon, isActive }) {
  return (
    <Link
      href={href}
      className={[
        "w-full flex items-center gap-3 px-3 py-2.5 rounded-[8px] text-left transition-colors text-sm no-underline",
        isActive
          ? "bg-[color:var(--color-accent-bg)] text-[color:var(--color-accent)] font-semibold"
          : "hover:bg-[color:var(--color-surface-raised)]",
      ].join(" ")}
      style={{ color: isActive ? "var(--color-accent)" : "var(--color-text)" }}
      aria-current={isActive ? "page" : undefined}
    >
      <span style={{ color: isActive ? "var(--color-accent)" : "var(--color-text-muted)", flexShrink: 0 }}>
        <Icon />
      </span>
      <span className="flex-1 min-w-0">
        <span className="block truncate">{label}</span>
        {!isActive && (
          <span
            className="block text-[11px] font-normal leading-tight truncate"
            style={{ color: "var(--color-text-muted)" }}
          >
            {description}
          </span>
        )}
      </span>
      {isActive && (
        <IconChevronRight style={{ color: "var(--color-accent)", flexShrink: 0 }} />
      )}
    </Link>
  );
}

// ── Sidebar ───────────────────────────────────────────────────────────────────

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();

  async function handleLogout() {
    await fetch("/api/auth/logout", { method: "POST" });
    router.push("/login");
    router.refresh();
  }

  return (
    <aside
      style={{
        width: 220,
        flexShrink: 0,
        height: "100%",
        borderRight: "1px solid var(--color-border)",
        background: "var(--color-surface)",
        display: "flex",
        flexDirection: "column",
      }}
    >
      {/* Marca */}
      <div
        style={{
          padding: "18px 16px",
          borderBottom: "1px solid var(--color-border)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div
            style={{
              width: 28,
              height: 28,
              borderRadius: 6,
              background: "var(--color-accent)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              flexShrink: 0,
            }}
          >
            <span
              style={{
                color: "white",
                fontWeight: 700,
                fontSize: 13,
                fontFamily: "var(--font-mono)",
                lineHeight: 1,
              }}
            >
              M
            </span>
          </div>
          <div>
            <p
              style={{
                fontSize: 13,
                fontWeight: 600,
                color: "var(--color-text)",
                lineHeight: 1.2,
              }}
            >
              Buscador ISRC
            </p>
            <p
              style={{
                fontSize: 10,
                color: "var(--color-text-muted)",
                fontFamily: "var(--font-mono)",
                lineHeight: 1.2,
              }}
            >
              musicadders · Soundcharts
            </p>
          </div>
        </div>
      </div>

      {/* Nav */}
      <nav style={{ flex: 1, padding: "12px 8px", display: "flex", flexDirection: "column", gap: 2 }}>
        <p
          style={{
            padding: "0 12px",
            marginBottom: 4,
            fontSize: 10,
            fontWeight: 600,
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            color: "var(--color-text-muted)",
          }}
        >
          Herramientas
        </p>
        {NAV_ITEMS.map(({ key, segment, label, description, Icon }) => {
          const isActive = pathname === `/${segment}` || pathname.startsWith(`/${segment}/`);
          return (
            <NavItem
              key={key}
              href={`/${segment}`}
              label={label}
              description={description}
              Icon={Icon}
              isActive={isActive}
            />
          );
        })}
      </nav>

      {/* Footer: logout */}
      <div
        style={{
          padding: "8px",
          paddingBottom: 16,
          borderTop: "1px solid var(--color-border)",
          paddingTop: 12,
        }}
      >
        <button
          onClick={handleLogout}
          style={{
            width: "100%",
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "8px 12px",
            borderRadius: 8,
            fontSize: 13,
            textAlign: "left",
            border: "none",
            background: "transparent",
            color: "var(--color-text-soft)",
            cursor: "pointer",
            transition: "background 150ms ease, color 150ms ease",
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.color = "var(--color-danger)";
            e.currentTarget.style.background = "var(--color-danger-bg)";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.color = "var(--color-text-soft)";
            e.currentTarget.style.background = "transparent";
          }}
        >
          <IconLogOut />
          Cerrar sesión
        </button>
      </div>
    </aside>
  );
}
