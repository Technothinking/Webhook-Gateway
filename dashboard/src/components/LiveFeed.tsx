"use client";

import { useEffect, useState } from "react";
import { getFeed, FeedItem } from "@/lib/api";
import Skeleton from "@/components/Skeleton";
import Hint from "@/components/Hint";

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
    return secs === 0
      ? "Backoff over: waiting for the retry scheduler or an active subscriber"
      : `Backing off: next attempt in ~${secs}s`;
  }
  if (item.status === "retry_queued")
    return "Retry queued: a worker will pick it up shortly";
  if (item.status === "deferred")
    return "Circuit open: held until the subscriber recovers";
  if (item.status === "failed" && item.attempt_number > 0)
    return "Retries exhausted: moved to dead letters";
  if (item.status === "failed") return "Replay failed";
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
  if (!items) return <Skeleton className="h-48" />;
  if (items.length === 0)
    return (
      <div className="rounded-lg border border-dashed border-zinc-300 p-6 text-center text-sm text-zinc-500 dark:border-zinc-700">
        No deliveries yet. Use <b>Send event</b> above to create one.
      </div>
    );

  return (
    <div className="space-y-2">
      {error && (
        <p className="text-sm text-amber-500">
          Connection lost, showing the last data ({error}).
        </p>
      )}
      <div className="max-h-96 overflow-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-zinc-100 text-left dark:bg-zinc-900">
            <tr>
              <th className="p-2">Time</th>
              <th className="hidden p-2 sm:table-cell">Event</th>
              <th className="p-2">Subscriber</th>
              <th className="p-2">
                Attempt
                <Hint text="#1 to #5 are real delivery attempts. 'replay' is a manual redelivery. 'deferred' is a parked event." />
              </th>
              <th className="p-2">Status</th>
              <th className="hidden p-2 text-right sm:table-cell">Latency</th>
            </tr>
          </thead>
          <tbody>
            {items.map((i) => {
              const note = explain(i, now);
              return (
                <tr key={i.id} className="border-t border-zinc-200 align-top dark:border-zinc-800">
                  <td className="whitespace-nowrap p-2">
                    {new Date(i.attempted_at).toLocaleTimeString()}
                  </td>
                  <td className="hidden p-2 sm:table-cell">{i.event_type}</td>
                  <td className="p-2">{i.subscriber_name}</td>
                  <td className="p-2">{attemptLabel(i.attempt_number)}</td>
                  <td className="p-2">
                    <span className={`font-medium ${STATUS_STYLE[i.status] ?? ""}`}>
                      {i.status}
                      {i.http_status_code ? ` (${i.http_status_code})` : ""}
                    </span>
                    {note && <p className="text-xs text-zinc-500">{note}</p>}
                  </td>
                  <td className="hidden p-2 text-right sm:table-cell">
                    {i.latency_ms != null ? `${i.latency_ms} ms` : "n/a"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}