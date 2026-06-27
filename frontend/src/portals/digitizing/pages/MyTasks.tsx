import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  List, Button, Tag, Typography, Spin, Empty, Badge,
} from "antd";
import { ArrowLeftOutlined } from "@ant-design/icons";
import api from "@shared/api/client";
import AgentWorkspace from "@shared/components/AgentWorkspace";
import type { Task } from "@shared/types";

const STATUS_COLOR: Record<string, string> = {
  pending: "default",
  in_progress: "processing",
  completed: "success",
  failed: "error",
  stale: "warning",
};

export default function MyTasks() {
  const qc = useQueryClient();
  const [activeTask, setActiveTask] = useState<Task | null>(null);

  const { data: tasks = [], isLoading } = useQuery<Task[]>({
    queryKey: ["my-tasks"],
    queryFn: () => api.get("/tasks/mine").then((r) => r.data),
    refetchInterval: 30_000,
  });

  if (activeTask) {
    return (
      <div style={{ height: "calc(100vh - 64px)", display: "flex", flexDirection: "column" }}>
        <div style={{ padding: "8px 16px", borderBottom: "1px solid #f0f0f0", background: "#fff" }}>
          <Button
            icon={<ArrowLeftOutlined />}
            onClick={() => {
              setActiveTask(null);
              qc.invalidateQueries({ queryKey: ["my-tasks"] });
            }}
          >
            Back to tasks
          </Button>
        </div>
        <div style={{ flex: 1, overflow: "hidden" }}>
          <AgentWorkspace
            task={activeTask}
            onComplete={() => {
              setActiveTask(null);
              qc.invalidateQueries({ queryKey: ["my-tasks"] });
            }}
          />
        </div>
      </div>
    );
  }

  return (
    <div>
      <Typography.Title level={4}>My Tasks</Typography.Title>

      {isLoading ? (
        <Spin />
      ) : tasks.length === 0 ? (
        <Empty description="No pending tasks assigned to you" />
      ) : (
        <List
          dataSource={tasks}
          rowKey="id"
          renderItem={(task) => (
            <List.Item
              actions={[
                <Button
                  type="primary"
                  onClick={() => setActiveTask(task)}
                >
                  {task.status === "in_progress" ? "Resume" : "Open"}
                </Button>,
              ]}
            >
              <List.Item.Meta
                title={
                  <Typography.Text strong>
                    Record #{task.record_id}
                    <Badge
                      count={task.task_type.toUpperCase()}
                      style={{ background: "#108ee9", marginLeft: 8 }}
                    />
                  </Typography.Text>
                }
                description={
                  <Typography.Text type="secondary">
                    Batch #{task.batch_id} ·{" "}
                    <Tag color={STATUS_COLOR[task.status] ?? "default"}>{task.status}</Tag>
                    {task.due_at && ` · Due ${new Date(task.due_at).toLocaleString()}`}
                  </Typography.Text>
                }
              />
            </List.Item>
          )}
        />
      )}
    </div>
  );
}
