import { Card, Tabs, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import api from "@shared/api/client";
import RecordTimeline from "@shared/components/RecordTimeline";
import type { AuditEvent, RecordVersion } from "@shared/types";

interface Props { recordId: number }

export default function RecordHistory({ recordId }: Props) {
  const { data: events } = useQuery<AuditEvent[]>({
    queryKey: ["record-history", recordId],
    queryFn: () => api.get(`/records/${recordId}/history`).then((r) => r.data),
  });

  const { data: versions } = useQuery<RecordVersion[]>({
    queryKey: ["record-versions", recordId],
    queryFn: () => api.get(`/records/${recordId}/versions`).then((r) => r.data),
  });

  return (
    <Card>
      <Typography.Title level={4}>Record #{recordId} — History</Typography.Title>
      <Tabs
        items={[
          {
            key: "timeline",
            label: "Audit Timeline",
            children: <RecordTimeline events={events ?? []} />,
          },
          {
            key: "versions",
            label: `Versions (${versions?.length ?? 0})`,
            children: (
              <div>
                {versions?.map((v) => (
                  <Card key={v.id} size="small" title={`Version ${v.version_number} — ${v.reason}`} style={{ marginBottom: 12 }}>
                    <pre style={{ fontSize: 12 }}>{JSON.stringify(v.indexed_data, null, 2)}</pre>
                  </Card>
                ))}
              </div>
            ),
          },
        ]}
      />
    </Card>
  );
}
