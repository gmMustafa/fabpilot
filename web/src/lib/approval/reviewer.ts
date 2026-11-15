"use client";
/**
 * The reviewer's name, remembered in this browser (7.5). A per-viewer CONVENIENCE only, not an
 * identity: there is no login yet (the gap documented in 6.2); the API records the name, it
 * doesn't verify it. localStorage can be unavailable (private window, blocked site data), so
 * every access is guarded and the page works without it.
 *
 * useSyncExternalStore, not useState + useEffect: localStorage is an EXTERNAL store. Reading it
 * in an effect and then calling setState renders twice (React's lint flags it); this hook
 * reads it during render, and the server snapshot ("") keeps server rendering consistent.
 */
import { useSyncExternalStore } from "react";

const KEY = "fabpilot.reviewer";
const listeners = new Set<() => void>();

function read(): string {
  try {
    return window.localStorage.getItem(KEY) ?? "";
  } catch {
    return ""; // storage unavailable
  }
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  window.addEventListener("storage", listener); // the name changed in ANOTHER tab
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

function write(name: string): void {
  try {
    window.localStorage.setItem(KEY, name);
  } catch {
    // storage unavailable: nothing is remembered
  }
  listeners.forEach((l) => l()); // "storage" only fires in OTHER tabs: notify this one
}

export function useReviewerName(): [string, (name: string) => void] {
  const name = useSyncExternalStore(subscribe, read, () => "");
  return [name, write];
}
