"use client";

/**
 * AdminAddForm
 *
 * Formulario para añadir un nuevo usuario: email + contraseña.
 * El hash bcrypt (cost 12) se genera en el servidor; la contraseña
 * NUNCA viaja de vuelta al cliente.
 *
 * Validación client-side (mínima, para UX):
 *   - Email: formato básico (regex RFC-light)
 *   - Contraseña: mínimo 8 caracteres (obligatorio), mínimo 12 recomendado
 *
 * Props:
 *   onAdd   (email: string, password: string) => Promise<void>
 *   loading boolean
 */

import { useState } from "react";
import { EMAIL_RE } from "@/lib/auth/email-re";

// ── Icono ─────────────────────────────────────────────────────────────────────

function IconUserPlus() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <line x1="19" y1="8" x2="19" y2="14" />
      <line x1="22" y1="11" x2="16" y2="11" />
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

// ── Helpers de validación ─────────────────────────────────────────────────────

// EMAIL_RE se importa desde @/lib/auth/email-re (mismo que el servidor)
// para garantizar que cliente y servidor usan la misma validación.
const MIN_PW = 8;
const REC_PW = 12;

function validateForm(email, password) {
  if (!EMAIL_RE.test(email.trim())) return "Introduce un email válido.";
  if (password.length < MIN_PW)       return `La contraseña debe tener al menos ${MIN_PW} caracteres.`;
  return null;
}

// ── Componente ────────────────────────────────────────────────────────────────

export default function AdminAddForm({ onAdd, loading }) {
  const [email,    setEmail]    = useState("");
  const [password, setPassword] = useState("");
  const [localErr, setLocalErr] = useState(null);

  const pwLen         = password.length;
  const pwTooShort    = pwLen > 0 && pwLen < MIN_PW;
  const pwBelowRec    = pwLen >= MIN_PW && pwLen < REC_PW;
  const pwStrong      = pwLen >= REC_PW;

  async function handleSubmit(e) {
    e.preventDefault();
    setLocalErr(null);

    const err = validateForm(email, password);
    if (err) {
      setLocalErr(err);
      return;
    }

    const ok = await onAdd(email.trim().toLowerCase(), password);
    // Limpiar el formulario solo si la operación fue exitosa.
    // En error (email_invalid, user_exists, etc.) se conservan los campos
    // para que el usuario pueda corregir sin reescribir desde cero.
    if (ok) {
      setEmail("");
      setPassword("");
    }
  }

  return (
    <form onSubmit={handleSubmit} noValidate>
      <div
        className="flex flex-col gap-4 p-5 rounded-xl"
        style={{
          background: "var(--color-surface)",
          border:     "1px solid var(--color-border)",
          boxShadow:  "var(--shadow-sm)",
        }}
      >

        {/* Email */}
        <div className="flex flex-col gap-1.5">
          <label
            htmlFor="admin-email"
            className="text-xs font-semibold uppercase tracking-wide"
            style={{ color: "var(--color-text-soft)" }}
          >
            Email
          </label>
          <input
            id="admin-email"
            type="email"
            autoComplete="off"
            spellCheck={false}
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
              setLocalErr(null);
            }}
            placeholder="usuario@musicadders.com"
            disabled={loading}
            className="fuga-input"
            aria-describedby={localErr ? "admin-form-error" : undefined}
          />
        </div>

        {/* Contraseña */}
        <div className="flex flex-col gap-1.5">
          <label
            htmlFor="admin-password"
            className="text-xs font-semibold uppercase tracking-wide"
            style={{ color: "var(--color-text-soft)" }}
          >
            Contraseña
          </label>
          <input
            id="admin-password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => {
              setPassword(e.target.value);
              setLocalErr(null);
            }}
            placeholder="Mínimo 8 caracteres"
            disabled={loading}
            className="fuga-input"
            aria-describedby="admin-pw-hint admin-form-error"
            style={
              pwTooShort
                ? { borderColor: "var(--color-danger-border)" }
                : undefined
            }
          />

          {/* Indicador de fortaleza (mínima UX, sin meter una barra entera) */}
          <p
            id="admin-pw-hint"
            className="text-xs"
            style={{
              color: pwTooShort
                ? "var(--color-danger-text)"
                : pwBelowRec
                  ? "var(--color-warning-text)"
                  : pwStrong
                    ? "var(--color-accent)"
                    : "var(--color-text-muted)",
            }}
          >
            {pwTooShort
              ? `Mínimo ${MIN_PW} caracteres.`
              : pwBelowRec
                ? `Funcional, pero se recomiendan ${REC_PW}+.`
                : pwStrong
                  ? "Contraseña aceptable."
                  : `Mínimo ${MIN_PW} caracteres; se recomiendan ${REC_PW}+.`}
          </p>
        </div>

        {/* Error de validación local (o reenviado desde el servidor vía prop) */}
        {localErr && (
          <p
            id="admin-form-error"
            className="text-xs"
            role="alert"
            style={{ color: "var(--color-danger-text)" }}
          >
            {localErr}
          </p>
        )}

        {/* Separador + acción */}
        <div style={{ height: 1, background: "var(--color-border)" }} />

        <div className="flex items-center gap-3 flex-wrap">
          <button
            type="submit"
            disabled={loading || !email || !password || pwTooShort}
            className="btn btn-primary self-start"
          >
            {loading ? (
              <><IconLoader /> Añadiendo…</>
            ) : (
              <><IconUserPlus /> Añadir usuario</>
            )}
          </button>
          <p className="text-xs" style={{ color: "var(--color-text-muted)" }}>
            El hash bcrypt se genera en el servidor. La contraseña no se almacena
            en claro.
          </p>
        </div>
      </div>
    </form>
  );
}
