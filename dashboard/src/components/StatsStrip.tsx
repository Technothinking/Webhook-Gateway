"use client";

import { useEffect, useState } from "react";
import { getStats, Stats } from "@/lib/api";

import Skeleton from "@/components/Skeleton";

function Card({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div
      title={hint}
      className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-800"
    >
      <p className="text-xs text-zinc-500">{label}</p>
      <p className="mt-1 text-xl font-semibold">{value}</p>
    </div>
  );
}

export default function StatsStrip() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const data = await getStats();
        if (alive) {
          setStats(data);
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

  if (error && !stats) return <p className="text-red-500">Can&apos;t reach API: {error}</p>;
  if (!stats)
  return (
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {Array.from({ length: 5 }).map((_, i) => (
          <Skeleton key={i} className="h-16" />
        ))}
      </div>
    );

  const ms = (v: number | null) => (v === null ? "n/a" : `${v} ms`);

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
      <Card label="Total events" value={stats.total_events.toLocaleString()} />
      <Card
        label="Success rate"
        value={
          stats.success_rate === null
            ? "n/a"
            : `${(stats.success_rate * 100).toFixed(1)}%`
        }
        hint="Successful HTTP attempts / all real attempts. Retries count as failures; replays are excluded."
      />
      <Card
        label="p50 latency"
        value={ms(stats.p50_latency_ms)}
        hint="Median delivery request latency"
      />
      <Card
        label="p99 latency"
        value={ms(stats.p99_latency_ms)}
        hint="99th percentile delivery request latency"
      />
      <Card label="Dead letters" value={stats.dead_letters.toLocaleString()} />
    </div>
  );
}