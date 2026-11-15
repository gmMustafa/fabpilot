"use client";
/**
 * Client-side context for the whole app (7.2): TanStack Query's cache.
 *
 * The QueryClient lives in useState, NOT at module level: a module-level client would be
 * shared by every request the Next.js server renders, i.e. by different users. useState
 * gives one client per browser tab, created once.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(() => new QueryClient());
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
