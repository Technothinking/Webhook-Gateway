import { ReactNode } from "react";

export default function Section({
  title,
  how,
  children,
}: {
  title: string;
  how: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-medium">{title}</h2>
      <details className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-100">
        <summary className="cursor-pointer font-medium">How this works</summary>
        <div className="mt-2 space-y-1">{how}</div>
      </details>
      {children}
    </section>
  );
}