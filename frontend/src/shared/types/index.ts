export interface AuthUser {
  user_id: number;
  role: string;
  portal: string;
  full_name: string;
  access_token: string;
}

export interface Project {
  id: number;
  tenant_id: number;
  name: string;
  description: string | null;
  proposed_end_date: string | null;
  s3_bucket_name: string | null;
  s3_bucket_status: string;
  digitizing_org_id: number;
  customer_org_id: number;
}

export interface Organization {
  id: number;
  name: string;
  type: string;
}

export interface Cabinet {
  id: number;
  project_id: number;
  organization_id: number | null;
  name: string;
  description: string | null;
  created_by: number | null;
  created_at: string | null;
}

export interface CabinetRecord {
  id: number;
  source_identifier: string | null;
  original_filename: string | null;
  file_reference: string | null;
  status: string;
  current_version: number;
  has_image: boolean;
  has_data: boolean;
  cabinet_id: number | null;
  batch_id: number | null;
  on_hold?: boolean;
}

export type InspectionLevel = "normal" | "tightened" | "reduced";
/** ISO 2859-1 general inspection level: the Table 1 column (lot size -> code letter). */
export type GeneralLevel = "I" | "II" | "III";

export interface Lot {
  id: number;
  project_id: number;
  name: string;
  description: string | null;
  status: string;
  sample_rate: number | null;
  sample_size: number | null;
  acceptance_number: number | null;
  accuracy_rate: number | null;
  /** Inspection level the customer's sample was drawn under. */
  aql_status_snapshot: InspectionLevel | null;
  /** Inspection level the supervisor set for the lot when inspecting it. */
  inspection_level: InspectionLevel | null;
  qc_completed_at: string | null;
  critical_defect_count: number | null;
  major_defect_count?: number | null;
  minor_defect_count: number | null;
  /** Per-class Ac/Re of the customer's sample; null before defect classes. */
  class_plan?: ClassResults | null;
  released_at: string | null;
  released_by: number | null;
  created_by: number | null;
  /** Current customer QC round; earlier (failed) rounds are in LotDetail.qc_rounds. */
  qc_round: number;
  /** The customer QC supervisor who confirmed the verdict, and when. */
  decided_at?: string | null;
  decided_by?: number | null;
}

/** A finished customer QC round of a lot that was failed and resubmitted. */
export interface LotQcRound {
  round_number: number;
  status: string;
  record_count: number;
  sample_rate: number | null;
  sample_size: number | null;
  acceptance_number: number | null;
  accuracy_rate: number | null;
  aql_status_snapshot: InspectionLevel | null;
  critical_defect_count: number | null;
  major_defect_count?: number | null;
  minor_defect_count: number | null;
  class_plan?: ClassResults | null;
  sampled_record_ids: number[];
  released_at: string | null;
  qc_completed_at: string | null;
  created_at: string;
}

export interface QcProjectSummary {
  project_id: number;
  lots_quality_checked: number;
  lots_rejected: number;
  records_passed: number;
  qc_daily_throughput: number;
}

export interface AQLConfig {
  /** Suggested inspection level for the next lot (ISO 2859-1 switching). */
  current_status: InspectionLevel;
  current_aql_level: number;
  consecutive_passes: number;
  consecutive_failures: number;
  normal_aql: number;
  tightened_aql: number;
  reduced_aql: number;
  passes_to_reduce: number;
  failures_to_tighten: number;
  sampling_mode: "iso" | "manual";
  general_level: GeneralLevel;
  /** null = zero tolerance. */
  critical_aql: number | null;
  /** Same value as normal_aql. */
  major_aql: number;
  minor_aql: number;
  error_classes: ErrorClasses;
}

export type QcFieldStatus = "accepted" | "defective";

/** What a reviewer found wrong with a field; the project's agreement with
 * the customer (AQLConfig.error_classes) gives it a class. */
export type ErrorType = "missing" | "typo" | "interpretation";
/** ISO 2859-1 non-conformity class. Identifier-field errors are critical. */
export type Severity = "critical" | "major" | "minor";
export type ErrorClasses = Record<ErrorType, "major" | "minor">;

/** One class's acceptance plan; found / accepted once the sample is judged. */
export interface ClassResult {
  /** null = zero tolerance (critical only). */
  aql: number | null;
  acceptance_number: number;
  rejection_number: number;
  found?: number | null;
  accepted?: boolean | null;
}
export type ClassResults = Record<Severity, ClassResult>;

export interface FieldResult {
  field_key: string;
  status: QcFieldStatus;
  note?: string;
  error_type?: ErrorType;
}

