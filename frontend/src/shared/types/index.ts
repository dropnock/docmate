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
}

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
  aql_status_snapshot: "normal" | "tightened" | "reduced" | null;
  qc_completed_at: string | null;
  critical_defect_count: number | null;
  minor_defect_count: number | null;
  released_at: string | null;
  released_by: number | null;
  created_by: number | null;
}

export interface QcProjectSummary {
  project_id: number;
  lots_quality_checked: number;
  lots_rejected: number;
  records_passed: number;
  qc_daily_throughput: number;
}

export interface AQLConfig {
  current_status: string;
  current_aql_level: number;
  consecutive_passes: number;
  consecutive_failures: number;
  normal_aql: number;
  tightened_aql: number;
  reduced_aql: number;
  passes_to_reduce: number;
  failures_to_tighten: number;
  sampling_mode: "iso" | "manual";
}

export type QcFieldStatus = "accepted" | "defective";

export interface FieldResult {
  field_key: string;
  status: QcFieldStatus;
  note?: string;
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
}

export interface QcFieldResultsOut {
  lot_id: number;
  any_critical_defect: boolean;
  non_critical_defect_total: number;
  acceptance_number: number | null;
  fields: FieldResultSummary[];
}

export interface LotDetail extends Lot {
  records: {
    record_id: number;
    source_identifier: string | null;
    original_filename: string | null;
    status: string;
    is_sampled: boolean;
  }[];
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
  batches_indexed: number;
  batches_qa_completed: number;
  batches_to_be_qad: number;
  total_records_indexed: number;
  total_records_qad: number;
  total_records_remaining: number;
  records_withdrawn: number;
  records_illegible: number;
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

export type LotInspectionStatus = "in_progress" | "passed" | "failed" | "cancelled";
export type InspectionItemStatus = "pending" | "accepted" | "defective";

export interface InspectionFieldResult {
  field_key: string;
  title: string;
  status: QcFieldStatus;
  is_critical: boolean;
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
}

export interface LotInspectionSummary {
  id: number;
  lot_id: number;
  attempt_number: number;
  status: LotInspectionStatus;
  aql_level: number;
  lot_size: number;
  code_letter: string;
  sample_size: number;
  acceptance_number: number;
  rejection_number: number;
  random_seed: string;
  critical_defect_count: number | null;
  minor_defect_count: number | null;
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
}

export interface LotInspectionList {
  available_aql_levels: number[];
  default_aql_level: number;
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
  inspections: LotInspectionDetail[];
}
