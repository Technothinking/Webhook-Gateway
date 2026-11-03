import SubscriberList from "@/components/SubscriberList";
import LiveFeed from "@/components/LiveFeed";
import EventForm from "@/components/EventForm";
import OutageToggle from "@/components/OutageToggle";
import StatsStrip from "@/components/StatsStrip";
import DeadLetters from "@/components/DeadLetters";

export default function Home() {
  return (
    <main className="mx-auto w-full max-w-4xl p-8 space-y-6">
      <h1 className="text-2xl font-semibold">Webhook Reliability Gateway</h1>
      <StatsStrip />
      <section className="space-y-3">
        <h2 className="text-lg font-medium">Controls</h2>
        <OutageToggle />
        <EventForm />
      </section>
      <section className="space-y-3">
        <h2 className="text-lg font-medium">Subscribers</h2>
        <SubscriberList />
      </section>
      <section className="space-y-3">
        <h2 className="text-lg font-medium">Live deliveries</h2>
        <LiveFeed />
      </section>
      <section className="space-y-3">
        <h2 className="text-lg font-medium">Dead letters</h2>
        <DeadLetters />
      </section>
    </main>
  );
}