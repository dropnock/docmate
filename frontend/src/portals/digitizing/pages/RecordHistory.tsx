import { Card, Empty, Select, Space, Spin, Tabs, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import api from "@shared/api/client";
import RecordTimeline from "@shared/components/RecordTimeline";
import type { AuditEvent, Batch, DocRecord, RecordVersion } from "@shared/types";

interface Props {
  projectId: number;
}

const RECORD_STATUS_COLOR: Record<string, string> = {
  pending: "Pending",
  indexing: "Indexing",
  indexed: "Indexed",
  qa_pending: "QA pending",
  qa_passed: "QA passed",
  qa_failed: "QA failed",
  qc_pending: "QC pending",
  qc_passed: "QC passed",
  qc_failed: "QC failed",
  disqualified: "Disqualified",
};

function recordLabel(record: DocRecord) {
  const fileName = record.file_reference?.split("/").pop();
  const status = RECORD_STATUS_COLOR[record.status] ?? record.status.replace(/_/g, " ");
  return fileName ? `#${record.id} - ${status} - ${fileName}` : `#${record.id} - ${status}`;
}

export default function RecordHistory({ projectId }: Props) {
  const [batchId, setBatchId] = useState<number | null>(null);
  const [recordId, setRecordId] = useState<number | null>(null);

  const { data: batches = [], isLoading: batchesLoading } = useQuery<Batch[]>({
    queryKey: ["batches", projectId],
    queryFn: () => api.get(`/projects/${projectId}/batches`).then((r) => r.data),
  });

  const { data: records = [], isLoading: recordsLoading } = useQuery<DocRecord[]>({
    queryKey: ["records", batchId],
    queryFn: () => api.get(`/batches/${batchId}/records`).then((r) => r.data),
    enabled: !!batchId,
  });

  const { data: events } = useQuery<AuditEvent[]>({
    queryKey: ["record-history", recordId],
    queryFn: () => api.get(`/records/${recordId}/history`).then((r) => r.data),
    enabled: !!recordId,
  });

  const { data: versions } = useQuery<RecordVersion[]>({
    queryKey: ["record-versions", recordId],
    queryFn: () => api.get(`/records/${recordId}/versions`).then((r) => r.data),
    enabled: !!recordId,
  });

  return (
    <div>
      <Typography.Title level={4}>Record History</Typography.Title>
      <Space style={{ marginBottom: 16 }} wrap>
        <Select
          placeholder="Select batch"
          style={{ width: 260 }}
          loading={batchesLoading}
          value={batchId}
          options={batches.map((b) => ({
            value: b.id,
            label: `${b.name} (${b.status.replace(/_/g, " ")})`,
          }))}
          onChange={(nextBatchId) => {
            setBatchId(nextBatchId);
            setRecordId(null);
          }}
        />
        <Select
          showSearch
          placeholder="Select record"
          style={{ width: 360 }}
          loading={recordsLoading}
          value={recordId}
          disabled={!batchId}
          optionFilterProp="label"
          options={records.map((record) => ({
            value: record.id,
            label: recordLabel(record),
          }))}
          onChange={setRecordId}
        />
      </Space>

      {!batchId ? (
        <Empty description="Select a batch to choose a record" />
      ) : recordsLoading ? (
        <Spin />
      ) : !recordId ? (
        <Empty description="Select a record to view its audit trail and versions" />
      ) : (
        <Card>
          <Typography.Title level={5}>Record #{recordId}</Typography.Title>
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
                      <Card
                        key={v.id}
                        size="small"
                        title={`Version ${v.version_number} - ${v.reason}`}
                        style={{ marginBottom: 12 }}
                      >
                        <pre style={{ fontSize: 12 }}>{JSON.stringify(v.indexed_data, null, 2)}</pre>
                      </Card>
                    ))}
                  </div>
                ),
              },
            ]}
          />
        </Card>
      )}
    </div>
  );
}
