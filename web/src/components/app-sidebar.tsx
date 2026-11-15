"use client";
/**
 * The app shell's navigation (UI pass): a dark sidebar on large screens, a top bar on small
 * ones. The active route is highlighted (usePathname); the approval queue shows how many runs
 * wait for a decision.
 *
 * `status` is a SLOT: the API status is an async Server Component (it asks /health on the
 * server), and a Client Component can't import one, but it can render one handed to it as
 * children/props from a Server Component (the layout). The request stays on the server.
 *
 * Suspense around the links: usePathname is runtime data on a route with dynamic params
 * (/machines/[code]), unknown while Next.js prerenders that route's static shell. Only the
 * leaf that reads it suspends; its fallback is the same links without the highlight, so the
 * shell (logo, links, frame) is still prerendered and nothing jumps when the highlight arrives.
 */
import { useQuery } from "@tanstack/react-query";
import { ClipboardCheck, Factory, Gauge, Info, type LucideIcon, Search } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { type ReactNode, Suspense } from "react";

import { LogoMark } from "@/components/logo-mark";
import { api } from "@/lib/api/client";
import { cn } from "@/lib/utils";

type Item = { href: string; label: string; icon: LucideIcon; badge?: "approvals" };

const NAV: Item[] = [
  { href: "/", label: "Plant overview", icon: Factory },
  { href: "/incidents/new", label: "Investigate", icon: Search },
  { href: "/approvals", label: "Approvals", icon: ClipboardCheck, badge: "approvals" },
  { href: "/gateway", label: "LLM usage", icon: Gauge },
  { href: "/about", label: "About", icon: Info },
];

/** "/" is active only on "/", the others also on their sub-pages (/runs/... belongs to
 * Investigate, /machines/... to the plant overview). */
function isActive(href: string, pathname: string): boolean {
  if (href === "/") return pathname === "/" || pathname.startsWith("/machines");
  if (href === "/incidents/new")
    return pathname.startsWith("/incidents") || pathname.startsWith("/runs");
  return pathname.startsWith(href);
}

function useWaitingCount(): number | undefined {
  const queue = useQuery({
    queryKey: ["runs", "paused"],
    queryFn: async () => {
      const { data } = await api.GET("/runs", { params: { query: { status: "paused" } } });
      return data ?? [];
    },
    refetchInterval: 15_000,
  });
  return queue.data?.length;
}

function Logo() {
  return (
    <Link href="/" className="flex items-center gap-2.5">
      <LogoMark />
      <span className="leading-tight">
        <span className="block text-base font-semibold text-white">FabPilot</span>
        <span className="block text-xs text-sidebar-foreground/70">Maintenance copilot</span>
      </span>
    </Link>
  );
}

function NavLink({ item, active, waiting }: { item: Item; active: boolean; waiting?: number }) {
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
        "outline-none focus-visible:ring-2 focus-visible:ring-sidebar-ring",
        active
          ? "bg-sidebar-primary text-sidebar-primary-foreground"
          : "text-sidebar-foreground hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
      )}
    >
      <Icon className="size-4 shrink-0" aria-hidden />
      <span className="flex-1">{item.label}</span>
      {item.badge && waiting ? (
        <span
          className="rounded-full bg-amber-400 px-1.5 text-xs font-semibold text-black tabular-nums"
          aria-label={`${waiting} waiting for a decision`}
        >
          {waiting}
        </span>
      ) : null}
    </Link>
  );
}

function NavLinks({ pathname }: { pathname: string | null }) {
  const waiting = useWaitingCount();
  return NAV.map((item) => (
    <NavLink
      key={item.href}
      item={item}
      active={pathname !== null && isActive(item.href, pathname)}
      waiting={waiting}
    />
  ));
}

function ActiveNavLinks() {
  return <NavLinks pathname={usePathname()} />;
}

function Links() {
  return (
    <Suspense fallback={<NavLinks pathname={null} />}>
      <ActiveNavLinks />
    </Suspense>
  );
}

export function AppSidebar({ status }: { status: ReactNode }) {
  return (
    <>
      <aside className="sticky top-0 hidden h-screen w-64 shrink-0 flex-col border-r border-sidebar-border bg-sidebar px-4 py-5 lg:flex">
        <Logo />
        <nav aria-label="Main" className="mt-8 flex flex-1 flex-col gap-1">
          <Links />
        </nav>
        <div className="border-t border-sidebar-border pt-4">{status}</div>
      </aside>

      {/* Small screens: the same navigation as a top bar */}
      <header className="sticky top-0 z-10 flex flex-col gap-3 bg-sidebar px-4 py-3 lg:hidden">
        <div className="flex items-center justify-between gap-3">
          <Logo />
          {status}
        </div>
        <nav aria-label="Main" className="-mx-1 flex gap-1 overflow-x-auto pb-1">
          <Links />
        </nav>
      </header>
    </>
  );
}
