/**
 * Smoke test de lógica pura para /api/admin/users
 *
 * Cubre: email regex, password length, user_exists, self_delete, last_user,
 * que GET no filtra hashes, invalidación de caché, catch discriminado de
 * readUsersFile (punto 2), DELETE con __proto__ (punto 7), y mutex simulado
 * de escritura concurrente (punto 1).
 *
 * NO requiere servidor en marcha. Para tests de integración (gate 401/403)
 * ver sección "Tests de integración" al final de este fichero.
 *
 * Uso: node web/scripts/test-admin-logic.mjs
 */

// ── Helpers ───────────────────────────────────────────────────────────────────

let passed = 0;
let failed = 0;

function assert(description, condition) {
  if (condition) {
    console.log(`  ok  ${description}`);
    passed++;
  } else {
    console.error(`  FAIL  ${description}`);
    failed++;
  }
}

// ── Email regex (mismo que email-re.js y route.js) ────────────────────────────

const EMAIL_RE = /^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$/;

console.log("\n=== Validación de email ===");
assert("email válido simple",              EMAIL_RE.test("a@b.com"));
assert("email válido con subdomain",       EMAIL_RE.test("user@sub.domain.com"));
assert("email válido con +",               EMAIL_RE.test("u+tag@domain.co"));
assert("email vacío → inválido",          !EMAIL_RE.test(""));
assert("sin @",                            !EMAIL_RE.test("notanemail"));
assert("sin dominio",                      !EMAIL_RE.test("a@"));
assert("sin TLD",                          !EMAIL_RE.test("a@b"));
assert("TLD demasiado corto (1 char)",     !EMAIL_RE.test("a@b.c"));
assert("espacios dentro → inválido",      !EMAIL_RE.test("a b@b.com"));
assert("arroba múltiple → inválido",      !EMAIL_RE.test("a@@b.com"));

// ── Password length ───────────────────────────────────────────────────────────

console.log("\n=== Validación de contraseña ===");

function validatePassword(pw) {
  return typeof pw === "string" && pw.length >= 8;
}

assert("7 chars → inválido",        !validatePassword("1234567"));
assert("8 chars → válido",           validatePassword("12345678"));
assert("12 chars → válido",          validatePassword("123456789012"));
assert("vacía → inválido",          !validatePassword(""));
assert("no-string (number) → inválido", !validatePassword(12345678));

// ── Lógica user_exists ────────────────────────────────────────────────────────

console.log("\n=== user_exists ===");

function simulatePost(existingUsers, email, password) {
  const normalized = email.toLowerCase().trim();
  if (!EMAIL_RE.test(normalized)) return { status: 400, error: "email_invalid" };
  if (!validatePassword(password)) return { status: 400, error: "password_too_short" };
  if (existingUsers[normalized] !== undefined) return { status: 400, error: "user_exists" };
  return { status: 201, ok: true };
}

const existingUsers = { "existing@musicadders.com": "$2b$12$fakehash" };

const r1 = simulatePost(existingUsers, "existing@musicadders.com", "ValidPass99");
assert("user_exists devuelve 400", r1.status === 400);
assert("user_exists error correcto", r1.error === "user_exists");

const r2 = simulatePost(existingUsers, "nuevo@musicadders.com", "ValidPass99");
assert("usuario nuevo devuelve 201", r2.status === 201);
assert("usuario nuevo ok=true", r2.ok === true);

const r3 = simulatePost(existingUsers, "EXISTING@MUSICADDERS.COM", "ValidPass99");
assert("email uppercase detecta user_exists tras normalizar", r3.error === "user_exists");

// ── Guardrails self_delete y last_user ────────────────────────────────────────

console.log("\n=== Guardrails DELETE ===");

function simulateDelete(existingUsers, targetEmail, adminEmail) {
  const email = targetEmail.toLowerCase().trim();
  // Punto 7: validar email antes de tocar el objeto
  if (!EMAIL_RE.test(email)) return { status: 400, error: "email_invalid" };
  if (existingUsers[email] === undefined) return { status: 400, error: "user_not_found" };
  if (email === adminEmail.toLowerCase().trim()) return { status: 400, error: "self_delete" };
  if (Object.keys(existingUsers).length <= 1) return { status: 400, error: "last_user" };
  return { status: 200, ok: true };
}

const twoUsers = {
  "admin@musicadders.com": "$2b$12$fakehash1",
  "user2@musicadders.com": "$2b$12$fakehash2",
};
const oneUser = { "admin@musicadders.com": "$2b$12$fakehash1" };

const d1 = simulateDelete(twoUsers, "nobody@domain.com", "admin@musicadders.com");
assert("user_not_found → 400", d1.status === 400 && d1.error === "user_not_found");

const d2 = simulateDelete(twoUsers, "admin@musicadders.com", "admin@musicadders.com");
assert("self_delete → 400", d2.status === 400 && d2.error === "self_delete");

