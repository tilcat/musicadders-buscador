/**
 * Comprueba si un email está en la lista SPOTIFY_CENTRAL_ADMINS.
 * Fail-closed: si la variable no está configurada → false.
 * Solo para uso server-side (routes de /api/playlist/setup/*).
 */
export function isSpotifyAdmin(email) {
  const admins = (process.env.SPOTIFY_CENTRAL_ADMINS ?? "")
    .split(",")
    .map((e) => e.trim().toLowerCase())
    .filter(Boolean);
  if (admins.length === 0) return false;
  return admins.includes((email ?? "").toLowerCase().trim());
}
