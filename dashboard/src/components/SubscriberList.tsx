"use client";

import { useEffect, useState } from "react";
import { getSubscriberStats, SubscriberStat } from "@/lib/api";

const BADGE: Record<string, { dot: string; label: string }> = {
  active: { dot: "🟢", label: "Active" },
  degraded: { dot: "🟡", label: "Degraded" },
  paused: { dot: "🔴", label: "Paused" },
};

export default function SubscriberList() {
  const [subs, setSubs] = useState<SubscriberStat[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const data = await getSubscriberStats();
        if (alive) {
          setSubs(data);
          setError(null);
        }
      } catch (e) {
        if (alive) setError((e as Error).message);
      }
    };
    load();
    const id = setInterval(load, 3000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  if (error && !subs) return <p className="text-red-500">Can&apos;t reach API: {error}</p>;
  if (!subs) return <p className="text-zinc-500">Loading subscribers…</p>;
  if (subs.length === 0) return <p className="text-zinc-500">No subscribers yet.</p>;

  return (
    <ul className="divide-y divide-zinc-200 dark:divide-zinc-800 rounded-lg border border-zinc-200 dark:border-zinc-800">
      {subs.map((s) => {
        const badge = BADGE[s.status] ?? { dot: "⚪", label: s.status };
        return (
          <li key={s.id} className="flex items-center justify-between p-4">
            <div className="min-w-0">
              <p className="font-medium">{s.name}</p>
              <p className="truncate text-sm text-zinc-500">{s.endpoint_url}</p>
            </div>
            <div className="text-right text-sm">
              <p>{badge.dot} {badge.label}</p>
              <p className="text-zinc-500">
                {s.success_rate === null
                  ? "no recent traffic"
                  : `${(s.success_rate * 100).toFixed(1)}% success (${s.attempts})`}
              </p>
            </div>
          </li>
        );
      })}
    </ul>
  );
}