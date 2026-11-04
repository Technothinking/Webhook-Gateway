export default function Hint({ text }: { text: string }) {
  return (
    <span className="group relative inline-flex align-middle">
      <button
        type="button"
        aria-label={text}
        className="ml-1 h-4 w-4 rounded-full border border-zinc-400 text-[10px] leading-none text-zinc-500 focus:outline-none focus:ring-2 focus:ring-blue-400"
      >
        i
      </button>
      <span
        role="tooltip"
        className="pointer-events-none absolute left-0 top-full z-20 mt-1 hidden w-56 rounded-md bg-zinc-900 p-2 text-left text-xs font-normal text-white shadow group-focus-within:block group-hover:block dark:bg-zinc-100 dark:text-zinc-900"
      >
        {text}
      </span>
    </span>
  );
}