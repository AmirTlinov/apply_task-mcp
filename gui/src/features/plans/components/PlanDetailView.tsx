import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Edit3,
  FileText,
  ListOrdered,
  ListTodo,
  Plus,
  Trash2,
} from "lucide-react";
import type { Plan, TaskListItem } from "@/types/task";
import { resumeEntity, listTasks, updatePlan, updateContract, editTask, verifyCheckpoint, getHandoff } from "@/lib/tauri";
import { TaskTableView } from "@/features/tasks/components/TaskTableView";
import { TaskPlanView } from "@/features/tasks/components/TaskPlanView";
import { CheckpointMarks } from "@/components/common/CheckpointMarks";
import { Markdown } from "@/components/common/Markdown";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/components/common/toast";
import { cn } from "@/lib/utils";

interface PlanDetailViewProps {
  planId: string;
  searchQuery?: string;
  onBack: () => void;
  onOpenTask?: (taskId: string) => void;
  onNewTask?: () => void;
}

function clampInt(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.max(min, Math.min(max, Math.trunc(value)));
}

function splitPlanSteps(text: string): string[] {
  return String(text || "")
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

function extractPlanChecklist(plan?: Plan | null) {
  const raw = plan?.plan;
  return {
    steps: Array.isArray(raw?.steps) ? raw!.steps : [],
    current: typeof raw?.current === "number" ? raw!.current : 0,
    doc: typeof raw?.doc === "string" ? raw!.doc : "",
  };
}

export function PlanDetailView({
  planId,
  searchQuery,
  onBack,
  onOpenTask,
  onNewTask,
}: PlanDetailViewProps) {
  const queryClient = useQueryClient();
  const [isExportingHandoff, setIsExportingHandoff] = useState(false);
  const planQueryKey = useMemo(() => ["plan", planId] as const, [planId]);

  const planQuery = useQuery({
    queryKey: planQueryKey,
    queryFn: async () => {
      const resp = await resumeEntity(planId);
      if (!resp.success || !resp.plan) {
        throw new Error(resp.error || "Failed to load plan");
      }
      return resp.plan;
    },
  });

  const tasksQuery = useQuery({
    queryKey: ["plan-tasks", planId],
    queryFn: async () => {
      const resp = await listTasks({ parent: planId, compact: true });
      if (!resp.success) throw new Error(resp.error || "Failed to load plan tasks");
      return resp.tasks as TaskListItem[];
    },
  });

  const tasks = tasksQuery.data ?? [];
  const filteredTasks = useMemo(() => {
    if (!searchQuery) return tasks;
    const query = searchQuery.toLowerCase();
    return tasks.filter(
      (task) =>
        task.title.toLowerCase().includes(query) ||
        task.id.toLowerCase().includes(query)
    );
  }, [searchQuery, tasks]);

  const plan = planQuery.data;

  if (planQuery.isLoading) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-foreground-muted">
        Loading plan…
      </div>
    );
  }

  if (!plan || planQuery.isError) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-2 text-sm text-foreground-muted">
        <span>Plan not found.</span>
        <Button variant="outline" onClick={onBack}>
          Back
        </Button>
      </div>
    );
  }

  const checklist = extractPlanChecklist(plan);
  const planStepsCount = checklist.steps.length;
  const planCurrent = clampInt(checklist.current, 0, planStepsCount);
  const planProgress = planStepsCount > 0 ? Math.round((planCurrent / planStepsCount) * 100) : 0;
  const criteriaOk = !!plan.criteria_confirmed || !!plan.criteria_auto_confirmed;
  const testsOk = !!plan.tests_confirmed || !!plan.tests_auto_confirmed;

  const handleExportHandoff = async () => {
    if (!plan?.id) return;
    setIsExportingHandoff(true);
    try {
      const resp = await getHandoff({ planId: plan.id });
      if (!resp.success || !resp.data) {
        throw new Error(resp.error || "Failed to export handoff");
      }
      const blob = new Blob([JSON.stringify(resp.data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `apply-task-handoff-${plan.id}.json`;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(url);
      toast.success("Handoff exported");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to export handoff";
      toast.error(message);
    } finally {
      setIsExportingHandoff(false);
    }
  };

  return (
    <div className="flex flex-1 flex-col overflow-hidden bg-background">
      <div className="border-b border-border bg-background px-[var(--density-page-pad)] py-3">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex min-w-0 items-start gap-3">
            <Button variant="outline" size="icon" onClick={onBack} aria-label="Back">
              <ArrowLeft className="h-4 w-4" />
            </Button>

            <div className="min-w-0 space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-md bg-background-muted px-2 py-0.5 font-mono text-[11px] text-foreground-muted">
                  {plan.id}
                </span>
                <span className="rounded-full bg-background-muted px-2.5 py-1 text-xs font-semibold text-foreground-muted">
                  {planProgress}%
                </span>
                <CheckpointMarks criteriaOk={criteriaOk} testsOk={testsOk} />
              </div>
              <h1 className="text-lg font-semibold leading-snug tracking-tight text-foreground">
                {plan.title}
              </h1>
            </div>
          </div>

          <div className="flex items-center gap-2 text-xs font-semibold text-foreground-muted">
            <Button
              variant="outline"
              size="sm"
              onClick={handleExportHandoff}
              disabled={isExportingHandoff}
              aria-label="Export handoff"
            >
              <FileText className="mr-2 h-4 w-4" />
              {isExportingHandoff ? "Exporting..." : "Handoff"}
            </Button>
            <span className="rounded-full bg-background-muted px-2.5 py-1">
              {planCurrent}/{planStepsCount}
            </span>
          </div>
        </div>
      </div>

      <div className="custom-scrollbar flex-1 overflow-y-auto px-[var(--density-page-pad)] pb-[var(--density-page-pad)] pt-4">
        <section className="mb-6">
          <div className="mb-3 flex items-center gap-2 text-sm font-medium text-foreground-muted">
            <ListTodo className="h-4 w-4 text-primary" />
            <span>Tasks</span>
          </div>
          <TaskTableView
            tasks={filteredTasks}
            isLoading={tasksQuery.isLoading}
            onTaskClick={onOpenTask}
            onNewTask={onNewTask}
            searchQuery={searchQuery}
          />
        </section>

        <PlanCheckpointsSection plan={plan} />
        <PlanChecklistSection plan={plan} />
        <PlanContractSection plan={plan} />
        <PlanNotesSection plan={plan} />
      </div>
    </div>
  );
}

function PlanCheckpointsSection({ plan }: { plan: Plan }) {
  const queryClient = useQueryClient();
  const planQueryKey = useMemo(() => ["plan", plan.id] as const, [plan.id]);

  const mutation = useMutation({
    mutationFn: async (payload: { checkpoint: "criteria" | "tests"; confirmed: boolean }) => {
      const resp = await verifyCheckpoint({
        taskId: plan.id,
        kind: "task_detail",
        checkpoint: payload.checkpoint,
        confirmed: payload.confirmed,
      });
      if (!resp.success) throw new Error(resp.error || "Failed to update checkpoint");
      return resp;
    },
    onSuccess: (resp) => {
      if (resp.plan) {
        queryClient.setQueryData(planQueryKey, resp.plan);
      } else {
        queryClient.invalidateQueries({ queryKey: planQueryKey });
      }
      toast.success("Checkpoint updated");
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "Failed to update checkpoint");
    },
  });

  const criteria = plan.success_criteria ?? [];
  const tests = plan.tests ?? [];
  const blockers = plan.blockers ?? [];
  const criteriaOk = !!plan.criteria_confirmed || !!plan.criteria_auto_confirmed;
  const testsOk = !!plan.tests_confirmed || !!plan.tests_auto_confirmed;

  const renderCheckpoint = (
    label: string,
    items: string[],
    status: "ok" | "auto" | "todo",
    onToggle?: () => void
  ) => {
    const statusLabel = status === "ok" ? "OK" : status === "auto" ? "AUTO" : "TODO";
    const statusClass =
      status === "ok"
        ? "bg-status-ok/10 text-status-ok"
        : status === "auto"
          ? "bg-primary/10 text-primary"
          : "bg-status-warn/10 text-status-warn";

    return (
      <div className="rounded-lg border border-border bg-background-subtle p-3">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div className="text-xs font-semibold uppercase tracking-wide text-foreground-subtle">
            {label}
          </div>
          <div className="flex items-center gap-2">
            <span className={cn("rounded-full px-2 py-0.5 text-[10px] font-bold", statusClass)}>
              {statusLabel}
            </span>
            {onToggle && (
              <Button
                variant="outline"
                size="sm"
                className="h-7"
                onClick={onToggle}
                disabled={mutation.isPending}
              >
                {status === "ok" ? "Reset" : "Confirm"}
              </Button>
            )}
          </div>
        </div>
        {items.length > 0 ? (
          <ul className="space-y-1 text-sm text-foreground">
            {items.map((item, idx) => (
              <li key={`${label}-${idx}`} className="flex gap-2">
                <span className="text-foreground-subtle">{idx + 1}.</span>
                <span className="min-w-0">{item}</span>
              </li>
            ))}
          </ul>
        ) : (
          <div className="text-sm text-foreground-muted">No items.</div>
        )}
      </div>
    );
  };

  return (
    <section className="mb-6">
      <div className="mb-3 flex items-center gap-2 text-sm font-medium text-foreground-muted">
        <span>Checkpoints</span>
        <CheckpointMarks criteriaOk={criteriaOk} testsOk={testsOk} />
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        {renderCheckpoint(
          "Criteria",
          criteria,
          criteriaOk ? "ok" : "todo",
          plan.criteria_auto_confirmed
            ? undefined
            : () => mutation.mutate({ checkpoint: "criteria", confirmed: !criteriaOk })
        )}
        {renderCheckpoint(
          "Tests",
          tests,
          testsOk ? "ok" : plan.tests_auto_confirmed ? "auto" : "todo",
          plan.tests_auto_confirmed
            ? undefined
            : () => mutation.mutate({ checkpoint: "tests", confirmed: !testsOk })
        )}
      </div>
      <div className="mt-3 rounded-lg border border-border bg-background-subtle p-3">
        <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-foreground-subtle">
          Blockers
        </div>
        {blockers.length > 0 ? (
          <ul className="space-y-1 text-sm text-foreground">
            {blockers.map((item, idx) => (
              <li key={`blocker-${idx}`} className="flex gap-2">
                <span className="text-foreground-subtle">{idx + 1}.</span>
                <span className="min-w-0">{item}</span>
              </li>
            ))}
          </ul>
        ) : (
          <div className="text-sm text-foreground-muted">No blockers.</div>
        )}
      </div>
    </section>
  );
}

function PlanChecklistSection({ plan }: { plan: Plan }) {
  const queryClient = useQueryClient();
  const planQueryKey = useMemo(() => ["plan", plan.id] as const, [plan.id]);
  const checklist = extractPlanChecklist(plan);
  const planStepsCount = checklist.steps.length;
  const planCurrent = clampInt(checklist.current, 0, planStepsCount);
  const planDoc = checklist.doc;
  const hasAnyPlan = planDoc.trim().length > 0 || planStepsCount > 0;

  const [docEditorOpen, setDocEditorOpen] = useState(false);
  const [docTab, setDocTab] = useState<"edit" | "preview">("edit");
  const [draftDoc, setDraftDoc] = useState<string>("");

  const [stepsEditorOpen, setStepsEditorOpen] = useState(false);
  const [draftSteps, setDraftSteps] = useState("");
  const [draftCurrent, setDraftCurrent] = useState<string>("0");
  const [clearConfirmOpen, setClearConfirmOpen] = useState(false);

  const mutation = useMutation({
    mutationFn: async (payload: { doc?: string; steps?: string[]; current?: number; advance?: boolean }) => {
      const resp = await updatePlan({
        planId: plan.id,
        doc: payload.doc,
        steps: payload.steps,
        current: payload.current,
        advance: payload.advance,
      });
      if (!resp.success) throw new Error(resp.error || "Failed to update plan");
      return resp;
    },
    onSuccess: (resp) => {
      if (resp.plan) {
        queryClient.setQueryData(planQueryKey, resp.plan);
      } else {
        queryClient.invalidateQueries({ queryKey: planQueryKey });
      }
      toast.success("Plan updated");
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "Failed to update plan");
    },
  });

  const openDocEditor = () => {
    setDocTab("edit");
    setDraftDoc(planDoc);
    setDocEditorOpen(true);
  };

  const saveDoc = () => {
    mutation.mutate({ doc: String(draftDoc ?? "") });
    setDocEditorOpen(false);
  };

  const openStepsEditor = () => {
    setDraftSteps(checklist.steps.join("\n"));
    setDraftCurrent(String(planCurrent));
    setStepsEditorOpen(true);
  };

  const saveSteps = () => {
    const nextSteps = splitPlanSteps(draftSteps);
    const rawCurrent = Number(draftCurrent);
    const nextCurrent = clampInt(Number.isFinite(rawCurrent) ? rawCurrent : 0, 0, nextSteps.length);
    mutation.mutate({ steps: nextSteps, current: nextCurrent });
    setStepsEditorOpen(false);
  };

  const advance = () => mutation.mutate({ advance: true });
  const clearPlan = () => {
    mutation.mutate({ doc: "", steps: [], current: 0 });
    setClearConfirmOpen(false);
  };

  return (
    <>
      <section className="mb-6 rounded-lg border border-border bg-background-subtle p-4">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm font-medium text-foreground-muted">
            <ListOrdered className="h-4 w-4 text-primary" />
            <span>Plan</span>
            <span className="text-xs font-semibold tabular-nums text-foreground-subtle">
              {planCurrent}/{planStepsCount}
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" size="sm" className="h-8 gap-2" onClick={openDocEditor} disabled={mutation.isPending}>
              <Edit3 className="h-4 w-4" />
              Edit doc
            </Button>
            <Button variant="outline" size="sm" className="h-8 gap-2" onClick={openStepsEditor} disabled={mutation.isPending}>
              <Edit3 className="h-4 w-4" />
              Edit steps
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-8 gap-2"
              onClick={advance}
              disabled={mutation.isPending || planStepsCount === 0}
              title="Advance plan current index"
            >
              <Plus className="h-4 w-4" />
              Advance
            </Button>
            <Button
              variant="outline"
              size="sm"
              className="h-8 gap-2 text-status-fail hover:text-status-fail"
              onClick={() => setClearConfirmOpen(true)}
              disabled={mutation.isPending || !hasAnyPlan}
            >
              <Trash2 className="h-4 w-4" />
              Clear
            </Button>
          </div>
        </div>

        <TaskPlanView plan={checklist} showHeader={false} className="mb-0 border-none bg-transparent p-0" />

        <div className="mt-4 rounded-lg border border-border bg-background p-3">
          <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-foreground-subtle">
            Plan doc
          </div>
          {planDoc.trim().length > 0 ? (
            <Markdown content={planDoc} className="text-sm" />
          ) : (
            <div className="text-sm text-foreground-muted">
              Plan doc is empty. Use it for architecture, rollout, and long-form notes.
            </div>
          )}
        </div>
      </section>

      <Dialog open={docEditorOpen} onOpenChange={(open) => setDocEditorOpen(open)}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>Edit Plan doc</DialogTitle>
          </DialogHeader>

          <div className="flex items-center gap-2">
            <button
              type="button"
              className={cn(
                "rounded-md px-3 py-1 text-xs font-semibold transition-colors",
                docTab === "edit" ? "bg-background text-foreground" : "text-foreground-muted hover:bg-background-hover"
              )}
              onClick={() => setDocTab("edit")}
            >
              Edit
            </button>
            <button
              type="button"
              className={cn(
                "rounded-md px-3 py-1 text-xs font-semibold transition-colors",
                docTab === "preview" ? "bg-background text-foreground" : "text-foreground-muted hover:bg-background-hover"
              )}
              onClick={() => setDocTab("preview")}
            >
              Preview
            </button>
          </div>

          {docTab === "edit" ? (
            <Textarea
              value={draftDoc}
              onChange={(e) => setDraftDoc(e.target.value)}
              rows={18}
              placeholder="Plan narrative, architecture, rollout..."
            />
          ) : (
            <div className="max-h-[60vh] overflow-y-auto rounded-md border border-border bg-background-subtle p-4">
              {draftDoc.trim().length > 0 ? (
                <Markdown content={draftDoc} className="text-sm" />
              ) : (
                <div className="text-sm text-foreground-muted">Nothing to preview.</div>
              )}
            </div>
          )}

          <DialogFooter>
            <Button variant="outline" onClick={() => setDocEditorOpen(false)} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button onClick={saveDoc} disabled={mutation.isPending}>
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={stepsEditorOpen} onOpenChange={(open) => setStepsEditorOpen(open)}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>Edit Plan steps</DialogTitle>
          </DialogHeader>

          <div className="space-y-4">
            <div className="space-y-2">
              <div className="text-xs font-semibold uppercase tracking-wider text-foreground-subtle">
                Steps (one per line)
              </div>
              <Textarea
                value={draftSteps}
                onChange={(e) => setDraftSteps(e.target.value)}
                rows={10}
                placeholder="1) ...&#10;2) ..."
              />
            </div>
            <div className="space-y-2">
              <div className="text-xs font-semibold uppercase tracking-wider text-foreground-subtle">
                Current index
              </div>
              <Input
                type="number"
                value={draftCurrent}
                onChange={(e) => setDraftCurrent(e.target.value)}
                min={0}
                max={Math.max(0, checklist.steps.length)}
              />
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setStepsEditorOpen(false)} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button onClick={saveSteps} disabled={mutation.isPending}>
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        isOpen={clearConfirmOpen}
        title="Clear plan?"
        description="This removes plan doc + steps and resets current progress."
        confirmLabel="Clear"
        cancelLabel="Cancel"
        danger
        onCancel={() => setClearConfirmOpen(false)}
        onConfirm={clearPlan}
      />
    </>
  );
}