export interface ContributingAgent {
  id: number;
  full_name: string;
}

export interface FieldResultSummary {
  field_key: string;
  is_critical: boolean;
  defective_count: number;
  accepted_count: number;
  contributing_agents: ContributingAgent[];
  by_class?: Record<Severity, number>;
  by_error_type?: Partial<Record<ErrorType, number>>;
}

export interface QcFieldResultsOut {
  lot_id: number;
  any_critical_defect: boolean;
  non_critical_defect_total: number;
  acceptance_number: number | null;
  /** Per-class plan and current totals; null for lots sampled before classes. */
  classes: ClassResults | null;
  fields: FieldResultSummary[];
}

export interface LotDetail extends Lot {
  records: {
    record_id: number;
    source_identifier: string | null;
    original_filename: string | null;
    status: string;
    is_sampled: boolean;
    /** The record's current customer QC task, once it has been assigned. */
    qc_task: {
      task_id: number;
      batch_id: number;
      status: "pending" | "in_progress" | "completed" | "failed";
      assigned_to: number | null;
      assigned_to_name: string | null;
    } | null;
  }[];
  qc_rounds: LotQcRound[];
}

/** A customer QC batch as the customer supervisor sees it (GET
 * /lots/project/{id}/qc-batches): who holds its tasks and how far it got. */
export interface QcBatchSummary {
  id: number;
  name: string;
  document_type_id: number;
  created_at: string;
  lot_id: number | null;
  lot_name: string | null;
  total: number;
  pending: number;
  in_progress: number;
  completed: number;
  failed: number;
  assignees: { id: number; full_name: string; role: string | null; tasks: number; open: number }[];
}

export interface Batch {
  id: number;
  project_id: number;
  cabinet_id: number | null;
  document_type_id: number;
  name: string;
  batch_type: "indexing" | "qc";
  status: "draft" | "submitted" | "indexing" | "qa_review" | "customer_qc" | "passed" | "rejected" | "complete";
  aql_level_snapshot: number | null;
  aql_sample_size: number | null;
  indexer_name: string | null;
  qa_agent_name: string | null;
  record_count: number | null;
  created_at: string;
  completed_at: string | null;
}

export interface RecordsDashboard {
  /** Live lot counts. Draft lots are split by how far they are from handover. */
  lots: {
    preparing: number;
    inspecting: number;
    ready_to_release: number;
    released: number;
    in_customer_qc: number;
    passed: number;
    rejected: number;
    records_not_in_lot: number;
  };
  batches_indexed: number;
  batches_qa_completed: number;
  batches_to_be_qad: number;
  /** Live, not date-filtered: batches in indexing now, and those of them
   * that can't go on to QA while one of their records has an open flag. */
  batches_being_indexed: number;
  batches_blocked_by_flags: number;
  total_records_indexed: number;
  total_records_qad: number;
  total_records_remaining: number;
  records_withdrawn: number;
  records_illegible: number;
  /** Records with an open flag (live). */
  records_flagged: number;
}

export interface DocRecord {
  id: number;
  batch_id: number;
  cabinet_id: number | null;
  file_reference: string | null;
  original_filename: string | null;
  source_identifier: string | null;
  indexed_data: { [key: string]: unknown } | null;
  current_version: number;
  /** Rejected by QA or customer QC, or requeued, at some point (GET /records/{id}). */
  previously_sent_back?: boolean;
  locked_by: number | null;
  locked_at: string | null;
  status: string;
  updated_at?: string;
  indexed_by_id: number | null;
  indexed_by_name: string | null;
  qa_by_id: number | null;
  qa_by_name: string | null;
  // Open flag for a supervisor — set by GET /batches/{id}/records.
  flagged?: boolean;
}

export interface RecordListResponse {
  items: DocRecord[];
  total: number;
}

export interface Task {
  id: number;
  record_id: number;
  batch_id: number;
  task_type: string;
  assigned_to: number | null;
  status: string;
  started_at: string | null;
  completed_at: string | null;
  processing_time_seconds: number | null;
  active_seconds?: number;
  sessions?: number;
  // Only populated by GET /tasks/mine — see that endpoint. Null everywhere else.
  batch_status: string | null;
  // Open flag for a supervisor on the task's record — also only from /tasks/mine.
  flagged?: boolean;
}

export interface TaskTypeMetrics {
  total_records_processed: number;
  records_today: number;
  avg_processing_time_seconds: number;
  error_rate: number;
  tasks_in_progress: number;
}

export type StaffTaskType = "indexing" | "qa" | "qc";
export type StaffTaskSort = "time_desc" | "time_asc" | "finished_desc" | "finished_asc";

