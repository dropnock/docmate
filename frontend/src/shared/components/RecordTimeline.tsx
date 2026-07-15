import { Badge, Descriptions, Drawer, Tag, Timeline, Typography } from "antd";
import dayjs from "dayjs";
import type { AuditEvent } from "@shared/types";

const ACTION_COLOR: Record<string, string> = {
  created: "blue",
  locked: "orange",
  unlocked: "cyan",
  lock_expired: "gold",
  assigned: "purple",
  reassigned: "geekblue",
  indexing_submitted: "green",
  version_created: "magenta",
  qa_passed: "success",
  qa_failed: "error",
  qc_passed: "success",
  qc_rejected: "error",
  disqualified: "error",
  batch_escalated: "warning",
  stale_flagged: "warning",
  status_changed: "default",
  sampled: "lime",
};

function EventDetail({ event }: { event: AuditEvent }) {
  return (
    <Descriptions size="small" column={1}>
      {event.actor_name && <Descriptions.Item label="By">{event.actor_name}</Descriptions.Item>}
      <Descriptions.Item label="At">{dayjs(event.performed_at).format("YYYY-MM-DD HH:mm:ss")}</Descriptions.Item>
      {event.new_value && (
        <Descriptions.Item label="New">
          <pre style={{ margin: 0, fontSize: 11 }}>{JSON.stringify(event.new_value, null, 2)}</pre>
        </Descriptions.Item>
      )}
      {event.metadata && (
        <Descriptions.Item label="Meta">
          <pre style={{ margin: 0, fontSize: 11 }}>{JSON.stringify(event.metadata, null, 2)}</pre>
        </Descriptions.Item>
      )}
    </Descriptions>
  );
}

interface Props {
  events: AuditEvent[];
}

export default function RecordTimeline({ events }: Props) {
  return (
    <Timeline
      items={events.map((e) => ({
        key: e.id,
        color: ACTION_COLOR[e.action] || "blue",
        children: (
          <div>
            <Tag color={ACTION_COLOR[e.action]}>{e.action.replace(/_/g, " ").toUpperCase()}</Tag>
            <Typography.Text type="secondary" style={{ fontSize: 12, marginLeft: 8 }}>
              {dayjs(e.performed_at).format("MMM D, HH:mm")}
              {e.actor_name && ` · ${e.actor_name}`}
            </Typography.Text>
            {e.new_value && (
              <div style={{ marginTop: 4 }}>
                <EventDetail event={e} />
              </div>
            )}
          </div>
        ),
      }))}
    />
  );
}