function PlanContractSection({ plan }: { plan: Plan }) {
  const queryClient = useQueryClient();
  const planQueryKey = useMemo(() => ["plan", plan.id] as const, [plan.id]);
  const contractText = String(plan.contract || "");
  const versions = typeof plan.contract_versions_count === "number" ? plan.contract_versions_count : 0;

  const [editorOpen, setEditorOpen] = useState(false);
  const [docTab, setDocTab] = useState<"edit" | "preview">("edit");
  const [draftContract, setDraftContract] = useState("");

  const mutation = useMutation({
    mutationFn: async (payload: { current: string }) => {
      const resp = await updateContract({ planId: plan.id, current: payload.current });
      if (!resp.success) throw new Error(resp.error || "Failed to update contract");
      return resp;
    },
    onSuccess: (resp) => {
      if (resp.plan) {
        queryClient.setQueryData(planQueryKey, resp.plan);
      } else {
        queryClient.invalidateQueries({ queryKey: planQueryKey });
      }
      toast.success("Contract updated");
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "Failed to update contract");
    },
  });

  const openEditor = () => {
    setDocTab("edit");
    setDraftContract(contractText);
    setEditorOpen(true);
  };

  const save = () => {
    mutation.mutate({ current: String(draftContract ?? "") });
    setEditorOpen(false);
  };

  return (
    <>
      <section className="mb-6">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm font-medium text-foreground-muted">
            <FileText className="h-4 w-4" />
            <span>Contract</span>
            {versions > 0 && (
              <span className="text-xs font-semibold tabular-nums text-foreground-subtle">
                v{versions}
              </span>
            )}
          </div>

          <Button
            variant="outline"
            size="sm"
            className="h-8 gap-2"
            onClick={openEditor}
            disabled={mutation.isPending}
          >
            <Edit3 className="h-4 w-4" />
            Edit
          </Button>
        </div>

        <div className="rounded-lg border border-border bg-background-subtle p-[var(--density-card-pad)]">
          {contractText.trim().length > 0 ? (
            <Markdown content={contractText} className="text-sm" />
          ) : (
            <div className="text-sm text-foreground-muted">
              Contract is empty. Capture intent, constraints, and definition of done here.
            </div>
          )}
        </div>
      </section>

      <Dialog open={editorOpen} onOpenChange={(open) => setEditorOpen(open)}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>Edit Contract</DialogTitle>
          </DialogHeader>

          <div className="flex items-center gap-2">
            <button
              type="button"
              className={cn(
                "rounded-md px-3 py-1 text-xs font-semibold transition-colors",
                docTab === "edit" ? "bg-background text-foreground" : "text-foreground-muted hover:bg-background-hover"
              )}
              onClick={() => setDocTab("edit")}
            >
              Edit
            </button>
            <button
              type="button"
              className={cn(
                "rounded-md px-3 py-1 text-xs font-semibold transition-colors",
                docTab === "preview" ? "bg-background text-foreground" : "text-foreground-muted hover:bg-background-hover"
              )}
              onClick={() => setDocTab("preview")}
            >
              Preview
            </button>
          </div>

          {docTab === "edit" ? (
            <Textarea
              value={draftContract}
              onChange={(e) => setDraftContract(e.target.value)}
              rows={18}
              placeholder="Intent, constraints, definition of done…"
            />
          ) : (
            <div className="max-h-[60vh] overflow-y-auto rounded-md border border-border bg-background-subtle p-4">
              {draftContract.trim().length > 0 ? (
                <Markdown content={draftContract} className="text-sm" />
              ) : (
                <div className="text-sm text-foreground-muted">Nothing to preview.</div>
              )}
            </div>
          )}

          <DialogFooter>
            <Button variant="outline" onClick={() => setEditorOpen(false)} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button onClick={save} disabled={mutation.isPending}>
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function PlanNotesSection({ plan }: { plan: Plan }) {
  const queryClient = useQueryClient();
  const planQueryKey = useMemo(() => ["plan", plan.id] as const, [plan.id]);
  const descriptionText = String(plan.description || "");
  const contextText = String(plan.context || "");
  const hasNotes = descriptionText.trim().length > 0 || contextText.trim().length > 0;

  const [editorOpen, setEditorOpen] = useState(false);
  const [docTab, setDocTab] = useState<"edit" | "preview">("edit");
  const [draftDescription, setDraftDescription] = useState("");
  const [draftContext, setDraftContext] = useState("");

  const mutation = useMutation({
    mutationFn: async (payload: { description: string; context: string }) => {
      const resp = await editTask({
        taskId: plan.id,
        description: payload.description,
        context: payload.context,
      });
      if (!resp.success) throw new Error(resp.error || "Failed to update notes");
      return resp;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: planQueryKey });
      toast.success("Notes updated");
    },
    onError: (err) => {
      toast.error(err instanceof Error ? err.message : "Failed to update notes");
    },
  });

  const openEditor = () => {
    setDocTab("edit");
    setDraftDescription(descriptionText);
    setDraftContext(contextText);
    setEditorOpen(true);
  };

  const save = () => {
    mutation.mutate({
      description: String(draftDescription ?? ""),
      context: String(draftContext ?? ""),
    });
    setEditorOpen(false);
  };

  const previewMarkdown = [
    draftDescription.trim().length > 0 ? "### Description\n\n" + draftDescription.trim() : "",
    draftContext.trim().length > 0 ? "### Context\n\n" + draftContext.trim() : "",
  ]
    .filter((part) => part.length > 0)
    .join("\n\n");

  return (
    <>
      <section className="mb-6">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm font-medium text-foreground-muted">
            <FileText className="h-4 w-4" />
            <span>Notes</span>
          </div>

          <Button variant="outline" size="sm" className="h-8 gap-2" onClick={openEditor} disabled={mutation.isPending}>
            <Edit3 className="h-4 w-4" />
            Edit
          </Button>
        </div>

        <div className="rounded-lg border border-border bg-background-subtle p-[var(--density-card-pad)]">
          {hasNotes ? (
            <div className="space-y-6">
              <div>
                <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-foreground-subtle">
                  Description
                </div>
                {descriptionText.trim().length > 0 ? (
                  <Markdown content={descriptionText} className="text-sm" />
                ) : (
                  <div className="text-sm text-foreground-muted">Empty.</div>
                )}
              </div>

              <div>
                <div className="mb-2 text-xs font-semibold uppercase tracking-wider text-foreground-subtle">
                  Context
                </div>
                {contextText.trim().length > 0 ? (
                  <Markdown content={contextText} className="text-sm" />
                ) : (
                  <div className="text-sm text-foreground-muted">Empty.</div>
                )}
              </div>
            </div>
          ) : (
            <div className="text-sm text-foreground-muted">
              Keep background, constraints, and implementation notes here.
            </div>
          )}
        </div>
      </section>

      <Dialog open={editorOpen} onOpenChange={(open) => (open ? setEditorOpen(true) : setEditorOpen(false))}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>Edit Notes</DialogTitle>
          </DialogHeader>

          <div className="flex items-center gap-2">
            <button
              type="button"
              className={cn(
                "rounded-md px-3 py-1 text-xs font-semibold transition-colors",
                docTab === "edit" ? "bg-background text-foreground" : "text-foreground-muted hover:bg-background-hover"
              )}
              onClick={() => setDocTab("edit")}
            >
              Edit
            </button>
            <button
              type="button"
              className={cn(
                "rounded-md px-3 py-1 text-xs font-semibold transition-colors",
                docTab === "preview" ? "bg-background text-foreground" : "text-foreground-muted hover:bg-background-hover"
              )}
              onClick={() => setDocTab("preview")}
            >
              Preview
            </button>
          </div>

          {docTab === "edit" ? (
            <div className="space-y-4">
              <div className="space-y-2">
                <div className="text-xs font-semibold uppercase tracking-wider text-foreground-subtle">
                  Description
                </div>
                <Textarea
                  value={draftDescription}
                  onChange={(e) => setDraftDescription(e.target.value)}
                  rows={5}
                  placeholder="Short overview of the plan (optional)"
                />
              </div>
              <div className="space-y-2">
                <div className="text-xs font-semibold uppercase tracking-wider text-foreground-subtle">
                  Context
                </div>
                <Textarea
                  value={draftContext}
                  onChange={(e) => setDraftContext(e.target.value)}
                  rows={10}
                  placeholder="Background, constraints, links, decisions, logs…"
                />
              </div>
            </div>
          ) : (
            <div className="max-h-[60vh] overflow-y-auto rounded-md border border-border bg-background-subtle p-4">
              {previewMarkdown.trim().length > 0 ? (
                <Markdown content={previewMarkdown} className="text-sm" />
              ) : (
                <div className="text-sm text-foreground-muted">Nothing to preview.</div>
              )}
            </div>
          )}

          <DialogFooter>
            <Button variant="outline" onClick={() => setEditorOpen(false)} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button onClick={save} disabled={mutation.isPending}>
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