/** One record an agent worked on, with the time spent on it
 * (GET /analytics/staff-productivity/tasks). */
export interface StaffTaskTime {
  task_id: number;
  record_id: number;
  record_label: string;
  batch_id: number;
  batch_name: string;
  status: "completed" | "failed" | "in_progress";
  /** submitted | skipped_<status> | requeued | passed | rejected | in_progress */
  outcome: string;
  started_at: string | null;
  finished_at: string | null;
  seconds: number;
  /** 0 = timed before sessions were counted (last open to finish). */
  sessions: number;
}

export interface StaffTaskTimes {
  summary: {
    tasks: number;
    finished: number;
    total_seconds: number;
    avg_seconds: number;
    median_seconds: number;
    fastest_seconds: number;
    slowest_seconds: number;
    reopened: number;
  };
  total: number;
  rows: StaffTaskTime[];
}

export interface StaffMetric {
  user_id: number;
  full_name: string;
  email: string;
  indexing: TaskTypeMetrics;
  qa: TaskTypeMetrics;
}

export interface QcStaffMetric {
  user_id: number;
  full_name: string;
  email: string;
  qc: TaskTypeMetrics;
}

export interface ProjectKPIs {
  project_id: number;
  total_records: number;
  records_complete: number;
  records_remaining: number;
  completion_pct: number;
  daily_throughput_rate: number;
  projected_end_date: string | null;
  proposed_end_date: string | null;
  days_to_proposed_end: number | null;
  on_track: boolean | null;
  error_rate: number;
  records_inspected: number;
  defects_found: number;
}

export interface BurnupPoint {
  date: string;
  completed: number | null;
  projected: number | null;
}

export interface AuditEvent {
  id: number;
  entity_type: string;
  entity_id: number;
  action: string;
  performed_by: number | null;
  actor_name: string | null;
  performed_at: string;
  old_value: { [key: string]: unknown } | null;
  new_value: { [key: string]: unknown } | null;
  metadata: { [key: string]: unknown } | null;
}

export interface RecordVersion {
  id: number;
  record_id: number;
  version_number: number;
  indexed_data: { [key: string]: unknown };
  created_by: number;
  reason: string;
}

export interface ShiftProjectAssignment {
  project_shift_id: number;
  project_id: number;
  project_name: string;
}

export interface Shift {
  id: number;
  tenant_id: number;
  name: string;
  start_time: string;
  end_time: string;
  timezone: string;
  project_assignments: ShiftProjectAssignment[];
}

export interface UserRecord {
  id: number;
  tenant_id: number;
  email: string;
  full_name: string;
  role: string;
  portal: string;
  is_active: boolean;
  organization_id: number | null;
}

export interface Organization {
  id: number;
  tenant_id: number;
  name: string;
  type: string;
  realm_slug: string | null;
  s3_bucket_name: string | null;
  s3_bucket_status: string | null;
}

export interface AvailableStaff {
  id: number;
  full_name: string;
  email: string;
  role: string;
  shift_role: "indexer" | "qa" | null;
}

export interface BucketedStaffMember {
  assignment_id: number;
  user_id: number;
  full_name: string;
  email: string;
  has_active_work: boolean;
}

export interface StaffBuckets {
  unassigned: BucketedStaffMember[];
  indexer: BucketedStaffMember[];
  qa: BucketedStaffMember[];
}

export interface DocumentType {
  id: number;
  project_id: number;
  name: string;
  json_schema: { [key: string]: unknown };
}

// ── Outgoing lot inspection (digitizing supervisor, before release) ─────────
// See backend/app/services/lot_inspection_service.py.

/** superseded: passed, but the lot was recalled or had records removed
 * afterwards, so it no longer clears the lot for release. */
export type LotInspectionStatus = "in_progress" | "passed" | "failed" | "cancelled" | "superseded";
export type InspectionItemStatus = "pending" | "accepted" | "defective";

export interface InspectionFieldResult {
  field_key: string;
  title: string;
  status: QcFieldStatus;
  is_critical: boolean;
  error_type: ErrorType | null;
  severity: Severity | null;
  note: string | null;
}

export interface InspectionItem {
  record_id: number;
  source_identifier: string | null;
  original_filename: string | null;
  document_type_id: number | null;
  record_version: number;
  status: InspectionItemStatus;
  reviewed_at: string | null;
  field_results: InspectionFieldResult[];
}

export interface InspectionFieldTally {
  field_key: string;
  title: string;
  is_critical: boolean;
  defective_count: number;
  /** Per class; absent on reports from before defect classes. */
  critical?: number;
  major?: number;
  minor?: number;
}

