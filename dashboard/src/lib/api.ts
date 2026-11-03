export const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type SubscriberStat = {
  id: string;
  name: string;
  endpoint_url: string;
  status: "active" | "degraded" | "paused" | string;
  attempts: number;
  success_rate: number | null;
};

export async function getSubscriberStats(): Promise<SubscriberStat[]> {
  const res = await fetch(`${API_URL}/stats/subscribers`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export type FeedItem = {
  id: string;
  event_id: string;
  event_type: string;
  subscriber_id: string;
  subscriber_name: string;
  attempt_number: number;
  status: string;
  http_status_code: number | null;
  latency_ms: number | null;
  attempted_at: string;
  next_retry_at: string | null;
};

export async function getFeed(limit = 50): Promise<FeedItem[]> {
  const res = await fetch(`${API_URL}/delivery-attempts?limit=${limit}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export type Stats = {
  total_events: number;
  total_attempts: number;
  success_rate: number | null;
  p50_latency_ms: number | null;
  p99_latency_ms: number | null;
  dead_letters: number;
};

export async function getStats(): Promise<Stats> {
  const res = await fetch(`${API_URL}/stats`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export type DeadLetterItem = {
  id: string;
  event_id: string;
  event_type: string;
  subscriber_id: string;
  subscriber_name: string;
  failed_reason: string;
  moved_at: string;
  replay_count: number;
  delivered: boolean;
};

export async function getDeadLetters(): Promise<DeadLetterItem[]> {
  const res = await fetch(`${API_URL}/dead-letters`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}

export async function replayDeadLetter(id: string): Promise<void> {
  const res = await fetch(`${API_URL}/dead-letters/${id}/replay`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
}