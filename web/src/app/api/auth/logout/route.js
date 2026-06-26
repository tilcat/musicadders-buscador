import { NextResponse } from "next/server";
import { getIronSession } from "iron-session";
import { sessionOptions } from "@/lib/auth/session";

export async function POST(request) {
  const response = NextResponse.json({ ok: true });
  const session = await getIronSession(request, response, sessionOptions);
  session.destroy();
  await session.save();
  return response;
}
