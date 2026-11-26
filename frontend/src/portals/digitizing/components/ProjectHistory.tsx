import { Empty, Spin, Tag, Timeline, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import dayjs from "dayjs";
import { getProjectHistory, projectHistoryKey } from "@shared/api/aql";
import { ERROR_TYPE_LABEL } from "@shared/components/DefectClasses";
import type { AuditEvent, ErrorType } from "@shared/types";

const FIELD_LABEL: Record<string, string> = {
  name: "Name",
  description: "Description",
  proposed_end_date: "Proposed end date",
  customer_org_id: "Customer organisation",
  critical_aql: "Critical AQL",
  major_aql: "Major AQL",
  normal_aql: "Major AQL", // stored name of the major AQL
  minor_aql: "Minor AQL",
  error_classes: "Error-type agreement",
  general_level: "General inspection level",
  sampling_mode: "Sampling",
  passes_to_reduce: "Passes to reduce",
  failures_to_tighten: "Failures to tighten",
  tightened_aql: "Tightened AQL",
  reduced_aql: "Reduced AQL",
  aql_status: "Suggested inspection level",
  status: "Status",
  rows: "Rows",
};

const ACTION: Record<string, { label: string; color: string }> = {
  created: { label: "Project created", color: "blue" },
  settings_changed: { label: "Settings changed", color: "gold" },
  batch_escalated: { label: "Inspection tightened", color: "red" },
  flags_exported: { label: "Flags exported", color: "default" },
};

const AQL_FIELDS = new Set(["critical_aql", "major_aql", "normal_aql", "minor_aql", "tightened_aql", "reduced_aql"]);

function formatValue(field: string, v: unknown, orgName: (id: number) => string | undefined): string {
  if (AQL_FIELDS.has(field)) return v === null || v === undefined ? (field === "critical_aql" ? "Zero tolerance" : "—") : `${v}%`;
  if (v === null || v === undefined || v === "") return "—";
  if (field === "customer_org_id" && typeof v === "number") return orgName(v) ?? `#${v}`;
  if (field === "error_classes" && typeof v === "object") {
    return Object.entries(v as Record<string, string>)
      .map(([t, c]) => `${ERROR_TYPE_LABEL[t as ErrorType] ?? t} → ${c}`)
      .join(", ");
  }
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

/** Before the settings_changed action, AQL edits were filed as status_changed. */
function actionOf(e: AuditEvent) {
  if (ACTION[e.action]) return ACTION[e.action];
  if (e.action === "status_changed") {
    return e.new_value && "aql_status" in e.new_value
      ? { label: "Inspection level switched", color: "default" }
      : ACTION.settings_changed;
  }
  return { label: e.action.replace(/_/g, " "), color: "default" };
}

function Changes({ event, orgName }: { event: AuditEvent; orgName: (id: number) => string | undefined }) {
  const fields = Object.keys(event.new_value ?? {});
  if (!fields.length) return null;
  const isCreate = event.action === "created";
  return (
    <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
      {fields.map((f) => (
        <li key={f} data-history-field={f}>
          <Typography.Text type="secondary">{FIELD_LABEL[f] ?? f}: </Typography.Text>
          {!isCreate && event.old_value && f in event.old_value && (
            <>
              <Typography.Text delete type="secondary">{formatValue(f, event.old_value[f], orgName)}</Typography.Text>
              {" → "}
            </>
          )}
          <Typography.Text>{formatValue(f, event.new_value![f], orgName)}</Typography.Text>
        </li>
      ))}
    </ul>
  );
}

/** A project's audit trail: who created it with which AQLs, and every later
 * change to its details or quality settings, newest first. */
export default function ProjectHistory({
  projectId, orgName,
}: { projectId: number; orgName: (id: number) => string | undefined }) {
  const { data: events, isLoading } = useQuery({
    queryKey: projectHistoryKey(projectId),
    queryFn: () => getProjectHistory(projectId),
  });
  if (isLoading) return <Spin />;
  if (!events?.length) return <Empty description="No changes recorded yet" image={Empty.PRESENTED_IMAGE_SIMPLE} />;
  return (
    <Timeline
      items={events.map((e) => {
        const a = actionOf(e);
        return {
          key: e.id,
          children: (
            <div data-history-event={e.action}>
              <Tag color={a.color}>{a.label}</Tag>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {dayjs(e.performed_at).format("YYYY-MM-DD HH:mm")}
                {e.actor_name ? ` · ${e.actor_name}` : e.performed_by === null ? " · automatic" : ""}
              </Typography.Text>
              <Changes event={e} orgName={orgName} />
            </div>
          ),
        };
      })}
    />
  );
}
