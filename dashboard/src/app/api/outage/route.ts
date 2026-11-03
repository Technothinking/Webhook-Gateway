import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const DEMO_URL =
  process.env.DEMO_SUBSCRIBER_URL ?? "http://demo-subscriber:8001";

async function proxy(path: string, method: "GET" | "POST") {
  try {
    const res = await fetch(`${DEMO_URL}${path}`, { method, cache: "no-store" });
    return NextResponse.json(await res.json(), { status: res.status });
  } catch {
    return NextResponse.json(
      { error: "Demo subscriber unreachable" },
      { status: 502 }
    );
  }
}

export async function GET() {
  return proxy("/outage", "GET");
}

export async function POST() {
  return proxy("/toggle-outage", "POST");
}