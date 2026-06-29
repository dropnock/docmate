import {
  Badge,
  Button,
  Card,
  Col,
  Empty,
  Input,
  Row,
  Select,
  Spin,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnType } from "antd/es/table";
import api from "@shared/api/client";
import type { AvailableStaff, Batch, DocRecord, Shift } from "@shared/types";

interface Props {
  projectId: number;
}

const STATUS_COLOR: Record<string, string> = {
  pending: "default",
  indexing: "processing",
  indexed: "success",
  qa_pending: "warning",
  qa_passed: "success",
  qa_failed: "error",
  qc_pending: "warning",
  qc_passed: "success",
  qc_failed: "error",
};

export default function TaskAssignment({ projectId }: Props) {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [selectedBatch, setSelectedBatch] = useState<number | undefined>();
  const [selectedShift, setSelectedShift] = useState<number | undefined>();
  const [assignments, setAssignments] = useState<Record<number, number>>({});

  const { data: batches, isLoading: batchLoading } = useQuery<Batch[]>({
    queryKey: ["batches", projectId],
    queryFn: () =>
      api.get(`/projects/${projectId}/batches`).then((r) => r.data),
  });

  const { data: shifts } = useQuery<Shift[]>({
    queryKey: ["project-shifts", projectId],
    queryFn: () => api.get(`/projects/${projectId}/shifts`).then((r) => r.data),
  });

  const { data: records, isLoading: recLoading } = useQuery<DocRecord[]>({
    queryKey: ["records", selectedBatch],
    queryFn: () =>
      api.get(`/batches/${selectedBatch}/records`).then((r) => r.data),
    enabled: !!selectedBatch,
  });

  const { data: staff } = useQuery<AvailableStaff[]>({
    queryKey: ["available-staff", projectId, selectedShift],
    queryFn: () =>
      api
        .get(`/projects/${projectId}/available-staff`, {
          params: { shift_id: selectedShift },
        })
        .then((r) => r.data),
    enabled: !!selectedShift,
  });

  const assignMutation = useMutation({
    mutationFn: ({
      recordId,
      agentId,
    }: {
      recordId: number;
      agentId: number;
    }) =>
      api.post("/tasks/assign", {
        record_id: recordId,
        batch_id: selectedBatch,
        task_type: "indexing",
        agent_id: agentId,
      }),
    onSuccess: (_, { recordId }) => {
      message.success(`Record #${recordId} assigned`);
      qc.invalidateQueries({ queryKey: ["records", selectedBatch] });
      qc.invalidateQueries({ queryKey: ["batch-tasks", selectedBatch] });
    },
    onError: () => message.error("Assignment failed"),
  });

  const assignAllMutation = useMutation({
    mutationFn: async () => {
      if (!records || !selectedShift) return;
      const unassigned = records.filter((r) => r.status === "pending");
      const staffList = staff ?? [];
      if (!staffList.length) throw new Error("No staff available");
      await Promise.all(
        unassigned.map((rec, i) =>
          api.post("/tasks/assign", {
            record_id: rec.id,
            batch_id: selectedBatch,
            task_type: "indexing",
            agent_id: staffList[i % staffList.length].id,
          })
        )
      );
    },
    onSuccess: () => {
      message.success("All pending records assigned (round-robin)");
      qc.invalidateQueries({ queryKey: ["records", selectedBatch] });
    },
    onError: (e: Error) => message.error(e.message || "Bulk assign failed"),
  });

  const columns: ColumnType<DocRecord>[] = [
    { title: "Record ID", dataIndex: "id", width: 100 },
    {
      title: "File",
      dataIndex: "file_reference",
      render: (v: string | null) =>
        v ? (
          <Typography.Text ellipsis style={{ maxWidth: 200 }}>
            {v.split("/").pop()}
          </Typography.Text>
        ) : (
          <Tag color="default">No file</Tag>
        ),
    },
    {
      title: "Status",
      dataIndex: "status",
      render: (s: string) => (
        <Tag color={STATUS_COLOR[s] ?? "default"}>{s.replace(/_/g, " ")}</Tag>
      ),
    },
    {
      title: "Version",
      dataIndex: "current_version",
      render: (v: number) =>
        v > 1 ? <Badge count={`v${v}`} style={{ background: "#faad14" }} /> : `v${v}`,
    },
    {
      title: "Lock",
      dataIndex: "locked_by",
      render: (uid: number | null) =>
        uid ? <Tag color="orange">Locked</Tag> : <Tag color="green">Free</Tag>,
    },
    {
      title: "Assign To",
      key: "assign",
      render: (_: unknown, rec: DocRecord) => (
        <div style={{ display: "flex", gap: 8 }}>
          <Select
            placeholder="Choose agent"
            size="small"
            style={{ width: 180 }}
            disabled={!selectedShift || !staff?.length}
            value={assignments[rec.id]}
            options={staff?.map((s) => ({ label: s.full_name, value: s.id }))}
            onChange={(agentId) =>
              setAssignments((prev) => ({ ...prev, [rec.id]: agentId }))
            }
          />
          <Button
            size="small"
            type="primary"
            disabled={!assignments[rec.id]}
            loading={assignMutation.isPending}
            onClick={() =>
              assignMutation.mutate({
                recordId: rec.id,
                agentId: assignments[rec.id],
              })
            }
          >
            Assign
          </Button>
        </div>
      ),
    },
  ];

  const filteredRecords = (records ?? []).filter((r) => {
    const q = search.toLowerCase();
    return (
      r.status.toLowerCase().includes(q) ||
      (r.file_reference?.split("/").pop() ?? "").toLowerCase().includes(q) ||
      String(r.id).includes(q)
    );
  });

  const pendingCount = records?.filter((r) => r.status === "pending").length ?? 0;

  return (
    <div>
      <Typography.Title level={4}>Task Assignment</Typography.Title>

      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col>
          <Select
            placeholder="Select batch"
            style={{ width: 220 }}
            loading={batchLoading}
            options={batches?.map((b) => ({
              label: `${b.name} (${b.status})`,
              value: b.id,
            }))}
            onChange={setSelectedBatch}
          />
        </Col>
        <Col>
          <Select
            placeholder="Select shift"
            style={{ width: 180 }}
            options={shifts?.map((s) => ({ label: s.name, value: s.id }))}
            onChange={setSelectedShift}
          />
        </Col>
        {pendingCount > 0 && staff?.length ? (
          <Col>
            <Button
              onClick={() => assignAllMutation.mutate()}
              loading={assignAllMutation.isPending}
            >
              Auto-assign {pendingCount} pending (round-robin)
            </Button>
          </Col>
        ) : null}
      </Row>

      {!selectedBatch ? (
        <Empty description="Select a batch to view records" />
      ) : recLoading ? (
        <Spin />
      ) : (
        <Card>
          <Input.Search
            placeholder="Search by record ID, status or file name…"
            allowClear
            onChange={(e) => setSearch(e.target.value)}
            style={{ marginBottom: 16, maxWidth: 400 }}
          />
          <Table
            rowKey="id"
            columns={columns}
            dataSource={filteredRecords}
            size="middle"
            pagination={{ pageSize: 50 }}
            summary={() => (
              <Table.Summary>
                <Table.Summary.Row>
                  <Table.Summary.Cell index={0} colSpan={6}>
                    <Typography.Text type="secondary">
                      {filteredRecords.length} of {records?.length ?? 0} records · {pendingCount} unassigned
                    </Typography.Text>
                  </Table.Summary.Cell>
                </Table.Summary.Row>
              </Table.Summary>
            )}
          />
        </Card>
      )}
    </div>
  );
}
