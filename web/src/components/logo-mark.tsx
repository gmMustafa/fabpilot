/**
 * The FabPilot mark: a sensor signal with a spike, and an amber dot for "fault spotted".
 * The same drawing as app/icon.svg (the favicon). Inline SVG, so it is sharp at any size and
 * costs no extra request. Decorative: the name "FabPilot" always stands next to it.
 */
import { cn } from "@/lib/utils";

export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={cn("size-8 shrink-0", className)} aria-hidden>
      <rect width="32" height="32" rx="7" fill="#0E4491" />
      <path
        d="M5 18h5.5l2.5-7 4.5 13 3-8.5 1.5 2.5H27"
        fill="none"
        stroke="#FFFFFF"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="25.5" cy="8.5" r="2.5" fill="#F5B83D" />
    </svg>
  );
}
