import { useMemo } from "react";
import { AlertTriangle, CheckCircle2, Copy, Loader2, PlayCircle, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { RadarData, Suggestion } from "@/types/api";

function formatISOShort(ts: string | undefined): string {
  const raw = String(ts || "").trim();
  if (!raw) return "";
  // Keep it compact: YYYY-MM-DD HH:MM (local)
  const d = new Date(raw);
  if (Number.isNaN(d.getTime())) return raw;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function oneLineJson(value: unknown): string {
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export interface TaskRadarSectionProps {
  radar?: RadarData;
  isLoading: boolean;
  error?: string;
  isRunning?: boolean;
  onRunNext?: (next: Suggestion) => void;
  onCopyNext?: (next: Suggestion) => void;
  onRefresh?: () => void;
}

export function TaskRadarSection({ radar, isLoading, error, isRunning, onRunNext, onCopyNext, onRefresh }: TaskRadarSectionProps) {
  const runway = radar?.runway;
  const runwayOpen = Boolean(runway?.open);
  const next = radar?.next?.[0];
  const evidence = radar?.verify?.evidence_task;

  const runwayReason = useMemo(() => {
    if (!runway) return "";
    const lint = runway.blocking?.lint;
    const validation = runway.blocking?.validation;
    if (lint && Number(lint.errors_count || 0) > 0) {
      const top = Array.isArray(lint.top_errors) ? lint.top_errors[0] : null;
      const msg = typeof top?.message === "string" ? top.message : "";
      const code = typeof top?.code === "string" ? top.code : "";
      return [code, msg].filter(Boolean).join(": ");
    }
    if (validation && typeof validation.message === "string" && validation.message.trim().length > 0) {
      return validation.message;
    }
    return "";
  }, [runway]);

  const nextCopyPayload = useMemo(() => {
    if (!next) return "";
    return oneLineJson({ intent: next.action, ...(next.params ?? {}) });
  }, [next]);

  const evidenceLine = useMemo(() => {
    if (!evidence) return "";
    const checks = evidence.checks?.count ?? 0;
    const attachments = evidence.attachments?.count ?? 0;
    const steps = `${evidence.steps_with_any_evidence ?? 0}/${evidence.steps_total ?? 0}`;
    const last = formatISOShort(evidence.checks?.last_observed_at || evidence.attachments?.last_observed_at);
    const lastPart = last ? ` · last ${last}` : "";
    return `evidence: steps ${steps} · checks ${checks} · attachments ${attachments}${lastPart}`;
  }, [evidence]);

  return (
    <section className="mb-6 rounded-xl border border-border bg-card p-[var(--density-card-pad)]">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <div className="text-xs font-semibold uppercase tracking-wide text-foreground-muted">Radar</div>
            <span
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-semibold",
                runwayOpen ? "bg-status-ok/10 text-status-ok" : "bg-status-warn/10 text-status-warn"
              )}
            >
              {runwayOpen ? <CheckCircle2 className="h-3.5 w-3.5" /> : <AlertTriangle className="h-3.5 w-3.5" />}
              {runwayOpen ? "Runway open" : "Runway closed"}
            </span>
          </div>
          {isLoading ? (
            <div className="mt-2 flex items-center gap-2 text-sm text-foreground-muted">
              <Loader2 className="h-4 w-4 animate-spin" />
              Loading radar…
            </div>
          ) : error ? (
            <div className="mt-2 text-sm text-status-err">Failed to load radar: {error}</div>
          ) : runwayReason ? (
            <div className="mt-2 text-sm text-foreground-muted">{runwayReason}</div>
          ) : (
            <div className="mt-2 text-sm text-foreground-muted">No blockers</div>
          )}
          {evidenceLine && <div className="mt-2 text-xs text-foreground-muted">{evidenceLine}</div>}
        </div>

        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={onRefresh} disabled={isLoading || isRunning}>
            <RefreshCw className={cn("mr-2 h-4 w-4", (isLoading || isRunning) && "animate-spin")} />
            Refresh
          </Button>
        </div>
      </div>

      <div className="mt-4 rounded-lg border border-border bg-background-subtle p-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-xs font-semibold uppercase tracking-wide text-foreground-muted">Next</div>
            {next ? (
              <>
                <div className="mt-1 text-sm font-semibold text-foreground">{next.reason || `${next.action}`}</div>
                <div className="mt-1 break-all font-mono text-[11px] text-foreground-muted">{nextCopyPayload}</div>
              </>
            ) : (
              <div className="mt-1 text-sm text-foreground-muted">No suggestions</div>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="default"
              size="sm"
              onClick={() => next && onRunNext?.(next)}
              disabled={!next || isLoading || isRunning || !Boolean(next.validated)}
              title={!next?.validated ? "Suggestion is not validated" : undefined}
            >
              {isRunning ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <PlayCircle className="mr-2 h-4 w-4" />}
              Run
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => next && onCopyNext?.(next)}
              disabled={!next || isLoading}
            >
              <Copy className="mr-2 h-4 w-4" />
              Copy
            </Button>
          </div>
        </div>
      </div>
    </section>
  );
}

