import { Alert, Badge, Button, Space, Spin, Typography, message } from "antd";
import { useState, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import type { RJSFSchema } from "@rjsf/utils";
import api from "@shared/api/client";
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

  // Fetch view URL + content_type once task is in_progress.
  // staleTime: Infinity + refetchOnWindowFocus: false prevent a new presigned URL from
  // being generated while the image is loading, which would destroy the OSD viewer
  // mid-flight and produce NS_BINDING_ABORTED.
  const { data: viewData, isLoading: viewLoading } = useQuery({
    queryKey: ["record-view-url", task.record_id],
    queryFn: () =>
      api
        .get<{ view_url: string; content_type: string }>(`/records/${task.record_id}/view-url`)
        .then((r) => r.data),
    enabled: localStatus === "in_progress",
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });

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
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? "Failed to start task");
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
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? "Save failed — please try again");
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
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? "Submission failed — please try again");
    },
  });

  const isLockedByOther = record?.locked_by && record.locked_by !== task.assigned_to;
  const isMyTask = localStatus === "in_progress";

  const startLabel =
    task.task_type === "qa" ? "Start Quality Check"
    : task.task_type === "qc" ? "Start Quality Control"
    : "Start Indexing";

  const workspaceLabel =
    task.task_type === "qa" ? "Quality Check"
    : task.task_type === "qc" ? "Quality Control"
    : "Data Entry";

  // ─── Pending: show start button ─────────────────────────────────────────
  if (localStatus === "pending") {
    return (
      <div style={{ padding: 24 }}>
        <Typography.Title level={4}>Record #{task.record_id}</Typography.Title>
        <Button
          type="primary"
          onClick={() => startMutation.mutate()}
          loading={startMutation.isPending}
        >
          {startLabel}
        </Button>
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
            viewLoading ? (
              <Spin style={{ margin: 40 }} />
            ) : viewData?.view_url ? (
              viewData.content_type === "application/pdf" ? (
                <iframe
                  src={viewData.view_url}
                  style={{ width: "100%", height: "100%", border: "none" }}
                  title={`Record ${task.record_id}`}
                />
              ) : (
                <OpenSeadragonViewer imageUrl={viewData.view_url} />
              )
            ) : (
              <div style={{ padding: 24, color: "#888" }}>No file attached to this record.</div>
            )
          }
          right={
            // Flex column: fixed header + scrollable form + sticky submit footer
            <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
              {/* Header row */}
              <div
                style={{
                  padding: "6px 14px",
                  borderBottom: "1px solid #f0f0f0",
                  flexShrink: 0,
                  background: "#fff",
                }}
              >
                <Typography.Text strong style={{ fontSize: 13 }}>
                  {workspaceLabel} — Record #{task.record_id}&nbsp;
                </Typography.Text>
                <Badge
                  count={`v${record?.current_version ?? 1}`}
                  style={{ background: "#108ee9" }}
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
                  borderTop: "1px solid #f0f0f0",
                  background: "#fafafa",
                  flexShrink: 0,
                }}
              >
                {completeMutation.isError && (
                  <Typography.Text
                    type="danger"
                    style={{ display: "block", fontSize: 12, marginBottom: 6 }}
                  >
                    {(completeMutation.error as { response?: { data?: { detail?: string } } })
                      ?.response?.data?.detail ?? "Submission failed — please try again"}
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
