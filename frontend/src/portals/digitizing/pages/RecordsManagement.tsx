import { useMemo, useState } from "react";
import {
  Button, Card, Col, DatePicker, Drawer, Empty, Modal, Row, Select, Space,
  Statistic, Table, Tabs, Tag, Typography, message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs, { type Dayjs } from "dayjs";
import api from "@shared/api/client";
import { formatApiError } from "@shared/api/errors";
import PageHeader from "@shared/components/PageHeader";
import PageSkeleton from "@shared/components/PageSkeleton";
import RecordTimeline from "@shared/components/RecordTimeline";
import { useAvailableStaff } from "@shared/hooks/useAvailableStaff";
import type {
  AuditEvent, Batch, DocRecord, RecordsDashboard, RecordVersion, Shift,
} from "@shared/types";

const { RangePicker } = DatePicker;

interface Props {
  projectId: number;
}

const BATCH_STATUS_OPTIONS = [
  "draft", "submitted", "indexing", "qa_review", "customer_qc", "passed", "rejected", "complete",
];

const BATCH_STATUS_COLOR: Record<string, string> = {
  draft: "default",
  submitted: "processing",
  indexing: "processing",
  qa_review: "warning",
  customer_qc: "warning",
  passed: "success",
  rejected: "error",
  complete: "success",
};

const REQUIRED_SHIFT_ROLE: Record<string, "indexer" | "qa"> = {
  indexing: "indexer",
  qa: "qa",
};

type DateRange = [Dayjs | null, Dayjs | null] | null;

function toDateParams(range: DateRange) {
  const [from, to] = range ?? [null, null];
  return {
    date_from: from ? from.startOf("day").toISOString() : undefined,
    date_to: to ? to.endOf("day").toISOString() : undefined,
  };
}

function DashboardTab({ projectId }: { projectId: number }) {
  const [range, setRange] = useState<DateRange>(null);
  const { date_from, date_to } = toDateParams(range);

  const { data, isLoading } = useQuery<RecordsDashboard>({
    queryKey: ["records-dashboard", projectId, date_from, date_to],
    queryFn: () =>
      api
        .get("/analytics/records-dashboard", { params: { project_id: projectId, date_from, date_to } })
        .then((r) => r.data),
    refetchInterval: 60_000,
  });

  const tiles = [
    { title: "Batches Indexed", value: data?.batches_indexed },
    { title: "Batches QA'd", value: data?.batches_qa_completed },
    { title: "Total Records", value: data?.total_records },
    { title: "Records Withdrawn", value: data?.records_withdrawn },
    { title: "Records Illegible", value: data?.records_illegible },
  ];

  return (
    <div>
      <Space style={{ marginBottom: 16 }}>
        <RangePicker value={range as never} onChange={(v) => setRange(v as DateRange)} allowClear />
      </Space>
      {isLoading ? (
        <PageSkeleton variant="cards" count={5} />
      ) : (
        <Row gutter={[16, 16]}>
          {tiles.map((tile) => (
            <Col key={tile.title} xs={24} sm={12} md={8} lg={6}>
              <Card>
                <Statistic title={tile.title} value={tile.value ?? 0} />
              </Card>
            </Col>
          ))}
        </Row>
      )}
    </div>
  );
}

function ReassignModal({
  projectId, batch, onClose,
}: {
  projectId: number;
  batch: Batch;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [taskType, setTaskType] = useState<"indexing" | "qa" | undefined>();
  const [shiftId, setShiftId] = useState<number | undefined>();
  const [agentId, setAgentId] = useState<number | undefined>();

  const { data: shifts = [] } = useQuery<Shift[]>({
    queryKey: ["project-shifts", projectId],
    queryFn: () => api.get(`/projects/${projectId}/shifts`).then((r) => r.data),
  });

  const { data: staff = [] } = useAvailableStaff(projectId, shiftId);
  const eligibleStaff = taskType
    ? staff.filter((s) => s.shift_role === REQUIRED_SHIFT_ROLE[taskType])
    : staff;

  const reassign = useMutation({
    mutationFn: () =>
      api.post(`/batches/${batch.id}/reassign`, { task_type: taskType, agent_id: agentId }),
    onSuccess: () => {
      message.success("Batch reassigned");
      qc.invalidateQueries({ queryKey: ["records-history-batches", projectId] });
      onClose();
    },
    onError: (e: unknown) => message.error(formatApiError(e, "Failed to reassign batch")),
  });

  return (
    <Modal
      title={`Reassign — ${batch.name}`}
      open
      onCancel={onClose}
      onOk={() => reassign.mutate()}
      okButtonProps={{ disabled: !taskType || !agentId, loading: reassign.isPending }}
      destroyOnClose
    >
      <Space direction="vertical" style={{ width: "100%" }}>
        <Select
          placeholder="Task type"
          style={{ width: "100%" }}
          options={[
            { label: "Indexing", value: "indexing" },
            { label: "QA", value: "qa" },
          ]}
          value={taskType}
          onChange={(v) => { setTaskType(v); setAgentId(undefined); }}
        />
        <Select
          placeholder="Shift"
          style={{ width: "100%" }}
          options={shifts.map((s) => ({ label: s.name, value: s.id }))}
          value={shiftId}
          onChange={(v) => { setShiftId(v); setAgentId(undefined); }}
        />
        <Select
          placeholder="Agent"
          style={{ width: "100%" }}
          disabled={!shiftId || !taskType}
          options={eligibleStaff.map((s) => ({ label: s.full_name, value: s.id }))}
          value={agentId}
          onChange={setAgentId}
        />
      </Space>
    </Modal>
  );
}

function BatchDetailDrawer({
  batch, onClose,
}: {
  batch: Batch;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const [recordId, setRecordId] = useState<number | null>(null);

  const { data: records = [], isLoading } = useQuery<DocRecord[]>({
    queryKey: ["batch-detail-records", batch.id],
    queryFn: () => api.get(`/batches/${batch.id}/records`).then((r) => r.data),
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

  const unlockMutation = useMutation({
    mutationFn: (id: number) => api.post(`/records/${id}/unlock`),
    onSuccess: () => {
      message.success("Record unlocked");
      qc.invalidateQueries({ queryKey: ["batch-detail-records", batch.id] });
    },
    onError: (e: unknown) => message.error(formatApiError(e, "Failed to unlock record")),
  });

  const columns: ColumnsType<DocRecord> = [
    { title: "ID", dataIndex: "id", width: 70 },
    {
      title: "Filename",
      dataIndex: "original_filename",
      render: (v: string | null) => v ?? "—",
    },
    { title: "Status", dataIndex: "status", render: (s: string) => <Tag>{s.replace(/_/g, " ")}</Tag> },
    {
      title: "Locked By",
      dataIndex: "locked_by",
      render: (v: number | null) => v ?? "—",
    },
    {
      title: "",
      key: "actions",
      render: (_: unknown, record: DocRecord) => (
        <Space>
          <Button size="small" onClick={() => setRecordId(record.id)}>
            View
          </Button>
          {record.locked_by != null && (
            <Button
              size="small"
              danger
              loading={unlockMutation.isPending && unlockMutation.variables === record.id}
              onClick={() => unlockMutation.mutate(record.id)}
            >
              Force Unlock
            </Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <Drawer title={`Batch — ${batch.name}`} open width={640} onClose={onClose}>
      <Table
        rowKey="id"
        size="small"
        loading={isLoading}
        columns={columns}
        dataSource={records}
        pagination={{ pageSize: 10 }}
      />

      {recordId && (
        <Card size="small" style={{ marginTop: 16 }} title={`Record #${recordId}`}>
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
    </Drawer>
  );
}

function HistoryTab({ projectId }: { projectId: number }) {
  const [range, setRange] = useState<DateRange>(null);
  const [status, setStatus] = useState<string | undefined>();
  const [reassignTarget, setReassignTarget] = useState<Batch | null>(null);
  const [detailTarget, setDetailTarget] = useState<Batch | null>(null);
  const { date_from, date_to } = toDateParams(range);

  const { data: batches = [], isLoading } = useQuery<Batch[]>({
    queryKey: ["records-history-batches", projectId, status, date_from, date_to],
    queryFn: () =>
      api
        .get(`/projects/${projectId}/batches`, { params: { status, date_from, date_to } })
        .then((r) => r.data),
  });

  const totalRecords = useMemo(
    () => batches.reduce((sum, b) => sum + (b.record_count ?? 0), 0),
    [batches]
  );

  const columns: ColumnsType<Batch> = [
    { title: "Name", dataIndex: "name" },
    {
      title: "Status",
      dataIndex: "status",
      render: (s: string) => <Tag color={BATCH_STATUS_COLOR[s] ?? "default"}>{s.replace(/_/g, " ")}</Tag>,
    },
    { title: "Records", dataIndex: "record_count", render: (v: number | null) => v ?? "—" },
    {
      title: "Completed",
      dataIndex: "completed_at",
      render: (v: string | null) => (v ? dayjs(v).format("YYYY-MM-DD HH:mm") : "—"),
    },
    {
      title: "Indexer",
      dataIndex: "indexer_name",
      render: (v: string | null) => v ?? "Unassigned",
    },
    {
      title: "",
      key: "actions",
      render: (_: unknown, batch: Batch) => (
        <Space>
          <Button size="small" onClick={() => setReassignTarget(batch)}>
            Reassign
          </Button>
          <Button size="small" onClick={() => setDetailTarget(batch)}>
            View Details
          </Button>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <Space style={{ marginBottom: 12 }} wrap>
        <RangePicker value={range as never} onChange={(v) => setRange(v as DateRange)} allowClear />
        <Select
          placeholder="Status"
          allowClear
          style={{ width: 160 }}
          options={BATCH_STATUS_OPTIONS.map((s) => ({ label: s.replace(/_/g, " "), value: s }))}
          value={status}
          onChange={setStatus}
        />
      </Space>

      <Typography.Text type="secondary" style={{ display: "block", marginBottom: 8 }}>
        {batches.length} batch{batches.length === 1 ? "" : "es"}, {totalRecords} record{totalRecords === 1 ? "" : "s"}
      </Typography.Text>

      {batches.length === 0 && !isLoading ? (
        <Empty description="No batches match the current filter" />
      ) : (
        <Table
          rowKey="id"
          columns={columns}
          dataSource={batches}
          loading={isLoading}
          size="small"
          pagination={{ pageSize: 20 }}
        />
      )}

      {reassignTarget && (
        <ReassignModal
          projectId={projectId}
          batch={reassignTarget}
          onClose={() => setReassignTarget(null)}
        />
      )}
      {detailTarget && (
        <BatchDetailDrawer batch={detailTarget} onClose={() => setDetailTarget(null)} />
      )}
    </div>
  );
}

export default function RecordsManagement({ projectId }: Props) {
  return (
    <div>
      <PageHeader title="Records Management" />
      <Card>
        <Tabs
          items={[
            {
              key: "dashboard",
              label: "Dashboard",
              children: <DashboardTab projectId={projectId} />,
            },
            {
              key: "history",
              label: "History",
              children: <HistoryTab projectId={projectId} />,
            },
          ]}
        />
      </Card>
    </div>
  );
}
