"use client";
/**
 * Start an investigation (7.4b): pick a machine, ask a question. Two requests: create the
 * incident (POST /incidents), start a run on it (POST /incidents/{id}/runs: 202), then go to
 * the run's page, which subscribes to its events. A 429 (all slots busy) is shown with its
 * Retry-After, via describeError.
 */
import { useQuery } from "@tanstack/react-query";
import { Send } from "lucide-react";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api/client";
import { describeError } from "@/lib/api/errors";

async function fetchMachines() {
  const { data, error, response } = await api.GET("/machines");
  if (!data) throw new Error(describeError(response.status, error, null));
  return data;
}

export function NewInvestigationForm({ initialMachine }: { initialMachine?: string }) {
  const router = useRouter();
  const machines = useQuery({ queryKey: ["machines"], queryFn: fetchMachines });
  const [machine, setMachine] = useState(initialMachine ?? "M-08");
  const [question, setQuestion] = useState(
    "Discharge temperature alarm. Is the compressor overheating?",
  );
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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
        >
          {(machines.data ?? [{ code: machine, name: "", line: "" }]).map((m) => (
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
          maxLength={200}
          minLength={3}
          required
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
        />
        <span className="text-xs text-muted-foreground">
          {question.length}/200 · the agent uses ~2-4 LLM requests of the free daily quota
        </span>
      </label>
      {error && <p className="text-sm text-destructive">{error}</p>}
      <Button type="submit" disabled={busy}>
        <Send aria-hidden /> {busy ? "Starting…" : "Investigate"}
      </Button>
    </form>
  );
}
