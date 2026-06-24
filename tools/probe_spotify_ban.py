#!/usr/bin/env python3
"""Probe read-only: ¿sigue Spotify rate-limitando nuestra app?

Hace UNA sola llamada (Client Credentials token + 1x GET /search?q=isrc:),
que es exactamente la request que el resolutor usa y la que se rate-limita.
NO crea nada, NO toca playlists. Seguro de correr.

Uso:
  SPOTIFY_CLIENT_ID=xxx SPOTIFY_CLIENT_SECRET=yyy python3 tools/probe_spotify_ban.py [ISRC]

Salida:
  - 200  -> el ban ya pasó, podemos operar.
  - 429  -> seguimos limitados; imprime Retry-After (segundos / horas).
  - otro -> credenciales o problema distinto.
"""
import base64
import os
import sys
import time

import requests

CID = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
CS = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
ISRC = sys.argv[1] if len(sys.argv) > 1 else "ES14H2600001"

if not (CID and CS):
    sys.exit("Faltan SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET en el entorno.")

# 1) Client Credentials token (bucket propio, mismo que usa el resolutor)
auth = base64.b64encode(f"{CID}:{CS}".encode()).decode()
tr = requests.post(
    "https://accounts.spotify.com/api/token",
    headers={"Authorization": f"Basic {auth}",
             "Content-Type": "application/x-www-form-urlencoded"},
    data={"grant_type": "client_credentials"},
    timeout=15,
)
if tr.status_code == 429:
    ra = tr.headers.get("Retry-After", "?")
    print(f"⛔ 429 YA en el token endpoint. Retry-After={ra}s "
          f"(~{int(ra)/3600:.1f}h)" if ra.isdigit() else f"⛔ 429 token. Retry-After={ra}")
    sys.exit(2)
if tr.status_code != 200:
    sys.exit(f"Token falló: HTTP {tr.status_code} {tr.text[:200]}")
tok = tr.json()["access_token"]

# 2) La request que se limita: search por ISRC
t0 = time.time()
r = requests.get(
    "https://api.spotify.com/v1/search",
    headers={"Authorization": f"Bearer {tok}"},
    params={"q": f"isrc:{ISRC}", "type": "track", "limit": 1},
    timeout=15,
)
ms = int((time.time() - t0) * 1000)
print(f"GET /search isrc:{ISRC} -> HTTP {r.status_code} ({ms} ms)")

if r.status_code == 429:
    ra = r.headers.get("Retry-After", "")
    if ra.isdigit():
        s = int(ra)
        print(f"⛔ SIGUE EL BAN. Retry-After = {s}s  (~{s/3600:.2f} h, "
              f"hasta las {time.strftime('%H:%M', time.localtime(time.time()+s))})")
    else:
        print(f"⛔ SIGUE EL BAN. Retry-After (cabecera) = {ra!r}")
    sys.exit(2)
elif r.status_code == 200:
    items = (r.json().get("tracks") or {}).get("items") or []
    print(f"✅ BAN LEVANTADO. Track {'encontrado' if items else 'no encontrado'} "
          f"para {ISRC}. Podemos operar (con el nuevo pacing).")
else:
    print(f"⚠️ Respuesta inesperada: {r.text[:300]}")
    sys.exit(1)
