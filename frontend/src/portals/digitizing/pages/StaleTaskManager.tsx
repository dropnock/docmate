import { Button, Checkbox, Select, Table, Tag, Typography, message } from "antd";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@shared/api/client";
import type { AvailableStaff, Task } from "@shared/types";

interface Props { projectId: number; shiftId?: number }

export default function StaleTaskManager({ projectId, shiftId }: Props) {
  const qc = useQueryClient();
  const [selected, setSelected] = useState<number[]>([]);
  const [targetAgent, setTargetAgent] = useState<number | undefined>();

  const { data: staleTasks, isLoading } = useQuery<Task[]>({
    queryKey: ["stale-tasks", projectId],
    queryFn: () => api.get("/tasks/stale", { params: { project_id: projectId } }).then((r) => r.data),
    refetchInterval: 30_000,
  });

  const { data: staff } = useQuery<AvailableStaff[]>({
    queryKey: ["available-staff", projectId, shiftId],
    queryFn: () =>
      api
        .get(`/projects/${projectId}/available-staff`, { params: { shift_id: shiftId ?? 0 } })
        .then((r) => r.data),
    enabled: !!shiftId,
  });

  const bulkMutation = useMutation({
    mutationFn: () =>
      api.post("/tasks/bulk-reassign", { task_ids: selected, agent_id: targetAgent }),
    onSuccess: () => {
      message.success(`Reassigned ${selected.length} tasks`);
      setSelected([]);
      qc.invalidateQueries({ queryKey: ["stale-tasks"] });
    },
  });

  const singleMutation = useMutation({
    mutationFn: ({ taskId, agentId }: { taskId: number; agentId: number }) =>
      api.patch(`/tasks/${taskId}/reassign`, null, { params: { agent_id: agentId } }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["stale-tasks"] });
      message.success("Reassigned");
    },
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
    { title: "Status", dataIndex: "status", render: (s: string) => <Tag color="warning">{s}</Tag> },
    { title: "Due", dataIndex: "due_at", render: (d: string) => d?.slice(0, 19) },
    {
      title: "Reassign",
      key: "action",
      render: (_: unknown, t: Task) => (
        <Select
          placeholder="Assign to..."
          size="small"
          style={{ width: 160 }}
          options={staff?.map((s) => ({ label: s.full_name, value: s.id }))}
          onChange={(agentId) => singleMutation.mutate({ taskId: t.id, agentId })}
        />
      ),
    },
  ];

  return (
    <div>
      <Typography.Title level={4}>Stale Tasks</Typography.Title>
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
      <Table rowKey="id" columns={columns} dataSource={staleTasks} loading={isLoading} size="small" />
    </div>
  );
}
