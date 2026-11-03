"use client";

import { useState } from "react";
import { API_URL } from "@/lib/api";

export default function EventForm() {
  const [eventType, setEventType] = useState("order.created");
  const [payload, setPayload] = useState('{"order_id": 123, "amount": 499}');
  const [count, setCount] = useState(1);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const submit = async (e: React.SyntheticEvent) => {
    e.preventDefault();

    let parsed: unknown;
    try {
      parsed = JSON.parse(payload);
    } catch {
      setMessage("Payload is not valid JSON");
      return;
    }
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      setMessage("Payload must be a JSON object");
      return;
    }

    setBusy(true);
    let ok = 0;
    let lastError = "";
    for (let i = 0; i < count; i++) {
      try {
        const res = await fetch(`${API_URL}/events/`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            event_type: eventType,
            payload: { ...(parsed as object), n: i },
            idempotency_key: `dash-${crypto.randomUUID()}`,
          }),
        });
        if (res.ok) ok++;
        else lastError = `HTTP ${res.status}`;
      } catch (err) {
        lastError = (err as Error).message;
      }
    }
    setBusy(false);
    setMessage(
      `Sent ${ok}/${count} events` + (lastError ? ` (last error: ${lastError})` : "")
    );
  };

  const input =
    "rounded-md border border-zinc-300 bg-transparent p-2 text-sm dark:border-zinc-700";

  return (
    <form
      onSubmit={submit}
      className="space-y-3 rounded-lg border border-zinc-200 p-4 dark:border-zinc-800"
    >
      <div className="flex gap-3">
        <label className="flex-1 text-sm">
          Event type
          <input
            className={`${input} mt-1 w-full`}
            value={eventType}
            maxLength={30}
            onChange={(e) => setEventType(e.target.value)}
          />
        </label>
        <label className="w-24 text-sm">
          Count
          <input
            type="number"
            min={1}
            max={100}
            className={`${input} mt-1 w-full`}
            value={count}
            onChange={(e) =>
              setCount(Math.min(100, Math.max(1, Number(e.target.value) || 1)))
            }
          />
        </label>
      </div>
      <label className="block text-sm">
        Payload (JSON object)
        <textarea
          className={`${input} mt-1 w-full font-mono`}
          rows={3}
          value={payload}
          onChange={(e) => setPayload(e.target.value)}
        />
      </label>
      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={busy}
          className="rounded-md bg-zinc-900 px-4 py-2 text-sm text-white disabled:opacity-50 dark:bg-zinc-100 dark:text-zinc-900"
        >
          {busy ? "Sending…" : count > 1 ? `Send ${count} events` : "Send event"}
        </button>
        {message && <span className="text-sm text-zinc-500">{message}</span>}
      </div>
    </form>
  );
}