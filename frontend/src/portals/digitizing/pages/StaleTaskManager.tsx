import { Button, Checkbox, Empty, Input, Select, Table, Tag, Typography, message } from "antd";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { AxiosError } from "axios";
import api from "@shared/api/client";
import { useAvailableStaff } from "@shared/hooks/useAvailableStaff";
import type { DocRecord, Task } from "@shared/types";

const REQUIRED_SHIFT_ROLE: Record<string, "indexer" | "qa" | undefined> = {
  indexing: "indexer",
  qa: "qa",
};

interface Props { projectId: number; shiftId?: number }

export default function StaleTaskManager({ projectId, shiftId }: Props) {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<number[]>([]);
  const [targetAgent, setTargetAgent] = useState<number | undefined>();
  const [batchLookupInput, setBatchLookupInput] = useState("");
  const [lookupBatchId, setLookupBatchId] = useState<number | undefined>();

  // Record IDs aren't shown anywhere in the UI, so supervisors have no way
  // to target a "Force Unlock" by record ID directly — batch IDs are
  // visible (Cabinet Assignment's Batches table), so look up locked
  // records by batch instead.
  const { data: batchRecords, isLoading: batchRecordsLoading, isError: batchLookupFailed } = useQuery<DocRecord[]>({
    queryKey: ["batch-records-for-unlock", lookupBatchId],
    queryFn: () => api.get(`/batches/${lookupBatchId}/records`).then((r) => r.data),
    enabled: lookupBatchId != null,
  });
  const lockedRecords = (batchRecords ?? []).filter((r) => r.locked_by != null);

  const { data: staleTasks, isLoading } = useQuery<Task[]>({
    queryKey: ["stale-tasks", projectId],
    queryFn: () => api.get("/tasks/stale", { params: { project_id: projectId } }).then((r) => r.data),
    refetchInterval: 30_000,
  });

  const { data: staff } = useAvailableStaff(projectId, shiftId);

  const bulkMutation = useMutation({
    mutationFn: () =>
      api.post("/tasks/bulk-reassign", { task_ids: selected, agent_id: targetAgent }),
    onSuccess: () => {
      message.success(`Reassigned ${selected.length} tasks`);
      setSelected([]);
      qc.invalidateQueries({ queryKey: ["stale-tasks"] });
    },
    onError: (e: AxiosError<{ detail: string }>) =>
      message.error(e.response?.data?.detail ?? "Failed to reassign tasks"),
  });

  const singleMutation = useMutation({
    mutationFn: ({ taskId, agentId }: { taskId: number; agentId: number }) =>
      api.patch(`/tasks/${taskId}/reassign`, null, { params: { agent_id: agentId } }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["stale-tasks"] });
      message.success("Reassigned");
    },
    onError: (e: AxiosError<{ detail: string }>) =>
      message.error(e.response?.data?.detail ?? "Failed to reassign task"),
  });

  // Reassigning only releases a lock when it's still held by the task's own
  // assignee (task_service.reassign_task) — a lock left behind by anything
  // else (crash, race, manual fix) has no task to reassign it away from, so
  // this hits the record directly regardless of task/lock state.
  const unlockMutation = useMutation({
    mutationFn: (recordId: number) => api.post(`/records/${recordId}/unlock`),
    onSuccess: () => {
      message.success("Record unlocked");
      qc.invalidateQueries({ queryKey: ["batch-records-for-unlock", lookupBatchId] });
    },
    onError: (e: AxiosError<{ detail: string }>) =>
      message.error(e.response?.data?.detail ?? "Failed to unlock record"),
  });

  const columns = [
    {
      title: "",
      key: "check",
      render: (_: unknown, t: Task) => (
        <Checkbox
          checked={selected.includes(t.id)}
          onChange={(e) =>
            setSelected((prev) =>
              e.target.checked ? [...prev, t.id] : prev.filter((id) => id !== t.id)
            )
          }
        />
      ),
    },
    { title: "Task ID", dataIndex: "id" },
    { title: "Record", dataIndex: "record_id" },
    { title: "Type", dataIndex: "task_type", render: (t: string) => <Tag>{t}</Tag> },
    { title: "Status", dataIndex: "status", render: (s: string) => <Tag>{s}</Tag> },
    { title: "Due", dataIndex: "due_at", render: (d: string) => d?.slice(0, 19) },
    {
      title: "Reassign",
      key: "action",
      render: (_: unknown, t: Task) => {
        const requiredRole = REQUIRED_SHIFT_ROLE[t.task_type];
        const eligible = requiredRole
          ? staff?.filter((s) => s.shift_role === requiredRole)
          : staff;
        return (
          <Select
            placeholder="Assign to..."
            size="small"
            style={{ width: 160 }}
            options={eligible?.map((s) => ({ label: s.full_name, value: s.id }))}
            onChange={(agentId) => singleMutation.mutate({ taskId: t.id, agentId })}
          />
        );
      },
    },
    {
      title: "Lock",
      key: "unlock",
      render: (_: unknown, t: Task) => (
        <Button
          size="small"
          danger
          loading={unlockMutation.isPending && unlockMutation.variables === t.record_id}
          onClick={() => unlockMutation.mutate(t.record_id)}
        >
          Force Unlock
        </Button>
      ),
    },
  ];

  const q = search.toLowerCase();
  const filteredTasks = (staleTasks ?? []).filter((t) =>
    String(t.id).includes(q) ||
    String(t.record_id).includes(q) ||
    t.task_type.toLowerCase().includes(q) ||
    t.status.toLowerCase().includes(q)
  );

  return (
    <div>
      <Typography.Title level={4}>Stale Tasks</Typography.Title>

      {/* The table below only lists tasks whose due_at has already passed
          (/tasks/stale) — a lock stuck on a record whose task isn't overdue
          yet has no row here. Look up locked records by batch instead,
          since batch IDs (unlike record IDs) are visible elsewhere in the
          UI (Cabinet Assignment's Batches table). */}
      <Typography.Title level={5}>Find Locked Records by Batch</Typography.Title>
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12 }}>
        <Input
          placeholder="Batch ID"
          value={batchLookupInput}
          onChange={(e) => setBatchLookupInput(e.target.value)}
          onPressEnter={() => setLookupBatchId(Number(batchLookupInput))}
          style={{ width: 140 }}
        />
        <Button
          disabled={!batchLookupInput.trim()}
          loading={batchRecordsLoading}
          onClick={() => setLookupBatchId(Number(batchLookupInput))}
        >
          Find Locked Records
        </Button>
      </div>
      {lookupBatchId != null && !batchRecordsLoading && (
        batchLookupFailed ? (
          <Typography.Text type="danger">Batch {lookupBatchId} not found.</Typography.Text>
        ) : lockedRecords.length === 0 ? (
          <Empty description={`No locked records in batch ${lookupBatchId}`} style={{ marginBottom: 16 }} />
        ) : (
          <Table
            rowKey="id"
            size="small"
            dataSource={lockedRecords}
            pagination={false}
            style={{ marginBottom: 24 }}
            columns={[
              { title: "Record ID", dataIndex: "id" },
              {
                title: "Filename",
                dataIndex: "original_filename",
                render: (v: string | null) => v ?? "—",
              },
              { title: "Locked By (user ID)", dataIndex: "locked_by" },
              {
                title: "Locked At",
                dataIndex: "locked_at",
                render: (d: string | null) => d?.slice(0, 19) ?? "—",
              },
              {
                title: "",
                key: "unlock",
                render: (_: unknown, r: DocRecord) => (
                  <Button
                    size="small"
                    danger
                    loading={unlockMutation.isPending && unlockMutation.variables === r.id}
                    onClick={() => unlockMutation.mutate(r.id)}
                  >
                    Force Unlock
                  </Button>
                ),
              },
            ]}
          />
        )
      )}

      <Input.Search
        placeholder="Search by task ID, record ID, type or status…"
        allowClear
        onChange={(e) => setSearch(e.target.value)}
        style={{ marginBottom: 12, maxWidth: 400 }}
      />
      <div style={{ display: "flex", gap: 12, marginBottom: 12 }}>
        <Select
          placeholder="Bulk assign to..."
          style={{ width: 200 }}
          options={staff?.map((s) => ({ label: s.full_name, value: s.id }))}
          onChange={setTargetAgent}
        />
        <Button
          type="primary"
          disabled={!selected.length || !targetAgent}
          onClick={() => bulkMutation.mutate()}
          loading={bulkMutation.isPending}
        >
          Bulk Reassign ({selected.length} selected)
        </Button>
        <Button onClick={() => setSelected(staleTasks?.map((t) => t.id) ?? [])}>Select All</Button>
        <Button onClick={() => setSelected([])}>Clear</Button>
      </div>
      <Table rowKey="id" columns={columns} dataSource={filteredTasks} loading={isLoading} size="small" />
    </div>
  );
}
