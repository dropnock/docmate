import { Alert, Badge, Button, Modal, Space, Spin, Typography, message } from "antd";
import { useState, useRef, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import type { RJSFSchema } from "@rjsf/utils";
import api from "@shared/api/client";
import { formatApiError } from "@shared/api/errors";
import { useRecordImage } from "@shared/hooks/useRecordImage";
import type { DocumentType, Task, UserRecord } from "@shared/types";
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
  const [skipModalOpen, setSkipModalOpen] = useState(false);

  // Fetch the record image once task is in_progress — see useRecordImage
  // for why this proxies through the backend rather than using a presigned
  // S3 URL, and why it needs staleTime: Infinity.
  const {
    data: viewData,
    isLoading: viewLoading,
    page: imagePage,
    setPage: setImagePage,
    pageCount: imagePageCount,
  } = useRecordImage(task.record_id, localStatus === "in_progress");

  // Fetch record for lock info and existing indexed data
  const { data: record } = useQuery({
    queryKey: ["record", task.record_id],
    queryFn: () => api.get(`/records/${task.record_id}`).then((r) => r.data),
    refetchInterval: 10_000,
  });

  // Cached at the app root (App.tsx queries the same ["me"] key on load) —
  // needed to tell "locked by me" from "locked by someone else": comparing
  // against task.assigned_to instead of the real logged-in user produces a
  // false "locked by another user" banner whenever the two diverge (stale
  // cached task data, a rework re-assignment, etc.), even though the lock
  // is genuinely held by the person looking at it.
  const { data: me } = useQuery<UserRecord>({
    queryKey: ["me"],
    queryFn: () => api.get("/users/me").then((r) => r.data),
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

  // Skip — a third option alongside Save Progress / Done for a record that
  // can't be indexed at all (blank page, unreadable scan, wrong document).
  // Skips the schema form entirely — there's no data to submit — unlike
  // Done, which requires it to validate. The user picks which of the five
  // terminal statuses applies; that choice becomes the record's new status
  // directly, no separate confirmation step. Reopenable afterward like any
  // other record in the batch, same as an indexed one.
  const skipMutation = useMutation({
    mutationFn: (status: "withdrawn" | "ineligible" | "excluded" | "lapsed" | "illegible") =>
      api.post(`/tasks/${task.id}/skip`, { status }),
    onSuccess: (_data, status) => {
      message.success(`Record marked ${status}`);
      setSkipModalOpen(false);
      qc.invalidateQueries({ queryKey: ["record", task.record_id] });
      qc.invalidateQueries({ queryKey: ["my-tasks"] });
      onComplete?.();
    },
    onError: (e: unknown) => {
      message.error(formatApiError(e, "Failed to skip record"));
    },
  });

  // Guarded on `me` being loaded — comparing against undefined would flag
  // every locked record as "locked by other" during the brief window before
  // the ["me"] query resolves.
  const isLockedByOther = !!me && record?.locked_by != null && record.locked_by !== me.id;
  const isMyTask = localStatus === "in_progress";

  const workspaceLabel =
    task.task_type === "qa" ? "Quality Check"
    : task.task_type === "qc" ? "Quality Control"
    : "Data Entry";

  // Opening a task immediately (re-)starts it — no separate "Start" click
  // inside the workspace. This also covers reopening an already-completed
  // indexing task from My Tasks (record indexed/withdrawn/ineligible/
  // excluded/lapsed/illegible, batch not yet completed): start_task
  // re-acquires the lock and flips the record back to "indexing" regardless
  // of its prior status, so any non-"in_progress" task — not just "pending"
  // — needs this. QA/QC
  // tasks never reach here already-completed (they leave My Tasks for good
  // on completion, unchanged), so this never mis-fires for them. Guard with
  // a ref so this fires exactly once even if the component re-renders
  // before the mutation settles.
  const autoStarted = useRef(false);
  useEffect(() => {
    if (localStatus !== "in_progress" && !autoStarted.current) {
      autoStarted.current = true;
      startMutation.mutate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [localStatus]);

  // ─── Not yet in progress: starting automatically ────────────────────────
  if (localStatus !== "in_progress") {
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
          message={
            // Indexing sees this on its own reopened/corrected records too
            // (My Tasks lets an indexer go back in before the batch is
            // completed) — "Rework" implies a QA rejection, which isn't
            // necessarily what happened here.
            task.task_type === "indexing"
              ? `Editing submitted data — version ${record.current_version}. Previous data is pre-filled.`
              : `Rework — version ${record.current_version}. Previous data is pre-filled.`
          }
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
                    <OpenSeadragonViewer
                      imageUrl={viewData.objectUrl}
                      page={imagePage}
                      pageCount={imagePageCount}
                      onPageChange={setImagePage}
                    />
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
                <div style={{ display: "flex", gap: 8 }}>
                  {task.task_type === "indexing" && (
                    <Button danger onClick={() => setSkipModalOpen(true)}>
                      Skip
                    </Button>
                  )}
                  <Space.Compact block style={{ flex: 1 }}>
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
                      {task.task_type === "indexing" ? "Done" : "Submit & Complete"}
                    </Button>
                  </Space.Compact>
                </div>
              </div>
            </div>
          }
        />
      </div>

      <Modal
        title="Skip Record"
        open={skipModalOpen}
        onCancel={() => setSkipModalOpen(false)}
        footer={
          <Button onClick={() => setSkipModalOpen(false)} disabled={skipMutation.isPending}>
            Cancel
          </Button>
        }
      >
        <Typography.Paragraph type="secondary">
          Use this when the record can&apos;t be indexed at all — blank page, unreadable
          scan, wrong document. Choose why; no data will be submitted. It stays in your
          batch list marked with the status you pick until you complete the batch, so you
          can still change your mind.
        </Typography.Paragraph>
        <Space style={{ width: "100%" }} size="middle" wrap>
          <Button
            block
            danger
            loading={skipMutation.isPending && skipMutation.variables === "withdrawn"}
            disabled={skipMutation.isPending}
            onClick={() => skipMutation.mutate("withdrawn")}
          >
            Withdrawn
          </Button>
          <Button
            block
            danger
            loading={skipMutation.isPending && skipMutation.variables === "excluded"}
            disabled={skipMutation.isPending}
            onClick={() => skipMutation.mutate("excluded")}
          >
            Excluded
          </Button>
          <Button
            block
            danger
            loading={skipMutation.isPending && skipMutation.variables === "ineligible"}
            disabled={skipMutation.isPending}
            onClick={() => skipMutation.mutate("ineligible")}
          >
            Ineligible
          </Button>
          <Button
            block
            danger
            loading={skipMutation.isPending && skipMutation.variables === "lapsed"}
            disabled={skipMutation.isPending}
            onClick={() => skipMutation.mutate("lapsed")}
          >
            Lapsed
          </Button>
          <Button
            block
            danger
            loading={skipMutation.isPending && skipMutation.variables === "illegible"}
            disabled={skipMutation.isPending}
            onClick={() => skipMutation.mutate("illegible")}
          >
            Illegible
          </Button>
        </Space>
      </Modal>
    </div>
  );
}
