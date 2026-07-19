import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  List, Button, Typography, Spin, Empty, Badge, Tooltip, message,
} from "antd";
import { ArrowLeft, CheckCircle2 } from "lucide-react";
import api from "@shared/api/client";
import { formatApiError } from "@shared/api/errors";
import AgentWorkspace from "@shared/components/AgentWorkspace";
import StatusDot from "@shared/components/StatusDot";
import type { DocRecord, Task } from "@shared/types";

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

const RECORD_STATUS_LABEL: Record<string, string> = {
  pending: "Pending",
  indexing: "Indexing",
  indexed: "Indexed",
  withdrawn: "Withdrawn",
  ineligible: "Ineligible",
  excluded: "Excluded",
};

// A record in one of these statuses needs no further indexing work — it's
// what Complete Batch requires of every record before it'll submit (mirrors
// backend/app/models/record.py's RecordStatus.indexed + SKIPPED_RECORD_STATUSES).
const RECORD_DONE_STATUSES = new Set(["indexed", "withdrawn", "ineligible", "excluded"]);

// An indexing task belongs to the still-open batch worklist (grouped view,
// reopenable, gated by an explicit Complete Batch) only while its batch is
// still in the indexing phase — see GET /tasks/mine, which is the only
// place batch_status gets populated. Everything else (QA, QC, and a rework
// indexing task created after a QA rejection, whose batch has already moved
// past indexing) uses the plain flat list, unchanged.
function isOpenIndexingTask(task: Task): boolean {
  return task.task_type === "indexing" && task.batch_status === "indexing";
}

function BatchDetail({
  batchId,
  tasks,
  onBack,
  onOpenRecord,
}: {
  batchId: number;
  tasks: Task[];
  onBack: () => void;
  onOpenRecord: (task: Task) => void;
}) {
  const qc = useQueryClient();

  const { data: records = [], isLoading } = useQuery<DocRecord[]>({
    queryKey: ["records", batchId],
    queryFn: () => api.get(`/batches/${batchId}/records`).then((r) => r.data),
    refetchInterval: 10_000,
  });

  const taskByRecordId = new Map(tasks.map((t) => [t.record_id, t]));
  const remaining = records.filter((r) => !RECORD_DONE_STATUSES.has(r.status)).length;

  const [completing, setCompleting] = useState(false);
  const completeBatch = async () => {
    setCompleting(true);
    try {
      await api.post(`/batches/${batchId}/complete-indexing`);
      message.success("Batch completed — sent for QA");
      await qc.invalidateQueries({ queryKey: ["my-tasks"] });
      onBack();
    } catch (e) {
      message.error(formatApiError(e, "Failed to complete batch"));
    } finally {
      setCompleting(false);
    }
  };

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <Button icon={<ArrowLeft size={16} />} onClick={onBack}>
          Back to tasks
        </Button>
        <Tooltip title={remaining > 0 ? `${remaining} record(s) still need to be indexed, withdrawn, ineligible, or excluded` : undefined}>
          <Button
            type="primary"
            icon={<CheckCircle2 size={16} />}
            disabled={remaining > 0}
            loading={completing}
            onClick={completeBatch}
          >
            Complete Batch
          </Button>
        </Tooltip>
      </div>

      {isLoading ? (
        <Spin />
      ) : (
        <List
          dataSource={records}
          rowKey="id"
          renderItem={(record) => {
            const task = taskByRecordId.get(record.id);
            return (
              <List.Item
                actions={[
                  <Button
                    type="primary"
                    disabled={!task}
                    onClick={() => task && onOpenRecord(task)}
                  >
                    {RECORD_DONE_STATUSES.has(record.status) ? "Review / Correct" : "Open"}
                  </Button>,
                ]}
              >
                <List.Item.Meta
                  title={
                    <Typography.Text strong>
                      {record.original_filename ?? `Record #${record.id}`}
                    </Typography.Text>
                  }
                  description={
                    <StatusDot
                      filled={RECORD_DONE_STATUSES.has(record.status)}
                      label={RECORD_STATUS_LABEL[record.status] ?? record.status}
                    />
                  }
                />
              </List.Item>
            );
          }}
        />
      )}
    </div>
  );
}

