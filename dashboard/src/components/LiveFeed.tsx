"use client";

import { useEffect, useState } from "react";
import { getFeed, FeedItem } from "@/lib/api";

const STATUS_STYLE: Record<string, string> = {
  success: "text-green-600",
  pending_retry: "text-amber-500",
  retry_queued: "text-amber-500",
  deferred: "text-blue-500",
  failed: "text-red-500",
};

function attemptLabel(n: number) {
  if (n > 0) return `#${n}`;
  if (n === -1) return "deferred";
  return "replay";
}

function explain(item: FeedItem, now: number): string | undefined {
  if (item.status === "pending_retry" && item.next_retry_at) {
    const secs = Math.max(
      0,
      Math.round((new Date(item.next_retry_at).getTime() - now) / 1000)
    );
    return `Backing off: next attempt in ~${secs}s`;
  }
  if (item.status === "retry_queued")
    return "Retry queued: a worker will pick it up shortly";
  if (item.status === "deferred")
    return "Subscriber's circuit is open: held until it recovers";
  if (item.status === "failed" && item.attempt_number > 0)
    return "Retries exhausted: moved to the dead-letter queue";
  return undefined;
}

export default function LiveFeed() {
  const [items, setItems] = useState<FeedItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const data = await getFeed(50);
        if (alive) {
          setItems(data);
          setNow(Date.now());
          setError(null);
        }
      } catch (e) {
        if (alive) setError((e as Error).message);
      }
    };
    load();
    const id = setInterval(load, 2000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  if (error && !items) return <p className="text-red-500">Can&apos;t reach API: {error}</p>;
  if (!items) return <p className="text-zinc-500">Loading feed…</p>;
  if (items.length === 0) return <p className="text-zinc-500">No deliveries yet. Fire an event.</p>;

  return (
    <div className="max-h-96 overflow-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
      <table className="w-full text-sm">
        <thead className="sticky top-0 bg-zinc-100 text-left dark:bg-zinc-900">
          <tr>
            <th className="p-2">Time</th>
            <th className="p-2">Event</th>
            <th className="p-2">Subscriber</th>
            <th className="p-2">Attempt</th>
            <th className="p-2">Status</th>
            <th className="p-2 text-right">Latency</th>
          </tr>
        </thead>
        <tbody>
          {items.map((i) => (
            <tr
              key={i.id}
              title={explain(i, now)}
              className="border-t border-zinc-200 dark:border-zinc-800"
            >
              <td className="p-2 whitespace-nowrap">
                {new Date(i.attempted_at).toLocaleTimeString()}
              </td>
              <td className="p-2">{i.event_type}</td>
              <td className="p-2">{i.subscriber_name}</td>
              <td className="p-2">{attemptLabel(i.attempt_number)}</td>
              <td className={`p-2 font-medium ${STATUS_STYLE[i.status] ?? ""}`}>
                {i.status}
                {i.http_status_code ? ` (${i.http_status_code})` : ""}
              </td>
              <td className="p-2 text-right">
                {i.latency_ms != null ? `${i.latency_ms} ms` : "n/a"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}