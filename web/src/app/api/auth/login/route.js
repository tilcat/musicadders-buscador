import { NextResponse } from "next/server";
import { getIronSession } from "iron-session";
import { sessionOptions } from "@/lib/auth/session";
import { verifyCredentials } from "@/lib/auth/users";

export async function POST(request) {
  let body;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Cuerpo JSON inválido" }, { status: 400 });
  }

  const { email, password } = body ?? {};

  if (!email || !password) {
    return NextResponse.json(
      { error: "Email y contraseña son obligatorios" },
      { status: 400 }
    );
  }

  const valid = await verifyCredentials(email, password);

  if (!valid) {
    // Respuesta genérica para no dar pistas
    return NextResponse.json(
      { error: "Credenciales incorrectas" },
      { status: 401 }
    );
  }

  const response = NextResponse.json({ ok: true });
  const session = await getIronSession(request, response, sessionOptions);
  session.user = { authenticated: true, email: email.toLowerCase().trim() };
  await session.save();

  return response;
}