export default function MyTasks() {
  const qc = useQueryClient();
  const [activeTask, setActiveTask] = useState<Task | null>(null);
  const [selectedBatchId, setSelectedBatchId] = useState<number | null>(null);

  const { data: tasks = [], isLoading } = useQuery<Task[]>({
    queryKey: ["my-tasks"],
    queryFn: () => api.get("/tasks/mine").then((r) => r.data),
    refetchInterval: 30_000,
  });

  const indexingBatchTasks = tasks.filter(isOpenIndexingTask);
  const otherTasks = tasks.filter((t) => !isOpenIndexingTask(t));

  const batchGroups = new Map<number, Task[]>();
  for (const t of indexingBatchTasks) {
    const group = batchGroups.get(t.batch_id) ?? [];
    group.push(t);
    batchGroups.set(t.batch_id, group);
  }

  // ─── Level 3: the record workspace ──────────────────────────────────────
  if (activeTask) {
    const inBatchWorklist = isOpenIndexingTask(activeTask);
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
            onClick={async () => {
              await qc.invalidateQueries({ queryKey: ["my-tasks"] });
              setActiveTask(null);
              if (!inBatchWorklist) setSelectedBatchId(null);
            }}
          >
            {inBatchWorklist ? "Back to batch" : "Back to tasks"}
          </Button>
        </div>
        <div style={{ flex: 1, overflow: "hidden" }}>
          <AgentWorkspace
            key={activeTask.id}
            task={activeTask}
            onComplete={async () => {
              // Auto-advance to the next unfinished record, so the indexer/
              // QA reviewer never has to click back into the list between
              // records. What "unfinished" means, and where you land once
              // nothing's left, differs by flow: an open indexing batch
              // never disappears out from under you (every record stays
              // listed until Complete Batch is pressed), so falling back
              // means returning to that batch's record list, not exiting.
              await qc.invalidateQueries({ queryKey: ["my-tasks"] });
              const freshTasks = qc.getQueryData<Task[]>(["my-tasks"]) ?? [];
              const next = freshTasks
                .filter((t) =>
                  t.batch_id === activeTask.batch_id &&
                  t.id !== activeTask.id &&
                  t.status !== "completed"
                )
                .sort((a, b) => a.id - b.id)[0];
              if (next) {
                setActiveTask(next);
              } else if (inBatchWorklist) {
                message.info("All records done — review and press Complete Batch when ready.");
                setSelectedBatchId(activeTask.batch_id);
                setActiveTask(null);
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

  // ─── Level 2: a single open indexing batch's records ────────────────────
  if (selectedBatchId != null) {
    return (
      <div>
        <Typography.Title level={4}>My Tasks</Typography.Title>
        <BatchDetail
          batchId={selectedBatchId}
          tasks={batchGroups.get(selectedBatchId) ?? []}
          onBack={() => setSelectedBatchId(null)}
          onOpenRecord={setActiveTask}
        />
      </div>
    );
  }

  // ─── Level 1: batch cards (indexing) + flat list (everything else) ─────
  return (
    <div>
      <Typography.Title level={4}>My Tasks</Typography.Title>

      {isLoading ? (
        <Spin />
      ) : tasks.length === 0 ? (
        <Empty description="No pending tasks assigned to you" />
      ) : (
        <>
          {batchGroups.size > 0 && (
            <List
              header={<Typography.Text type="secondary">Indexing batches</Typography.Text>}
              dataSource={[...batchGroups.entries()]}
              rowKey={([batchId]) => batchId}
              style={{ marginBottom: otherTasks.length > 0 ? 24 : 0 }}
              renderItem={([batchId, batchTasks]) => {
                const done = batchTasks.filter((t) => t.status === "completed").length;
                return (
                  <List.Item
                    actions={[
                      <Button type="primary" onClick={() => setSelectedBatchId(batchId)}>
                        Continue
                      </Button>,
                    ]}
                  >
                    <List.Item.Meta
                      title={<Typography.Text strong>Batch #{batchId}</Typography.Text>}
                      description={
                        <Typography.Text type="secondary">
                          {done} of {batchTasks.length} record{batchTasks.length === 1 ? "" : "s"} done
                        </Typography.Text>
                      }
                    />
                  </List.Item>
                );
              }}
            />
          )}

          {otherTasks.length > 0 && (
            <List
              header={batchGroups.size > 0 ? <Typography.Text type="secondary">Other tasks</Typography.Text> : undefined}
              dataSource={otherTasks}
              rowKey="id"
              renderItem={(task) => (
                <List.Item
                  actions={[
                    <Button type="primary" onClick={() => setActiveTask(task)}>
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
        </>
      )}
    </div>
  );
}
