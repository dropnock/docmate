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
  stale_threshold_hours: number;
  digitizing_org_id: number;
  customer_org_id: number;
}

export interface Batch {
  id: number;
  project_id: number;
  document_type_id: number;
  name: string;
  status: string;
  aql_level_snapshot: number | null;
  aql_sample_size: number | null;
}

export interface DocRecord {
  id: number;
  batch_id: number;
  file_reference: string | null;
  indexed_data: { [key: string]: unknown } | null;
  current_version: number;
  locked_by: number | null;
  status: string;
}

export interface Task {
  id: number;
  record_id: number;
  batch_id: number;
  task_type: string;
  assigned_to: number | null;
  status: string;
  due_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  processing_time_seconds: number | null;
}

export interface StaffMetric {
  user_id: number;
  full_name: string;
  email: string;
  role: string;
  total_records_processed: number;
  records_today: number;
  avg_processing_time_seconds: number;
  error_rate: number;
  stale_task_count: number;
  tasks_in_progress: number;
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

export interface Shift {
  id: number;
  tenant_id: number;
  name: string;
  start_time: string;
  end_time: string;
  timezone: string;
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
}

export interface DocumentType {
  id: number;
  project_id: number;
  name: string;
  json_schema: { [key: string]: unknown };
}