export interface LotInspectionSummary {
  id: number;
  lot_id: number;
  attempt_number: number;
  status: LotInspectionStatus;
  /** Omitted from the customer's report: customers see the level only. */
  aql_level?: number;
  inspection_level: InspectionLevel;
  general_level: GeneralLevel;
  lot_size: number;
  code_letter: string;
  sample_size: number;
  acceptance_number: number;
  rejection_number: number;
  random_seed: string;
  critical_defect_count: number | null;
  major_defect_count: number | null;
  minor_defect_count: number | null;
  /** Per-class plan and verdict; null for inspections from before classes. */
  classes: ClassResults | null;
  defective_record_count: number | null;
  summary_note: string | null;
  inspected_by: number;
  inspector_name: string | null;
  started_at: string;
  completed_at: string | null;
}

export interface LotInspectionDetail extends LotInspectionSummary {
  reviewed_count: number;
  items: InspectionItem[];
  field_tally: InspectionFieldTally[];
  /** The project's current agreement (working view only), to show each
   * mark's class before it is saved. */
  error_classes?: ErrorClasses;
}

export interface SamplingPlan {
  code_letter: string;
  sample_size: number;
  acceptance_number: number;
  rejection_number: number;
  /** Per-class Ac/Re at this sample size. */
  classes?: ClassResults;
}

export interface LotInspectionList {
  /** The project's AQL; the supervisor chooses only the level. */
  aql_level: number;
  /** The project's general inspection level (I / II / III). */
  general_level: GeneralLevel;
  /** What ISO 2859-1's switching rules suggest for this lot. */
  suggested_level: InspectionLevel;
  lot_size: number;
  /** The project's critical / major / minor AQLs (critical null = zero tolerance). */
  class_aqls?: Record<Severity, number | null>;
  level_plans: Partial<Record<InspectionLevel, SamplingPlan>>;
  inspections: LotInspectionSummary[];
}

export interface LotInspectionReport {
  lot_id: number;
  lot_name: string;
  lot_status: string;
  project_name: string | null;
  lot_size: number;
  released_at: string | null;
  standard: string;
  final_status: LotInspectionStatus | null;
  inspection_level: InspectionLevel | null;
  inspections: LotInspectionDetail[];
}

/** GET /api/users/online — presence_service.online_users. */
export interface OnlineTask {
  id: number;
  task_type: "indexing" | "qa" | "qc";
  record_id: number;
  record_name: string;
  batch_name: string;
  project_name: string;
  last_active_at: string | null;
  /** Input in the task within the last 90 seconds. */
  working: boolean;
}

export interface OnlineUser {
  id: number;
  full_name: string;
  email: string;
  role: string;
  portal: "digitizing" | "customer";
  organization_name: string | null;
  last_seen_at: string;
  session_started_at: string | null;
  /** active: a request in the last 2 minutes; idle: older. */
  status: "active" | "idle";
  task: OnlineTask | null;
}

/** A field mark as the QC supervisor's review shows it. */
export interface QcReviewMark {
  status: QcFieldStatus;
  error_type: ErrorType | null;
  severity: Severity | null;
  note: string | null;
}

export interface QcReviewField {
  field_key: string;
  is_critical: boolean;
  /** The reviewer's own mark, never changed. */
  reviewer: QcReviewMark;
  /** The supervisor's change, if they made one. */
  override: QcReviewMark | null;
  overridden_by: string | null;
  overridden_at: string | null;
  /** The mark that counts: override ?? reviewer. */
  effective: QcReviewMark;
}

export interface QcReviewRecord {
  record_id: number;
  source_identifier: string | null;
  original_filename: string | null;
  document_type_id: number | null;
  indexed_data: Record<string, unknown>;
  status: string;
  qc_task_id: number | null;
  reviewer_passed: boolean | null;
  reviewer_name: string | null;
  reviewed_at: string | null;
  defect_count: number;
  overridden: boolean;
  fields: QcReviewField[];
}

/** GET /lots/{id}/qc-review — the customer QC supervisor's review of a lot. */
export interface QcReview {
  lot_id: number;
  lot_name: string;
  project_id: number;
  status: string;
  qc_round: number;
  sample_size: number | null;
  error_classes: ErrorClasses;
  /** false: sampled at a manual rate (whole-record rulings, 90% accuracy rule). */
  iso: boolean;
  acceptance_number: number | null;
  classes: ClassResults | null;
  /** The verdict the plan gives on the current marks. */
  accepted: boolean | null;
  accuracy_rate: number | null;
  any_critical_defect: boolean | null;
  non_critical_defect_total: number | null;
  records: QcReviewRecord[];
}
