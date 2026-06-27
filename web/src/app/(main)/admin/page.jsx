"use client";

/**
 * Página: Administración de usuarios — /admin
 *
 * Solo accesible para admins (SPOTIFY_CENTRAL_ADMINS).
 * El enforcement real es server-side (los endpoints de API devuelven 403
 * para no-admins); el cliente detecta el 403 inicial y muestra el estado
 * de acceso restringido.
 *
 * Funciones:
 *   1. Listar usuarios actuales (solo email — el hash NUNCA sale del servidor)
 *   2. Añadir usuario nuevo (email + contraseña → bcrypt cost 12 en servidor)
 *   3. Quitar usuario (con inline confirm de dos pasos — sin window.confirm)
 *
 * Endpoints esperados (todos admin-only, 403 si no cumple):
 *   GET  /api/admin/users
 *     → { users: string[] }            — lista de emails normalizados
 *
 *   POST /api/admin/users
 *     body: { email: string, password: string }
 *     → 201 { ok: true }
 *     → 400 { error: "email_invalid" | "password_too_short" | "user_exists" }
 *     → 403 si no admin
 *
 *   DELETE /api/admin/users
 *     body: { email: string }
 *     → 200 { ok: true }
 *     → 400 { error: "user_not_found" }
 *     → 403 si no admin
 *
 * Patrón de animación: animate-reveal SOLO en hijos directos del root
 * (no en el div raíz). Aprendizaje Fix 15 de F4.
 */

import { useState, useEffect, useCallback } from "react";
import { useRouter } from "next/navigation";
import AdminUserTable from "@/components/admin/AdminUserTable";
import AdminAddForm   from "@/components/admin/AdminAddForm";

// ── Iconos ────────────────────────────────────────────────────────────────────

function IconShield() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    </svg>
  );
}

function IconLoader() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
      aria-hidden="true" style={{ animation: "spin 0.8s linear infinite" }}>
      <path d="M21 12a9 9 0 1 1-6.219-8.56" />
    </svg>
  );
}

function IconCheck() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

// ── Mensajes de error legibles ────────────────────────────────────────────────

const ADD_ERROR_MESSAGES = {
  email_invalid:    "El email no tiene un formato válido.",
  password_too_short: "La contraseña debe tener al menos 8 caracteres.",
  user_exists:      "Este email ya tiene acceso.",
};

const REMOVE_ERROR_MESSAGES = {
  user_not_found: "El usuario no existe en el sistema.",
  self_delete:    "No puedes quitarte el acceso a ti mismo.",
  last_user:      "No se puede eliminar el último usuario con acceso.",
};

// ── Página ────────────────────────────────────────────────────────────────────

