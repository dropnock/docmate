import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  List, Button, Typography, Spin, Empty, Badge, message,
} from "antd";
import { ArrowLeft } from "lucide-react";
import api from "@shared/api/client";
import AgentWorkspace from "@shared/components/AgentWorkspace";
import StatusDot from "@shared/components/StatusDot";
import type { Task } from "@shared/types";

const STATUS_LABEL: Record<string, string> = {
  pending: "Pending",
  in_progress: "In Progress",
  completed: "Completed",
  failed: "Failed",
  stale: "Stale",
};

const TASK_TYPE_LABEL: Record<string, string> = {
  indexing: "Indexing",
  qa: "Quality Check",
  qc: "Quality Control",
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
      <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
        <div
          style={{
            padding: "6px 16px",
            borderBottom: "1px solid #E2E8F0",
            background: "#FFFFFF",
            flexShrink: 0,
          }}
        >
          <Button
            icon={<ArrowLeft size={16} />}
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
            key={activeTask.id}
            task={activeTask}
            onComplete={async () => {
              // Auto-advance to the next task in the same batch, so the
              // indexer/QA reviewer never has to click back into the list
              // between records — falls back to the list only once nothing
              // in this batch is left for them.
              await qc.invalidateQueries({ queryKey: ["my-tasks"] });
              const freshTasks = qc.getQueryData<Task[]>(["my-tasks"]) ?? [];
              const next = freshTasks
                .filter((t) => t.batch_id === activeTask.batch_id && t.id !== activeTask.id)
                .sort((a, b) => a.id - b.id)[0];
              if (next) {
                setActiveTask(next);
              } else {
                message.success("Batch complete — nice work!");
                setActiveTask(null);
              }
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
                  {task.status === "in_progress" ? "Resume" : "Start"}
                </Button>,
              ]}
            >
              <List.Item.Meta
                title={
                  <Typography.Text strong>
                    Record #{task.record_id}
                    <Badge
                      count={TASK_TYPE_LABEL[task.task_type] ?? task.task_type}
                      style={{ background: "#1E40AF", marginLeft: 8 }}
                    />
                  </Typography.Text>
                }
                description={
                  <Typography.Text type="secondary">
                    Batch #{task.batch_id} ·{" "}
                    <StatusDot
                      filled={task.status === "completed"}
                      label={STATUS_LABEL[task.status] ?? task.status}
                    />
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