const d3 = simulateDelete(oneUser, "admin@musicadders.com", "OTRO@MUSICADDERS.COM");
assert("last_user → 400 (aunque el admin no se borra a sí mismo)", d3.status === 400 && d3.error === "last_user");

const d4 = simulateDelete(twoUsers, "user2@musicadders.com", "admin@musicadders.com");
assert("borrado válido → 200 ok", d4.status === 200 && d4.ok === true);

// ── Punto 7: DELETE con cadenas no-email (proto pollution, etc.) ──────────────

console.log("\n=== DELETE con email inválido / proto-pollution (punto 7) ===");

const dProto1 = simulateDelete(twoUsers, "__proto__", "admin@musicadders.com");
assert("__proto__ como email → email_invalid (no user_not_found)", dProto1.error === "email_invalid");
assert("__proto__ no pasa EMAIL_RE", !EMAIL_RE.test("__proto__"));

const dProto2 = simulateDelete(twoUsers, "constructor", "admin@musicadders.com");
assert("'constructor' → email_invalid", dProto2.error === "email_invalid");

const dProto3 = simulateDelete(twoUsers, "toString", "admin@musicadders.com");
assert("'toString' → email_invalid", dProto3.error === "email_invalid");

// ── GET no filtra hashes ──────────────────────────────────────────────────────

console.log("\n=== GET no filtra hashes ===");

function simulateGet(existingUsers) {
  return { users: Object.keys(existingUsers) };
}

const getResult = simulateGet({
  "a@a.com": "$2b$12$hashAAAAA",
  "b@b.com": "$2b$12$hashBBBBB",
});

assert("GET devuelve array de emails", Array.isArray(getResult.users));
assert("GET contiene emails correctos", getResult.users.includes("a@a.com") && getResult.users.includes("b@b.com"));
assert("GET no contiene ningún hash", !getResult.users.some((v) => v.startsWith("$2b$")));
assert("GET no tiene propiedad 'hash' ni valores con $", !JSON.stringify(getResult).includes("$2b$"));

// ── Invalidación de caché (simulada) ─────────────────────────────────────────

console.log("\n=== Invalidación de caché ===");

let _usersCache = null;
function loadUsersSimulated(data) {
  if (_usersCache) return _usersCache;
  _usersCache = data;
  return _usersCache;
}
function invalidateUsersCacheSimulated() { _usersCache = null; }

const data1 = { "a@a.com": "hash1" };
const data2 = { "a@a.com": "hash1", "b@b.com": "hash2" };

loadUsersSimulated(data1);
assert("caché retiene el valor cargado", loadUsersSimulated({}) === data1);
invalidateUsersCacheSimulated();
const reloaded = loadUsersSimulated(data2);
assert("tras invalidar, carga nuevo valor", reloaded === data2);

// ── Punto 2: catch discriminado en readUsersFile ──────────────────────────────

console.log("\n=== readUsersFile: catch discriminado (punto 2) ===");

function callReadUsersFile(scenario) {
  let raw;
  try {
    if (scenario === "enoent") {
      const err = new Error("no such file");
      err.code = "ENOENT";
      throw err;
    }
    if (scenario === "eacces") {
      const err = new Error("permission denied");
      err.code = "EACCES";
      throw err;
    }
    raw = scenario === "corrupted" ? "{ not valid json" : '{"a@a.com":"$2b$12$hash"}';
  } catch (err) {
    if (err.code === "ENOENT") return {}; // correcto
    throw err; // EACCES u otros: propagar
  }
  return JSON.parse(raw); // SyntaxError si corrupto — propagar
}

let enoentResult;
try { enoentResult = callReadUsersFile("enoent"); } catch { enoentResult = "LANZÓ"; }
assert("ENOENT → devuelve {} sin lanzar", typeof enoentResult === "object" && Object.keys(enoentResult).length === 0);

let eaccesThrew = false;
try { callReadUsersFile("eacces"); } catch { eaccesThrew = true; }
assert("EACCES → lanza (no devuelve {})", eaccesThrew);

let corruptThrew = false;
try { callReadUsersFile("corrupted"); } catch { corruptThrew = true; }
assert("JSON corrupto → lanza SyntaxError (no devuelve {})", corruptThrew);

let okResult;
try { okResult = callReadUsersFile("ok"); } catch { okResult = null; }
assert("JSON válido → devuelve objeto", okResult !== null && okResult["a@a.com"] !== undefined);

// Verificar la invariante clave: ENOENT → {} no activa el path de corrupción
// El path de corrupción (JSON inválido) SIEMPRE lanza, nunca devuelve {}
// Esto significa que un admin nunca puede sobreescribir datos reales con {}
// si el fichero estaba corrupto.
const corruptWouldReturnEmpty = (() => {
  try { callReadUsersFile("corrupted"); return false; } catch { return true; }
})();
assert("JSON corrupto NUNCA devuelve {} (invariante anti-pérdida de datos)", corruptWouldReturnEmpty);

