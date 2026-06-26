"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

// ── Iconos ────────────────────────────────────────────────────────────────────

function IconLock(props) {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
      <path d="M7 11V7a5 5 0 0 1 10 0v4" />
    </svg>
  );
}

function IconMail(props) {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <rect x="2" y="4" width="20" height="16" rx="2" />
      <path d="m22 7-10 7L2 7" />
    </svg>
  );
}

function IconAlertCircle(props) {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}>
      <circle cx="12" cy="12" r="10" />
      <line x1="12" y1="8" x2="12" y2="12" />
      <line x1="12" y1="16" x2="12.01" y2="16" />
    </svg>
  );
}

function IconLoader(props) {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.5" strokeLinecap="round"
      style={{ animation: "spin 0.8s linear infinite", ...props.style }}
      {...props}>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      <path d="M21 12a9 9 0 1 1-6.219-8.56" />
    </svg>
  );
}

// ── InputField ────────────────────────────────────────────────────────────────

function InputField({ id, label, type = "text", value, onChange, placeholder, Icon, autoComplete, disabled, autoFocus }) {
  const [focused, setFocused] = useState(false);
  return (
    <div>
      <label
        htmlFor={id}
        className="block text-xs font-semibold mb-1.5"
        style={{ color: "var(--color-text-soft)" }}
      >
        {label}
      </label>
      <div
        className="relative flex items-center"
        style={{
          border: `1px solid ${focused ? "var(--color-accent)" : "var(--color-border)"}`,
          borderRadius: "var(--radius-md)",
          background: "var(--color-surface)",
          boxShadow: focused ? "0 0 0 3px rgba(26,158,92,0.12)" : "var(--shadow-inset)",
          transition: "border-color 150ms ease, box-shadow 150ms ease",
        }}
      >
        {Icon && (
          <span
            className="absolute left-3 flex-shrink-0"
            style={{ color: focused ? "var(--color-accent)" : "var(--color-text-muted)" }}
          >
            <Icon />
          </span>
        )}
        <input
          id={id}
          type={type}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          autoComplete={autoComplete}
          autoFocus={autoFocus}
          disabled={disabled}
          required
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          className="w-full bg-transparent outline-none text-sm py-[9px] pr-3"
          style={{
            paddingLeft: Icon ? "38px" : "12px",
            color: "var(--color-text)",
            fontFamily: "var(--font-sans)",
          }}
        />
      </div>
    </div>
  );
}

// ── LoginForm — subcomponente que usa useSearchParams (debe estar en Suspense) ─

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const rawFrom = searchParams.get("from") ?? "/batch";
  const from =
    rawFrom.startsWith("/") && !rawFrom.startsWith("//") && !rawFrom.startsWith("/\\")
      ? rawFrom
      : "/batch";

  const [email, setEmail]       = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading]   = useState(false);
  const [error, setError]       = useState("");

  async function handleSubmit(e) {
    e.preventDefault();
    if (!email.trim() || !password.trim()) return;

    setLoading(true);
    setError("");

    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });

      if (res.ok) {
        router.push(from);
        router.refresh();
      } else {
        const data = await res.json().catch(() => ({}));
        setError(data.error ?? "Credenciales incorrectas");
      }
    } catch {
      setError("Error de red. Comprueba tu conexión e inténtalo de nuevo.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-4">
      <InputField
        id="email"
        label="Email"
        type="email"
        value={email}
        onChange={setEmail}
        placeholder="usuario@musicadders.com"
        Icon={IconMail}
        autoComplete="email"
        autoFocus
        disabled={loading}
      />
      <InputField
        id="password"
        label="Contraseña"
        type="password"
        value={password}
        onChange={setPassword}
        placeholder="••••••••"
        Icon={IconLock}
        autoComplete="current-password"
        disabled={loading}
      />

      {error && (
        <div
          className="flex items-center gap-2 px-3 py-2.5 rounded-[6px] text-sm"
          style={{
            background: "var(--color-danger-bg)",
            border: "1px solid var(--color-danger-border)",
            color: "var(--color-danger)",
          }}
          role="alert"
        >
          <IconAlertCircle style={{ flexShrink: 0 }} />
          <span>{error}</span>
        </div>
      )}

      <button
        type="submit"
        disabled={loading || !email.trim() || !password.trim()}
        className="btn btn-primary w-full justify-center mt-1"
        style={{ padding: "10px 14px", fontSize: "14px" }}
      >
        {loading ? (
          <>
            <IconLoader />
            Entrando…
          </>
        ) : (
          "Entrar"
        )}
      </button>
    </form>
  );
}

// ── LoginPage — export default: envuelve LoginForm en Suspense ────────────────

export default function LoginPage() {
  return (
    <div
      className="min-h-screen flex flex-col items-center justify-center px-4"
      style={{ background: "var(--color-bg)" }}
    >
      <div
        className="w-full max-w-[380px]"
        style={{
          background: "var(--color-surface)",
          border: "1px solid var(--color-border)",
          borderRadius: "var(--radius-lg)",
          boxShadow: "var(--shadow-md)",
          padding: "32px",
        }}
      >
        {/* Marca */}
        <div className="flex flex-col items-center gap-3 mb-8">
          <div
            className="w-11 h-11 flex items-center justify-center rounded-[10px]"
            style={{ background: "var(--color-accent)" }}
          >
            <span
              className="text-white font-bold"
              style={{ fontSize: "18px", fontFamily: "var(--font-mono)" }}
            >
              M
            </span>
          </div>
          <div className="text-center">
            <h1 className="text-lg font-semibold" style={{ color: "var(--color-text)" }}>
              Buscador ISRC
            </h1>
            <p
              className="text-xs mt-0.5"
              style={{ color: "var(--color-text-muted)", fontFamily: "var(--font-mono)" }}
            >
              musicadders · acceso interno
            </p>
          </div>
        </div>

        {/* Formulario en Suspense (requerido por useSearchParams en Next 16) */}
        <Suspense fallback={<div style={{ height: 160 }} />}>
          <LoginForm />
        </Suspense>
      </div>

      <p
        className="mt-5 text-xs text-center max-w-[320px]"
        style={{ color: "var(--color-text-muted)" }}
      >
        Herramienta interna de búsqueda de playlists — musicadders · 2026
      </p>
    </div>
  );
}
