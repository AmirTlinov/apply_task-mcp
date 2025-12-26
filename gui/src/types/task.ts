/** Canonical status codes from backend serializers. */
export type TaskStatus = "TODO" | "ACTIVE" | "DONE";

/** Nested Step computed status. */
export type StepStatus = "pending" | "in_progress" | "blocked" | "completed";

export interface VerificationCheck {
  kind: string;
  spec: string;
  outcome: string;
  observed_at?: string;
  digest?: string;
  preview?: string;
  details?: Record<string, unknown>;
}

export interface Attachment {
  kind: string;
  path?: string;
  uri?: string;
  external_uri?: string;
  size?: number;
  digest?: string;
  observed_at?: string;
  meta?: Record<string, unknown>;
}

export interface TaskNode {
  path: string;
  id?: string;
  title: string;
  status: TaskStatus;
  status_code: TaskStatus;
  progress: number;
  priority?: "LOW" | "MEDIUM" | "HIGH";
  description?: string;
  context?: string;
  success_criteria?: string[];
  tests?: string[];
  criteria_confirmed?: boolean;
  tests_confirmed?: boolean;
  criteria_auto_confirmed?: boolean;
  tests_auto_confirmed?: boolean;
  criteria_notes?: string[];
  tests_notes?: string[];
  dependencies?: string[];
  next_steps?: string[];
  problems?: string[];
  risks?: string[];
  blocked?: boolean;
  blockers?: string[];
  status_manual?: boolean;
  attachments?: Attachment[];
  steps?: Step[];
}

export interface StepPlan {
  title: string;
  doc: string;
  attachments?: Attachment[];
  success_criteria?: string[];
  tests?: string[];
  blockers?: string[];
  criteria_confirmed?: boolean;
  tests_confirmed?: boolean;
  criteria_auto_confirmed?: boolean;
  tests_auto_confirmed?: boolean;
  criteria_notes?: string[];
  tests_notes?: string[];
  steps: string[];
  current: number;
  tasks?: TaskNode[];
}

/** Plan checklist stored on a Plan (PLAN-###). */
export interface PlanChecklist {
  steps: string[];
  current: number;
  doc: string;
}

/** Plan (TaskDetail(kind="plan")). */
export interface Plan {
  id: string;
  kind: "plan";
  title: string;
  domain?: string;
  created_at?: string | null;
  updated_at?: string | null;
  tags?: string[];
  description?: string;
  contract?: string;
  contract_versions_count?: number;
  context?: string;
  attachments?: Attachment[];
  success_criteria?: string[];
  tests?: string[];
  blockers?: string[];
  criteria_confirmed?: boolean;
  tests_confirmed?: boolean;
  criteria_auto_confirmed?: boolean;
  tests_auto_confirmed?: boolean;
  criteria_notes?: string[];
  tests_notes?: string[];
  plan: PlanChecklist;
  project_remote_updated?: string | null;
  events?: TaskEvent[];
}

/** Nested step node (recursive) from step_to_dict(). */
export interface Step {
  path: string;
  id?: string;
  title: string;
  completed: boolean;
  success_criteria: string[];
  tests: string[];
  blockers: string[];
  attachments?: Attachment[];
  verification_checks?: VerificationCheck[];
  verification_outcome?: string;
  criteria_confirmed: boolean;
  tests_confirmed: boolean;
  criteria_auto_confirmed?: boolean;
  tests_auto_confirmed?: boolean;
  criteria_notes: string[];
  tests_notes: string[];
  created_at?: string | null;
  completed_at?: string | null;
  progress_notes?: string[];
  started_at?: string | null;
  blocked?: boolean;
  block_reason?: string;
  computed_status?: StepStatus;
  plan?: StepPlan;
}

/** Task (TaskDetail(kind="task")). */
export interface Task {
  id: string;
  kind: "task";
  title: string;
  revision: number;
  status: TaskStatus;
  status_code: TaskStatus;
  progress: number;
  parent: string | null; // PLAN-### or null
  priority?: "LOW" | "MEDIUM" | "HIGH";
  domain?: string;
  phase?: string;
  component?: string;
  tags?: string[];
  assignee?: string;
  blocked?: boolean;
  blockers?: string[];
  description?: string;
  context?: string;
  attachments?: Attachment[];
  depends_on?: string[];
  success_criteria?: string[];
  tests?: string[];
  criteria_confirmed?: boolean;
  tests_confirmed?: boolean;
  criteria_auto_confirmed?: boolean;
  tests_auto_confirmed?: boolean;
  criteria_notes?: string[];
  tests_notes?: string[];
  dependencies?: string[];
  next_steps?: string[];
  problems?: string[];
  risks?: string[];
  history?: string[];
  created_at?: string | null;
  updated_at?: string | null;
  steps_count?: number;
  steps?: Step[];
  project_remote_updated?: string | null;
  events?: TaskEvent[];
}

/** Compact task view used in context list (task_to_dict(compact=true)). */
export interface TaskListItem {
  id: string;
  kind: "task";
  title: string;
  revision: number;
  status: TaskStatus;
  status_code: TaskStatus;
  progress: number;
  domain?: string;
  parent?: string | null;
  blocked?: boolean;
  criteria_confirmed?: boolean;
  tests_confirmed?: boolean;
  criteria_auto_confirmed?: boolean;
  tests_auto_confirmed?: boolean;
  steps_count: number;
  steps?: Array<
    Pick<
      Step,
      "path" | "title" | "completed" | "criteria_confirmed" | "tests_confirmed" | "criteria_auto_confirmed" | "tests_auto_confirmed"
    > & { ready?: boolean; needs?: string[]; status?: StepStatus; plan?: StepPlan }
  >;
  tags?: string[];
  updated_at?: string;
  namespace?: string;
}

/** Compact plan view used in context list (plan_to_dict(compact=True)). */
export interface PlanListItem {
  id: string;
  kind: "plan";
  title: string;
  domain?: string;
  contract_preview?: string;
  contract_versions_count?: number;
  plan_progress?: string;
  plan_doc_preview?: string;
  criteria_confirmed?: boolean;
  tests_confirmed?: boolean;
  criteria_auto_confirmed?: boolean;
  tests_auto_confirmed?: boolean;
}

/** Task event for timeline */
export interface TaskEvent {
  id: string;
  task_id: string;
  event_type: "created" | "updated" | "completed" | "checkpoint" | "note";
  description: string;
  timestamp: string;
  user?: string;
  data?: Record<string, unknown>;
}

/** Namespace info from tasks_storage */
export interface Namespace {
  namespace: string;
  path: string;
  task_count: number;
}

/** Storage info response */
export interface StorageInfo {
  global_storage: string;
  global_exists: boolean;
  local_storage: string;
  local_exists: boolean;
  current_storage: string;
  current_namespace: string;
  namespaces: Namespace[];
}

/** Project/namespace info */
export interface Project {
  namespace: string;
  name: string;
  path: string;
  task_count: number;
  last_activity?: string;
}
