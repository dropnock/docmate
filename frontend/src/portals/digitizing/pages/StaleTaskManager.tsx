import { Button, Checkbox, Input, Select, Table, Tag, Typography, message } from "antd";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { AxiosError } from "axios";
import api from "@shared/api/client";
import { useAvailableStaff } from "@shared/hooks/useAvailableStaff";
import type { Task } from "@shared/types";

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
  const [unlockRecordId, setUnlockRecordId] = useState("");

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
      setUnlockRecordId("");
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
          yet has no row here to attach a "Force Unlock" button to. This
          reaches any record directly, regardless of task staleness. */}
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 16 }}>
        <Input
          placeholder="Record ID"
          value={unlockRecordId}
          onChange={(e) => setUnlockRecordId(e.target.value)}
          style={{ width: 140 }}
        />
        <Button
          danger
          disabled={!unlockRecordId.trim()}
          loading={unlockMutation.isPending && unlockMutation.variables === Number(unlockRecordId)}
          onClick={() => unlockMutation.mutate(Number(unlockRecordId))}
        >
          Force Unlock Record
        </Button>
      </div>

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
