"use client";

/**
 * AdminUserTable
 *
 * Lista los emails registrados en users.json (sin hash — nunca viaja al cliente).
 * Cada fila tiene un botón "Quitar" con inline confirm de dos pasos.
 *
 * Props:
 *   users        string[]    — lista de emails (ya normalizados, sin hash)
 *   removingEmail string|null — email en proceso de DELETE (para deshabilitar su fila)
 *   onRemove     (email: string) => void
 */

import { useState } from "react";

// ── Iconos ────────────────────────────────────────────────────────────────────

function IconUser() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
      <circle cx="12" cy="7" r="4" />
    </svg>
  );
}

function IconTrash() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2" strokeLinecap="round"
      strokeLinejoin="round" aria-hidden="true">
      <polyline points="3 6 5 6 21 6" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <path d="M10 11v6M14 11v6" />
      <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
    </svg>
  );
}

function IconLoader() {
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none"
      stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"
      aria-hidden="true" style={{ animation: "spin 0.8s linear infinite" }}>
      <path d="M21 12a9 9 0 1 1-6.219-8.56" />
    </svg>
  );
}

// ── Fila de usuario ───────────────────────────────────────────────────────────

/**
 * Fila individual con inline-confirm de dos pasos.
 * El estado de confirmación es LOCAL a cada fila, no sube al padre,
 * para que cancelar en una fila no afecte a otras.
 */
function UserRow({ email, isRemoving, isEven, onRemove }) {
  const [confirming, setConfirming] = useState(false);

  function handleRemoveClick() {
    if (!confirming) {
      setConfirming(true);
      return;
    }
    onRemove(email);
    setConfirming(false);
  }

  function handleCancel() {
    setConfirming(false);
  }

  return (
    <tr className={isEven ? "fuga-row-even" : ""}>
      {/* Email — DM Mono, ocupa todo el ancho disponible */}
      <td className="cell-code" style={{ paddingLeft: 14 }}>
        <span className="flex items-center gap-2" style={{ color: "var(--color-text-soft)" }}>
          <IconUser />
          <span style={{ color: "var(--color-text)" }}>{email}</span>
        </span>
      </td>

      {/* Acción */}
      <td style={{ paddingRight: 12, textAlign: "right", width: 1, whiteSpace: "nowrap" }}>
        {isRemoving ? (
          /* Estado: eliminando en servidor */
          <span
            className="inline-flex items-center gap-1.5 text-xs"
            style={{ color: "var(--color-text-muted)" }}
          >
            <IconLoader />
            Quitando…
          </span>
        ) : confirming ? (
          /* Estado: esperando confirmación (inline) */
          <span className="inline-flex items-center gap-2 flex-wrap justify-end">
            <span className="text-xs" style={{ color: "var(--color-danger-text)" }}>
              ¿Quitar acceso?
            </span>
            <button
              type="button"
              onClick={handleRemoveClick}
              className="btn btn-danger"
              style={{ padding: "5px 10px", fontSize: 12 }}
            >
              Confirmar
            </button>
            <button
              type="button"
              onClick={handleCancel}
              className="btn btn-secondary"
              style={{ padding: "5px 10px", fontSize: 12 }}
            >
              Cancelar
            </button>
          </span>
        ) : (
          /* Estado: acción disponible */
          <button
            type="button"
            onClick={handleRemoveClick}
            className="btn btn-danger"
            style={{ padding: "5px 10px", fontSize: 12 }}
            aria-label={`Quitar acceso a ${email}`}
          >
            <IconTrash />
            Quitar
          </button>
        )}
      </td>
    </tr>
  );
}

// ── Tabla principal ───────────────────────────────────────────────────────────

export default function AdminUserTable({ users, removingEmail, onRemove }) {
  return (
    <div className="fuga-table-wrapper">
      <div className="fuga-table-scroll">
        <table className="fuga-table fuga-table--simple">
          <thead>
            <tr>
              <th style={{ paddingLeft: 14 }}>
                <span className="fuga-th-inner">Email</span>
              </th>
              <th style={{ width: 1 }} />
            </tr>
          </thead>
          <tbody>
            {users.length === 0 ? (
              <tr>
                <td colSpan={2} className="fuga-empty-cell">
                  No hay usuarios registrados.
                </td>
              </tr>
            ) : (
              users.map((email, i) => (
                <UserRow
                  key={email}
                  email={email}
                  isEven={i % 2 === 0}
                  isRemoving={removingEmail === email}
                  onRemove={onRemove}
                />
              ))
            )}
          </tbody>
        </table>
      </div>
      <div className="fuga-table-footer">
        <span>
          {users.length === 0
            ? "Sin usuarios"
            : `${users.length} usuario${users.length !== 1 ? "s" : ""}`}
        </span>
      </div>
    </div>
  );
}
