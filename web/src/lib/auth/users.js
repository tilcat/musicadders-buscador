/**
 * Carga los usuarios desde web/users.json (gitignored).
 * Formato: { "email@example.com": "$2b$12$hashbcrypt..." }
 *
 * Si el archivo no existe, devuelve {} y ninguna credencial es válida.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import bcrypt from "bcryptjs";

let _users = null;

/**
 * Carga users.json con catch discriminado:
 *   - ENOENT → {} (fichero no existe, normal en instalación nueva)
 *   - Otros errores (EACCES, JSON inválido/corrupto) → LANZA
 *     para que el llamador reciba el error con causa identificable
 *     en lugar de silenciar la corrupción y denegar acceso a todos.
 */
function loadUsers() {
  if (_users) return _users;

  let raw;
  try {
    raw = readFileSync(resolve(process.cwd(), "users.json"), "utf8");
  } catch (err) {
    if (err.code === "ENOENT") {
      _users = {};
      return _users;
    }
    throw err; // EACCES, EROFS, etc. — propagar
  }

  // JSON.parse lanza SyntaxError si el fichero está corrupto — propagar.
  _users = JSON.parse(raw);
  return _users;
}

/**
 * Invalida la caché en memoria de users.json.
 * Llamar tras cada escritura exitosa (POST/DELETE de /api/admin/users)
 * para que el siguiente login lea el estado actualizado desde disco.
 */
export function invalidateUsersCache() {
  _users = null;
}

/**
 * Verifica email + contraseña contra el store de usuarios.
 * @param {string} email
 * @param {string} password
 * @returns {Promise<boolean>}
 */
export async function verifyCredentials(email, password) {
  if (!email || !password) return false;

  let users;
  try {
    users = loadUsers();
  } catch {
    // Si users.json no se puede leer (corrupto, sin permisos),
    // denegar acceso en lugar de silenciar el error.
    return false;
  }

  const hash = users[email.toLowerCase().trim()];
  if (!hash) return false;

  return bcrypt.compare(password, hash);
}
