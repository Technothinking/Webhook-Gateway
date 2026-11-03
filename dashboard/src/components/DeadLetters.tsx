"use client";

import { useCallback, useEffect, useState } from "react";
import { getDeadLetters, replayDeadLetter, DeadLetterItem } from "@/lib/api";

export default function DeadLetters() {
  const [items, setItems] = useState<DeadLetterItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setItems(await getDeadLetters());
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 3000);
    return () => clearInterval(id);
  }, [load]);

  const replay = async (id: string) => {
    setBusyId(id);
    try {
      await replayDeadLetter(id);
      setNotice("Replay queued: a worker will deliver it shortly");
      await load();
    } catch (e) {
      setNotice(`Replay failed: ${(e as Error).message}`);
    } finally {
      setBusyId(null);
    }
  };

  if (error && !items) return <p className="text-red-500">Can&apos;t reach API: {error}</p>;
  if (!items) return <p className="text-zinc-500">Loading dead letters…</p>;
  if (items.length === 0)
    return <p className="text-zinc-500">No dead letters. Nothing has permanently failed.</p>;

  return (
    <div className="space-y-2">
      {notice && <p className="text-sm text-zinc-500">{notice}</p>}
      <div className="max-h-80 overflow-auto rounded-lg border border-zinc-200 dark:border-zinc-800">
        <table className="w-full text-sm">
          <thead className="sticky top-0 bg-zinc-100 text-left dark:bg-zinc-900">
            <tr>
              <th className="p-2">Moved</th>
              <th className="p-2">Event</th>
              <th className="p-2">Subscriber</th>
              <th className="p-2">Reason</th>
              <th className="p-2">State</th>
              <th className="p-2" />
            </tr>
          </thead>
          <tbody>
            {items.map((d) => (
              <tr key={d.id} className="border-t border-zinc-200 dark:border-zinc-800">
                <td className="p-2 whitespace-nowrap">
                  {new Date(d.moved_at).toLocaleTimeString()}
                </td>
                <td className="p-2">{d.event_type}</td>
                <td className="p-2">{d.subscriber_name}</td>
                <td className="max-w-xs truncate p-2 text-zinc-500" title={d.failed_reason}>
                  {d.failed_reason}
                </td>
                <td className="p-2 whitespace-nowrap">
                  {d.delivered ? (
                    <span className="text-green-600">✅ Delivered</span>
                  ) : d.replay_count > 0 ? (
                    <span className="text-amber-500">
                      Replayed {d.replay_count}×, not delivered
                    </span>
                  ) : (
                    <span className="text-red-500">Failed</span>
                  )}
                </td>
                <td className="p-2 text-right">
                  {!d.delivered && (
                    <button
                      onClick={() => replay(d.id)}
                      disabled={busyId === d.id}
                      className="rounded-md bg-zinc-900 px-3 py-1 text-xs text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
                    >
                      {busyId === d.id ? "Queuing…" : d.replay_count > 0 ? "Replay again" : "Replay"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}