export default function AdminPage() {
  const router = useRouter();

  const [users,         setUsers]         = useState([]);
  const [loading,       setLoading]       = useState(true);
  const [isAdminError,  setIsAdminError]  = useState(false);
  const [loadError,     setLoadError]     = useState(null);

  // Feedback de operaciones
  const [addLoading,    setAddLoading]    = useState(false);
  const [addError,      setAddError]      = useState(null);   // string|null
  const [addSuccess,    setAddSuccess]    = useState(null);   // email añadido

  const [removingEmail, setRemovingEmail] = useState(null);   // email en proceso
  const [removeError,   setRemoveError]   = useState(null);   // string|null

  // ── Cargar lista inicial ─────────────────────────────────────────────────

  const loadUsers = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const res = await fetch("/api/admin/users");
      if (res.status === 401) {
        // Sesión expirada o cuenta eliminada — redirigir al login
        router.push("/login");
        return;
      }
      if (res.status === 403) {
        setIsAdminError(true);
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setUsers(data.users ?? []);
    } catch {
      setLoadError("No se pudo cargar la lista de usuarios.");
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => { loadUsers(); }, [loadUsers]);

  // ── Añadir usuario ───────────────────────────────────────────────────────

  /**
   * Devuelve true si el usuario fue añadido con éxito, false en cualquier error.
   * AdminAddForm limpia el formulario solo cuando recibe true.
   */
  async function handleAdd(email, password) {
    setAddLoading(true);
    setAddError(null);
    setAddSuccess(null);
    setRemoveError(null);

    try {
      const res = await fetch("/api/admin/users", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ email, password }),
      });

      if (res.status === 401) { router.push("/login"); return false; }
      if (res.status === 403) { setIsAdminError(true); return false; }

      const data = await res.json();

      if (!res.ok) {
        setAddError(ADD_ERROR_MESSAGES[data?.error] ?? "Error inesperado al añadir el usuario.");
        return false;
      }

      // Éxito: recargar lista y mostrar banner de éxito
      setAddSuccess(email);
      await loadUsers();

      // Auto-limpiar éxito tras 5 s
      setTimeout(() => setAddSuccess(null), 5000);
      return true;
    } catch {
      setAddError("Error de red. Inténtalo de nuevo.");
      return false;
    } finally {
      setAddLoading(false);
    }
  }

  // ── Quitar usuario ───────────────────────────────────────────────────────

  async function handleRemove(email) {
    setRemovingEmail(email);
    setRemoveError(null);
    setAddSuccess(null);

    try {
      const res = await fetch("/api/admin/users", {
        method:  "DELETE",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ email }),
      });

      if (res.status === 403) {
        setIsAdminError(true);
        return;
      }

      const data = await res.json();

      if (!res.ok) {
        setRemoveError(REMOVE_ERROR_MESSAGES[data?.error] ?? "Error al quitar el usuario.");
        return;
      }

      // Éxito: actualizar lista localmente sin re-fetch (UX más rápida)
      setUsers((prev) => prev.filter((u) => u !== email));
    } catch {
      setRemoveError("Error de red. Inténtalo de nuevo.");
    } finally {
      setRemovingEmail(null);
    }
  }

  // ── Render: acceso restringido ───────────────────────────────────────────

  if (isAdminError) {
    return (
      <div className="flex flex-col gap-6 max-w-[520px]">
        <div className="animate-reveal">
          <h1
            className="text-xl font-semibold leading-tight"
            style={{ color: "var(--color-text)", letterSpacing: "-0.01em" }}
          >
            Administración
          </h1>
        </div>
        <div
          className="flex flex-col gap-1 p-5 rounded-xl animate-reveal animate-reveal-delay-1"
          role="alert"
          style={{
            background: "var(--color-danger-bg)",
            border:     "1px solid var(--color-danger-border)",
          }}
        >
          <p className="text-sm font-semibold" style={{ color: "var(--color-danger-text)" }}>
            Acceso restringido
          </p>
          <p className="text-sm" style={{ color: "var(--color-danger-text)", opacity: 0.85 }}>
            Solo administradores pueden acceder a esta sección. Contacta a{" "}
            <a
              href="mailto:victor.gimenez@musicadders.com"
              style={{ color: "var(--color-danger-text)", fontWeight: 600 }}
            >
              victor.gimenez@musicadders.com
            </a>{" "}
            si necesitas acceso.
          </p>
        </div>
      </div>
    );
  }

  // ── Render: página completa ──────────────────────────────────────────────

  return (
    <div className="flex flex-col gap-6 max-w-[640px]">

      {/* ── Cabecera ─────────────────────────────────────────────────────── */}
      <div className="animate-reveal">
        <div
          className="flex items-center gap-2 mb-0.5"
          style={{ color: "var(--color-text-muted)" }}
        >
          <IconShield />
          <span
            className="text-xs font-semibold uppercase tracking-wide"
            style={{ letterSpacing: "0.07em" }}
          >
            Solo administradores
          </span>
        </div>
        <h1
          className="text-xl font-semibold leading-tight"
          style={{ color: "var(--color-text)", letterSpacing: "-0.01em" }}
        >
          Administración de usuarios
        </h1>
        <p className="text-sm mt-1" style={{ color: "var(--color-text-soft)" }}>
          Gestiona quién puede acceder al Buscador ISRC. Los usuarios añadidos
          pueden entrar de inmediato; los eliminados pierden acceso en su
          siguiente inicio de sesión.
        </p>
      </div>

      {/* ── Banner de éxito al añadir ────────────────────────────────────── */}
      {addSuccess && (
        <div
          className="flex items-center gap-3 px-4 py-3 rounded-xl animate-reveal"
          role="status"
          style={{
            background: "var(--color-accent-bg)",
            border:     "1px solid var(--color-success-border)",
          }}
        >
          <span style={{ color: "var(--color-accent)", flexShrink: 0 }}>
            <IconCheck />
          </span>
          <p className="text-sm font-medium" style={{ color: "var(--color-accent-hover)" }}>
            <span
              style={{ fontFamily: "var(--font-mono)", fontWeight: 600 }}
            >
              {addSuccess}
            </span>{" "}
            añadido correctamente.
          </p>
        </div>
      )}

      {/* ── Banner de error al añadir ────────────────────────────────────── */}
      {addError && (
        <div
          className="px-4 py-3 rounded-xl animate-reveal"
          role="alert"
          style={{
            background: "var(--color-danger-bg)",
            border:     "1px solid var(--color-danger-border)",
          }}
        >
          <p className="text-sm font-semibold" style={{ color: "var(--color-danger-text)" }}>
            No se pudo añadir el usuario
          </p>
          <p className="text-xs mt-0.5" style={{ color: "var(--color-danger-text)", opacity: 0.85 }}>
            {addError}
          </p>
        </div>
      )}

      {/* ── Banner de error al quitar ────────────────────────────────────── */}
      {removeError && (
        <div
          className="px-4 py-3 rounded-xl animate-reveal"
          role="alert"
          style={{
            background: "var(--color-danger-bg)",
            border:     "1px solid var(--color-danger-border)",
          }}
        >
          <p className="text-sm font-semibold" style={{ color: "var(--color-danger-text)" }}>
            No se pudo quitar el usuario
          </p>
          <p className="text-xs mt-0.5" style={{ color: "var(--color-danger-text)", opacity: 0.85 }}>
            {removeError}
          </p>
        </div>
      )}

      {/* ── Sección: Usuarios actuales ───────────────────────────────────── */}
      <section className="animate-reveal animate-reveal-delay-1">
        <p
          className="text-xs font-semibold uppercase tracking-wide mb-3"
          style={{ color: "var(--color-text-muted)", letterSpacing: "0.07em" }}
        >
          Usuarios con acceso
        </p>

        {loading ? (
          <div
            className="flex items-center gap-2 px-4 py-5 rounded-xl text-sm"
            style={{
              background: "var(--color-surface)",
              border:     "1px solid var(--color-border)",
              color:      "var(--color-text-muted)",
            }}
          >
            <IconLoader />
            Cargando usuarios…
          </div>
        ) : loadError ? (
          <div
            className="px-4 py-4 rounded-xl text-sm"
            role="alert"
            style={{
              background: "var(--color-danger-bg)",
              border:     "1px solid var(--color-danger-border)",
              color:      "var(--color-danger-text)",
            }}
          >
            {loadError}
          </div>
        ) : (
          <AdminUserTable
            users={users}
            removingEmail={removingEmail}
            onRemove={handleRemove}
          />
        )}
      </section>

      {/* ── Sección: Añadir usuario ──────────────────────────────────────── */}
      <section className="animate-reveal animate-reveal-delay-2">
        <p
          className="text-xs font-semibold uppercase tracking-wide mb-3"
          style={{ color: "var(--color-text-muted)", letterSpacing: "0.07em" }}
        >
          Añadir usuario
        </p>
        <AdminAddForm
          onAdd={handleAdd}
          loading={addLoading}
        />
      </section>

      {/* ── Nota de seguridad ────────────────────────────────────────────── */}
      <p
        className="text-xs animate-reveal animate-reveal-delay-3"
        style={{ color: "var(--color-text-muted)", lineHeight: 1.6 }}
      >
        Los hashes bcrypt se almacenan en{" "}
        <code style={{ fontFamily: "var(--font-mono)" }}>users.json</code>{" "}
        en el servidor. Las contraseñas nunca se almacenan en claro ni viajan
        al cliente.
      </p>
    </div>
  );
}
