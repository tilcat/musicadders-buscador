/**
 * Carga los usuarios desde web/users.json (gitignored).
 * Formato: { "email@example.com": "$2b$12$hashbcrypt..." }
 *
 * Si el archivo no existe, solo el usuario de fallback de desarrollo
 * (definido en .env.local con DEV_USER_EMAIL/DEV_USER_HASH) estará disponible.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import bcrypt from "bcryptjs";

let _users = null;

function loadUsers() {
  if (_users) return _users;

  _users = {};

  // Cargar desde users.json si existe
  try {
    const usersPath = resolve(process.cwd(), "users.json");
    const raw = readFileSync(usersPath, "utf8");
    _users = JSON.parse(raw);
  } catch {
    // No existe users.json — normal en dev mínimo
  }

  return _users;
}

/**
 * Verifica email + contraseña contra el store de usuarios.
 * @param {string} email
 * @param {string} password
 * @returns {Promise<boolean>}
 */
export async function verifyCredentials(email, password) {
  if (!email || !password) return false;

  const users = loadUsers();
  const hash = users[email.toLowerCase().trim()];

  if (!hash) return false;

  return bcrypt.compare(password, hash);
}
