"use client";
/**
 * Start an investigation (7.4b): pick a machine, ask a question. Two requests: create the
 * incident (POST /incidents), start a run on it (POST /incidents/{id}/runs: 202), then go to
 * the run's page, which subscribes to its events. A 429 (all slots busy) is shown with its
 * Retry-After, via describeError.
 *
 * Starts EMPTY (a pre-filled question gets sent unread). ?machine=M-08 (from a machine page)
 * selects that machine; when the selected machine has active alarms, the form offers questions
 * built from them (suggestQuestions): one click fills the question, which stays editable.
 *
 * Fresh on every visit: with Cache Components, Next.js does not unmount a page you leave, it
 * HIDES it (React <Activity>), so useState would survive a trip to another tab. The layout
 * effect's cleanup runs when the page is hidden and resets the form; useLayoutEffect, not
 * useEffect, so the reset happens before the page is hidden (no flash of the old draft when
 * it is shown again).
 */
import { useQuery } from "@tanstack/react-query";
import { Lightbulb, Send } from "lucide-react";
import { useRouter } from "next/navigation";
import { type FormEvent, useLayoutEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";
import { MAX_QUESTION, suggestQuestions } from "@/lib/investigate/suggest";
import { useActiveAlarms } from "@/lib/investigate/use-active-alarms";
import { cn } from "@/lib/utils";

async function fetchMachines() {
  const { data, error, response } = await api.GET("/machines");
  if (!data) throw new Error(describeError(response.status, error, null));
  return data;
}

export function NewInvestigationForm({ initialMachine }: { initialMachine?: string }) {
  const router = useRouter();
  const machines = useQuery({ queryKey: ["machines"], queryFn: fetchMachines });
  const [machine, setMachine] = useState(initialMachine ?? "");
  const [question, setQuestion] = useState("");
  // wait for the machine list: the alarm window ends at the machine's newest reading
  const selected = machines.data?.find((m) => m.code === machine);
  const alarms = useActiveAlarms(selected?.code, selected?.last_reading_at);
  const suggestions = suggestQuestions(alarms.data ?? []);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useLayoutEffect(() => {
    return () => {
      setMachine(initialMachine ?? "");
      setQuestion("");
      setError(null);
      setBusy(false);
    };
  }, [initialMachine]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const incident = await api.POST("/incidents", { body: { machine, question } });
      if (!incident.data) {
        setError(describeError(incident.response.status, incident.error, null));
        return;
      }
      const run = await api.POST("/incidents/{incident_id}/runs", {
        params: { path: { incident_id: incident.data.id } },
      });
      if (!run.data) {
        const retryAfter = run.response.headers.get("Retry-After");
        setError(describeError(run.response.status, run.error, retryAfter));
        return;
      }
      router.push(`/runs/${run.data.thread_id}`);
    } catch {
      setError("API unreachable: is uvicorn running?");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      <label className="block space-y-1 text-sm">
        <span className="font-medium">Machine</span>
        <select
          className="block w-full max-w-md rounded-md border bg-background px-3 py-2"
          value={machine}
          onChange={(e) => setMachine(e.target.value)}
          required
        >
          <option value="" disabled>
            {machines.isPending ? "Loading machines…" : "Select a machine…"}
          </option>
          {(machines.data ?? (machine ? [{ code: machine, name: "", line: "" }] : [])).map((m) => (
            <option key={m.code} value={m.code}>
              {m.code} {m.name} {m.line && `(${m.line})`}
            </option>
          ))}
        </select>
      </label>
      <label className="block space-y-1 text-sm">
        <span className="font-medium">Question</span>
        <textarea
          className="block w-full max-w-2xl rounded-md border bg-background px-3 py-2"
          rows={3}
          maxLength={MAX_QUESTION}
          placeholder="What happened, and what do you want to know? e.g. Why did the pressure drop overnight?"
          minLength={3}
          required
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
        />
        <span className="text-xs text-muted-foreground">
          {question.length}/{MAX_QUESTION} · the agent uses ~2-4 LLM requests of the free daily
          quota
        </span>
      </label>
      {suggestions.length > 0 && (
        <div className="space-y-2">
          <p className="flex items-center gap-1.5 text-sm font-medium">
            <Lightbulb className="size-4 text-amber-600" aria-hidden />
            Suggested from {machine}&apos;s active alarms
          </p>
          <div className="flex max-w-2xl flex-col gap-2">
            {suggestions.map((q) => (
              <button
                key={q}
                type="button"
                onClick={() => setQuestion(q)}
                aria-pressed={question === q}
                className={cn(
                  "rounded-md border bg-card px-3 py-2 text-left text-sm transition-colors",
                  "outline-none hover:border-primary/50 hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring",
                  question === q && "border-primary bg-primary/5",
                )}
              >
                {q}
              </button>
            ))}
          </div>
        </div>
      )}
      {error && <p className="text-sm text-destructive">{error}</p>}
      <Button type="submit" disabled={busy}>
        <Send aria-hidden /> {busy ? "Starting…" : "Investigate"}
      </Button>
    </form>
  );
}
