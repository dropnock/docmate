import {
  Alert,
  Badge,
  Button,
  Card,
  Empty,
  Form,
  Input,
  List,
  Modal,
  Spin,
  Typography,
  message,
} from "antd";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@shared/api/client";
import { formatApiError } from "@shared/api/errors";
import { useRecordImage } from "@shared/hooks/useRecordImage";
import type { DocRecord, Task } from "@shared/types";
import OpenSeadragonViewer from "@shared/components/ImageViewer/OpenSeadragonViewer";
import SplitWorkspace from "@shared/components/SplitWorkspace";
import StatusDot from "@shared/components/StatusDot";

export default function QCWorkspace() {
  const qc = useQueryClient();
  const [activeTask, setActiveTask] = useState<Task | null>(null);
  const [rejectModalOpen, setRejectModalOpen] = useState(false);
  const [rejectForm] = Form.useForm<{ reason: string }>();

  // Fetch tasks assigned to me
  const { data: tasks, isLoading: tasksLoading } = useQuery<Task[]>({
    queryKey: ["my-tasks"],
    queryFn: () => api.get("/tasks/mine").then((r) => r.data),
    refetchInterval: 30_000,
  });

  // Fetch image for active task — see useRecordImage for why this proxies
  // through the backend rather than using a presigned S3 URL.
  const {
    data: viewData,
    isLoading: viewLoading,
    page: imagePage,
    setPage: setImagePage,
    pageCount: imagePageCount,
  } = useRecordImage(activeTask?.record_id, !!activeTask && activeTask.status === "in_progress");

  // Fetch record data
  const { data: record } = useQuery<DocRecord>({
    queryKey: ["record", activeTask?.record_id],
    queryFn: () =>
      api.get(`/records/${activeTask!.record_id}`).then((r) => r.data),
    enabled: !!activeTask,
    refetchInterval: 10_000,
  });

  const startMutation = useMutation({
    mutationFn: (taskId: number) => api.post(`/tasks/${taskId}/start`),
    onSuccess: (res) => {
      setActiveTask(res.data);
      qc.invalidateQueries({ queryKey: ["my-tasks"] });
      qc.invalidateQueries({ queryKey: ["record", activeTask?.record_id] });
    },
    onError: (err: unknown) => message.error(formatApiError(err, "Could not start task")),
  });

  const passMutation = useMutation({
    mutationFn: (taskId: number) =>
      api.post(`/tasks/${taskId}/complete`, { indexed_data: null }),
    onSuccess: () => {
      message.success("Record passed QC");
      setActiveTask(null);
      qc.invalidateQueries({ queryKey: ["my-tasks"] });
    },
    onError: () => message.error("Failed to complete task"),
  });

  const rejectMutation = useMutation({
    mutationFn: ({ taskId, reason }: { taskId: number; reason: string }) =>
      api.post(`/tasks/${taskId}/fail`, { reason }),
    onSuccess: () => {
      message.success("Record rejected — sent back for rework");
      setRejectModalOpen(false);
      rejectForm.resetFields();
      setActiveTask(null);
      qc.invalidateQueries({ queryKey: ["my-tasks"] });
    },
    onError: (err: { response?: { data?: { detail?: string } } }) =>
      message.error(err.response?.data?.detail ?? "Rejection failed"),
  });

  const handleReject = async () => {
    const values = await rejectForm.validateFields();
    if (!activeTask) return;
    rejectMutation.mutate({ taskId: activeTask.id, reason: values.reason });
  };

  // Task list panel
  if (!activeTask) {
    return (
      <div>
        <Typography.Title level={4}>QC Task Queue</Typography.Title>
        {tasksLoading ? (
          <Spin />
        ) : !tasks?.length ? (
          <Empty description="No QC tasks assigned to you" />
        ) : (
          <List
            dataSource={tasks}
            rowKey="id"
            renderItem={(task) => (
              <List.Item
                actions={[
                  task.status === "pending" ? (
                    <Button
                      type="primary"
                      loading={startMutation.isPending}
                      onClick={() => {
                        setActiveTask(task);
                        startMutation.mutate(task.id);
                      }}
                    >
                      Start QC
                    </Button>
                  ) : (
                    <Button onClick={() => setActiveTask(task)}>Resume</Button>
                  ),
                ]}
              >
                <List.Item.Meta
                  title={`Record #${task.record_id}`}
                  description={
                    <>
                      Batch #{task.batch_id} ·{" "}
                      <StatusDot filled={task.status === "in_progress"} label={task.status} />
                      {task.due_at && (
                        <Typography.Text type="secondary" style={{ marginLeft: 8 }}>
                          Due: {new Date(task.due_at).toLocaleString()}
                        </Typography.Text>
                      )}
                    </>
                  }
                />
              </List.Item>
            )}
          />
        )}
      </div>
    );
  }

  // Split-screen QC view
  const isLockedByOther =
    record?.locked_by && record.locked_by !== activeTask.assigned_to;

  return (
    <div style={{ height: "calc(100vh - 64px)", display: "flex", flexDirection: "column" }}>
      {/* Banners */}
      {isLockedByOther && (
        <Alert
          type="error"
          message={`Record locked by user ID ${record?.locked_by}. Please contact your supervisor.`}
          banner
        />
      )}
      {activeTask.status === "in_progress" && !isLockedByOther && (
        <Alert type="info" message="Record locked by you for QC review." banner />
      )}
      {record && record.current_version > 1 && (
        <Alert
          type="warning"
          message={`This is version ${record.current_version} — previously rejected and re-indexed.`}
          banner
        />
      )}

      {/* Nav bar */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          padding: "8px 16px",
          borderBottom: "1px solid #E2E8F0",
          background: "#FFFFFF",
        }}
      >
        <Button size="small" onClick={() => setActiveTask(null)}>
          ← Back to queue
        </Button>
        <Typography.Text strong>
          Record #{activeTask.record_id}
        </Typography.Text>
        <Badge
          count={`v${record?.current_version ?? 1}`}
          style={{ background: "#1E40AF" }}
        />
        <StatusDot filled={activeTask.status === "in_progress"} label={activeTask.status} />
        <div style={{ flex: 1 }} />
        {activeTask.status === "pending" && (
          <Button
            type="primary"
            loading={startMutation.isPending}
            onClick={() => startMutation.mutate(activeTask.id)}
          >
            Start QC
          </Button>
        )}
        {activeTask.status === "in_progress" && (
          <>
            <Button
              type="primary"
              loading={passMutation.isPending}
              onClick={() => passMutation.mutate(activeTask.id)}
            >
              Pass
            </Button>
            <Button
              danger
              onClick={() => setRejectModalOpen(true)}
            >
              Reject
            </Button>
          </>
        )}
      </div>

      {/* Split-screen */}
      <div style={{ flex: 1, overflow: "hidden" }}>
        <SplitWorkspace
          left={
            viewLoading ? (
              <Spin style={{ padding: 24 }} />
            ) : viewData?.objectUrl ? (
              viewData.contentType === "application/pdf" ? (
                <iframe
                  src={viewData.objectUrl}
                  style={{ width: "100%", height: "100%", border: "none" }}
                  title={`Record ${activeTask.record_id}`}
                />
              ) : (
                <OpenSeadragonViewer
                  imageUrl={viewData.objectUrl}
                  page={imagePage}
                  pageCount={imagePageCount}
                  onPageChange={setImagePage}
                />
              )
            ) : (
              <div style={{ padding: 24, color: "#64748B" }}>
                {activeTask.status === "pending"
                  ? "Start the task to load the image."
                  : "No image available for this record."}
              </div>
            )
          }
          right={
            <div style={{ padding: 16, overflowY: "auto", height: "100%" }}>
              <Typography.Title level={5}>Indexed Data</Typography.Title>
              {record?.indexed_data ? (
                <Card size="small">
                  {Object.entries(record.indexed_data).map(([k, v]) => (
                    <div key={k} style={{ marginBottom: 8 }}>
                      <Typography.Text type="secondary" style={{ textTransform: "capitalize" }}>
                        {k.replace(/_/g, " ")}
                      </Typography.Text>
                      <br />
                      <Typography.Text>{String(v)}</Typography.Text>
                    </div>
                  ))}
                </Card>
              ) : (
                <Typography.Text type="secondary">No indexed data yet.</Typography.Text>
              )}
            </div>
          }
        />
      </div>

      {/* Reject modal */}
      <Modal
        title={`Reject Record #${activeTask.record_id}`}
        open={rejectModalOpen}
        onCancel={() => setRejectModalOpen(false)}
        onOk={handleReject}
        confirmLoading={rejectMutation.isPending}
        okText="Confirm Rejection"
        okButtonProps={{ danger: true }}
      >
        <Typography.Paragraph type="secondary">
          The record will be sent back for re-indexing. The current indexed data
          will be preserved as version {record?.current_version ?? 1}.
        </Typography.Paragraph>
        <Form form={rejectForm} layout="vertical">
          <Form.Item
            name="reason"
            label="Rejection Reason"
            rules={[{ required: true, message: "Please provide a reason" }]}
          >
            <Input.TextArea rows={3} placeholder="Describe the issue with this record..." />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
