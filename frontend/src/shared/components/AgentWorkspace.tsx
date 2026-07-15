import { Alert, Badge, Button, Space, Spin, Typography, message } from "antd";
import { useState, useRef, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import type { RJSFSchema } from "@rjsf/utils";
import api from "@shared/api/client";
import { formatApiError } from "@shared/api/errors";
import { useRecordImage } from "@shared/hooks/useRecordImage";
import type { DocumentType, Task } from "@shared/types";
import OpenSeadragonViewer from "./ImageViewer/OpenSeadragonViewer";
import SchemaForm, { type SchemaFormHandle } from "./SchemaForm";
import SplitWorkspace from "./SplitWorkspace";

interface Props {
  task: Task;
  onComplete?: () => void;
}

export default function AgentWorkspace({ task, onComplete }: Props) {
  const qc = useQueryClient();
  const [localStatus, setLocalStatus] = useState(task.status);
  const formId = `task-form-${task.id}`;
  const formRef = useRef<SchemaFormHandle>(null);

  // Fetch the record image once task is in_progress — see useRecordImage
  // for why this proxies through the backend rather than using a presigned
  // S3 URL, and why it needs staleTime: Infinity.
  const { data: viewData, isLoading: viewLoading } = useRecordImage(
    task.record_id,
    localStatus === "in_progress"
  );

  // Fetch record for lock info and existing indexed data
  const { data: record } = useQuery({
    queryKey: ["record", task.record_id],
    queryFn: () => api.get(`/records/${task.record_id}`).then((r) => r.data),
    refetchInterval: 10_000,
  });

  // Fetch batch to get document_type_id
  const { data: batchData } = useQuery({
    queryKey: ["batch", task.batch_id],
    queryFn: () => api.get(`/batches/${task.batch_id}`).then((r) => r.data),
  });

  // Fetch document type schema
  const { data: docType } = useQuery<DocumentType>({
    queryKey: ["doctype", batchData?.document_type_id],
    queryFn: () =>
      api.get(`/document-types/${batchData.document_type_id}`).then((r) => r.data),
    enabled: !!batchData?.document_type_id,
  });

  // Start task — acquires lock, transitions to in_progress immediately via local state
  const startMutation = useMutation({
    mutationFn: () => api.post(`/tasks/${task.id}/start`),
    onSuccess: () => {
      setLocalStatus("in_progress");
      qc.invalidateQueries({ queryKey: ["record", task.record_id] });
    },
    onError: (e: unknown) => {
      message.error(formatApiError(e, "Failed to start task"));
    },
  });

  // Save draft — persists indexed_data without completing or releasing the lock
  const saveMutation = useMutation({
    mutationFn: (indexed_data: Record<string, unknown>) =>
      api.patch(`/records/${task.record_id}/draft`, { indexed_data }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["record", task.record_id] });
      message.success("Progress saved");
    },
    onError: (e: unknown) => {
      message.error(formatApiError(e, "Save failed — please try again"));
    },
  });

  // Complete task — saves indexed data and releases lock
  const completeMutation = useMutation({
    mutationFn: (indexed_data: Record<string, unknown>) =>
      api.post(`/tasks/${task.id}/complete`, { indexed_data }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["record", task.record_id] });
      qc.invalidateQueries({ queryKey: ["my-tasks"] });
      onComplete?.();
    },
    onError: (e: unknown) => {
      message.error(formatApiError(e, "Submission failed — please try again"));
    },
  });

  const isLockedByOther = record?.locked_by && record.locked_by !== task.assigned_to;
  const isMyTask = localStatus === "in_progress";

  const workspaceLabel =
    task.task_type === "qa" ? "Quality Check"
    : task.task_type === "qc" ? "Quality Control"
    : "Data Entry";

  // Opening a pending task immediately starts it — no separate "Start" click
  // inside the workspace. Guard with a ref so this fires exactly once even
  // if the component re-renders before the mutation settles.
  const autoStarted = useRef(false);
  useEffect(() => {
    if (localStatus === "pending" && !autoStarted.current) {
      autoStarted.current = true;
      startMutation.mutate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [localStatus]);

  // ─── Pending: starting automatically ────────────────────────────────────
  if (localStatus === "pending") {
    return (
      <div style={{ padding: 24 }}>
        <Typography.Title level={4}>Record #{task.record_id}</Typography.Title>
        {startMutation.isError ? (
          <Space direction="vertical">
            <Typography.Text type="danger">
              {formatApiError(startMutation.error, "Failed to start task")}
            </Typography.Text>
            <Button
              type="primary"
              onClick={() => startMutation.mutate()}
              loading={startMutation.isPending}
            >
              Retry
            </Button>
          </Space>
        ) : (
          <Space>
            <Spin />
            <Typography.Text>Starting {workspaceLabel.toLowerCase()}…</Typography.Text>
          </Space>
        )}
      </div>
    );
  }

  // ─── In Progress: split-screen workspace ────────────────────────────────
  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      {/* Lock / rework banners */}
      {isLockedByOther && (
        <Alert
          type="error"
          message={`This record is locked by another user (ID: ${record.locked_by}). Contact your supervisor.`}
          banner
        />
      )}
      {isMyTask && !isLockedByOther && (
        <Alert type="info" message="Record locked by you — submit to release." banner />
      )}
      {record?.current_version > 1 && (
        <Alert
          type="warning"
          message={`Rework — version ${record.current_version}. Previous data is pre-filled.`}
          banner
        />
      )}

      <div style={{ flex: 1, overflow: "hidden" }}>
        <SplitWorkspace
          left={
            <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
              <div
                style={{
                  padding: "6px 14px",
                  borderBottom: "1px solid #E2E8F0",
                  flexShrink: 0,
                  background: "#FFFFFF",
                }}
              >
                <Typography.Text strong style={{ fontSize: 13 }}>
                  {record?.original_filename ?? `Record #${task.record_id}`}
                </Typography.Text>
              </div>
              <div style={{ flex: 1, overflow: "hidden" }}>
                {viewLoading ? (
                  <Spin style={{ margin: 40 }} />
                ) : viewData?.objectUrl ? (
                  viewData.contentType === "application/pdf" ? (
                    <iframe
                      src={viewData.objectUrl}
                      style={{ width: "100%", height: "100%", border: "none" }}
                      title={`Record ${task.record_id}`}
                    />
                  ) : (
                    <OpenSeadragonViewer imageUrl={viewData.objectUrl} />
                  )
                ) : (
                  <div style={{ padding: 24, color: "#64748B" }}>No file attached to this record.</div>
                )}
              </div>
            </div>
          }
          right={
            // Flex column: fixed header + scrollable form + sticky submit footer
            <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
              {/* Header row */}
              <div
                style={{
                  padding: "6px 14px",
                  borderBottom: "1px solid #E2E8F0",
                  flexShrink: 0,
                  background: "#FFFFFF",
                }}
              >
                <Typography.Text strong style={{ fontSize: 13 }}>
                  {workspaceLabel} — Record #{task.record_id}&nbsp;
                </Typography.Text>
                <Badge
                  count={`v${record?.current_version ?? 1}`}
                  style={{ background: "#1E40AF" }}
                />
              </div>

              {/* Scrollable form area */}
              <div style={{ flex: 1, overflow: "auto", padding: "8px 14px" }}>
                {docType ? (
                  <SchemaForm
                    ref={formRef}
                    schema={docType.json_schema as RJSFSchema}
                    initialValues={record?.indexed_data ?? undefined}
                    onSubmit={(values) => completeMutation.mutate(values)}
                    formId={formId}
                  />
                ) : (
                  <Spin style={{ margin: 16 }} />
                )}
              </div>

              {/* Sticky submit footer — always visible regardless of form length */}
              <div
                style={{
                  padding: "8px 14px",
                  borderTop: "1px solid #E2E8F0",
                  background: "#F8FAFC",
                  flexShrink: 0,
                }}
              >
                {completeMutation.isError && (
                  <Typography.Text
                    type="danger"
                    style={{ display: "block", fontSize: 12, marginBottom: 6 }}
                  >
                    {formatApiError(completeMutation.error, "Submission failed — please try again")}
                  </Typography.Text>
                )}
                <Space.Compact block>
                  <Button
                    style={{ width: "35%" }}
                    loading={saveMutation.isPending}
                    onClick={() => {
                      const values = formRef.current?.getValues();
                      if (values) saveMutation.mutate(values);
                    }}
                  >
                    Save Progress
                  </Button>
                  <Button
                    type="primary"
                    style={{ width: "65%" }}
                    loading={completeMutation.isPending}
                    onClick={() =>
                      document.getElementById(`${formId}-submit`)?.click()
                    }
                  >
                    Submit &amp; Complete
                  </Button>
                </Space.Compact>
              </div>
            </div>
          }
        />
      </div>
    </div>
  );
}
