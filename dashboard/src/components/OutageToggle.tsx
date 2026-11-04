"use client";

import { useCallback, useEffect, useState } from "react";

export default function OutageToggle() {
  const [outage, setOutage] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await fetch("/api/outage", { cache: "no-store" });
      if (!res.ok) throw new Error(`status ${res.status}`);
      setOutage((await res.json()).outage_mode);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 3000);
    return () => clearInterval(id);
  }, [refresh]);

  const toggle = async () => {
    setBusy(true);
    try {
      const res = await fetch("/api/outage", { method: "POST" });
      if (!res.ok) throw new Error(`status ${res.status}`);
      setOutage((await res.json()).outage_mode);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-zinc-200 p-4 sm:flex-row sm:items-center sm:justify-between dark:border-zinc-800">
      <div>
        <p className="font-medium">Demo subscriber</p>
        <p className="text-sm text-zinc-500">
          {error
            ? `Unreachable (${error})`
            : outage === null
            ? "Checking…"
            : outage
            ? "💥 Outage ON: returning HTTP 500"
            : "✅ Healthy"}
        </p>
      </div>
      <button
        onClick={toggle}
        disabled={busy || outage === null}
        className="rounded-md bg-zinc-900 px-4 py-2 text-sm text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
      >
        {outage ? "Restore" : "Kill"}
      </button>
    </div>
  );
}