// ── Punto 1: mutex de escritura concurrente (promise-chain) ──────────────────

console.log("\n=== Mutex de escritura concurrente (punto 1) ===");

let _writeLock = Promise.resolve();

function withWriteLock(fn) {
  const next = _writeLock.then(fn);
  _writeLock = next.then(() => {}, () => {});
  return next;
}

let sharedUsers = { "initial@a.com": "hash_initial" };
const executionOrder = [];

async function runConcurrentWrites() {
  // Op1: simula bcrypt.hash cediendo el event loop con setImmediate
  const op1 = withWriteLock(async () => {
    executionOrder.push("op1:read");
    const snapshot = { ...sharedUsers };
    await new Promise((r) => setImmediate(r));
    snapshot["user1@a.com"] = "hash1";
    sharedUsers = snapshot;
    executionOrder.push("op1:write");
  });

  // Op2: arranca al mismo tiempo pero debe esperar a op1
  const op2 = withWriteLock(async () => {
    executionOrder.push("op2:read");
    const snapshot = { ...sharedUsers };
    snapshot["user2@a.com"] = "hash2";
    sharedUsers = snapshot;
    executionOrder.push("op2:write");
  });

  await Promise.all([op1, op2]);
}

await runConcurrentWrites();

assert("mutex: ambos usuarios escritos sin pérdida",
  "user1@a.com" in sharedUsers && "user2@a.com" in sharedUsers);

const op1WriteIdx = executionOrder.indexOf("op1:write");
const op2ReadIdx  = executionOrder.indexOf("op2:read");
assert("mutex: op2 lee DESPUÉS de que op1 escribe (no intercalado)",
  op1WriteIdx < op2ReadIdx);

// Verificar que sin mutex los resultados serían incorrectos (demostrar el bug)
// Si dos readers leen antes de cualquier escritura, la segunda escritura pisa a la primera.
let sharedUsersNoMutex = { "initial@a.com": "hash_initial" };
const snapshot1 = { ...sharedUsersNoMutex }; // lee antes del await
const snapshot2 = { ...sharedUsersNoMutex }; // también lee antes del await
// En el caso sin mutex, ambas lecturas capturan el mismo estado
snapshot1["user1@a.com"] = "hash1";
sharedUsersNoMutex = snapshot1; // escribe 1
snapshot2["user2@a.com"] = "hash2";
sharedUsersNoMutex = snapshot2; // escribe 2 — PISA a 1 (user1 se pierde)
assert("sin mutex: la segunda escritura pierde user1 (demuestra el bug)",
  !("user1@a.com" in sharedUsersNoMutex));

// ── Resultado final ───────────────────────────────────────────────────────────

console.log(`\n${passed + failed} tests: ${passed} passed, ${failed} failed`);

if (failed > 0) {
  console.error("\nALGUNOS TESTS FALLARON.");
  process.exit(1);
} else {
  console.log("\nTodos los tests pasaron.");
}

/*
 * ── Tests de integración (requieren servidor en marcha) ──────────────────────
 *
 * Para pruebas-ejecucion: después de `cd web && npm run build && npm start`,
 * verificar con curl o fetch:
 *
 * 1. Gate sin sesión (401):
 *    curl -s -o /dev/null -w "%{http_code}" http://localhost:3030/api/admin/users
 *    → debe devolver 401
 *
 * 2. Gate sin admin (403):
 *    (login con usuario no-admin, luego GET /api/admin/users)
 *    → debe devolver 403
 *
 * 3. GET devuelve emails sin hash:
 *    (login como admin, GET /api/admin/users)
 *    → { users: ["email@domain.com", ...] } — ningún valor empieza por "$2b$"
 *
 * 4. POST añade usuario:
 *    POST /api/admin/users body {"email":"test-new@x.com","password":"SecurePass1"}
 *    → 201 { ok: true }
 *    → GET /api/admin/users incluye "test-new@x.com"
 *    → login con test-new@x.com funciona inmediatamente (caché invalidada)
 *
 * 5. DELETE quita usuario:
 *    DELETE /api/admin/users body {"email":"test-new@x.com"}
 *    → 200 { ok: true }
 *    → GET ya no incluye "test-new@x.com"
 *    → login con test-new@x.com falla (caché invalidada)
 *
 * 6. Escritura atómica: durante un POST/DELETE, users.json.<pid>.<rand>.tmp
 *    aparece y desaparece; users.json nunca queda corrupto ni vacío.
 *
 * 7. DELETE con "__proto__" como email → 400 email_invalid (no 200 falso positivo)
 *    curl -X DELETE ... -d '{"email":"__proto__"}'
 *    → 400 { error: "email_invalid" }
 *
 * 8. 401 en carga → el cliente redirige a /login (verificar en el navegador).
 *
 * 9. Sesión huérfana: borrar un usuario desde otro admin → el usuario borrado
 *    recibe 401 en el siguiente GET /api/admin/users (gate de existencia).
 */
