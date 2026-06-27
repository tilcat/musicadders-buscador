/**
 * EMAIL_RE — regex de validación de email compartida entre cliente y servidor.
 *
 * Misma expresión que el Streamlit original:
 *   [A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}
 *
 * Usada en:
 *   - web/src/app/api/admin/users/route.js  (servidor)
 *   - web/src/components/admin/AdminAddForm.jsx  (cliente, validación UX)
 *
 * Mantener en un único lugar evita divergencia entre ambas validaciones.
 */
export const EMAIL_RE = /^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$/;
