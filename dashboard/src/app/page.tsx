import SubscriberList from "@/components/SubscriberList";
import LiveFeed from "@/components/LiveFeed";
import EventForm from "@/components/EventForm";
import OutageToggle from "@/components/OutageToggle";
import StatsStrip from "@/components/StatsStrip";
import DeadLetters from "@/components/DeadLetters";
import Section from "@/components/Section";

export default function Home() {
  return (
    <main className="mx-auto w-full max-w-4xl space-y-8 p-4 sm:p-8">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold">Webhook Reliability Gateway</h1>
        <p className="text-sm text-zinc-500">
          Events are stored, signed, delivered and retried. Kill the demo
          subscriber below to watch it recover.
        </p>
      </header>

      <StatsStrip />

      <Section
        title="Controls"
        how={
          <>
            <p>
              <b>Kill</b> makes the demo subscriber return HTTP 500. Send a few
              events while it is down, then <b>Restore</b> it.
            </p>
            <p>
              Each event gets a unique idempotency key, so a burst of events
              is never deduplicated.
            </p>
          </>
        }
      >
        <OutageToggle />
        <EventForm />
      </Section>

      <Section
        title="Subscribers"
        how={
          <>
            <p>
              🟢 active receives events. 🟡 degraded means the circuit breaker
              opened after 5 consecutive failed attempts: new events are held
              and a health check probes the endpoint. 🔴 paused was stopped
              manually.
            </p>
            <p>Success rate covers real HTTP attempts in the last hour.</p>
          </>
        }
      >
        <SubscriberList />
      </Section>

      <Section
        title="Live deliveries"
        how={
          <>
            <p>
              Every delivery attempt, newest first, refreshed every 2 seconds.
              A failed attempt waits (exponential backoff plus jitter) and is
              retried up to 5 times.
            </p>
            <p>
              <b>deferred</b> means the subscriber&apos;s circuit was open, so the
              event is parked until it recovers. <b>replay</b> is a manual
              redelivery from the dead-letter queue.
            </p>
          </>
        }
      >
        <LiveFeed />
      </Section>

      <Section
        title="Dead letters"
        how={
          <>
            <p>
              A delivery lands here after all 5 attempts fail. Nothing is
              deleted: <b>Replay</b> sends it again to that one subscriber and
              bypasses the retry budget and the circuit breaker.
            </p>
            <p>The record stays for history, with its state updated.</p>
          </>
        }
      >
        <DeadLetters />
      </Section>
    </main>
  );
}