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
  Popconfirm,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import { ArrowLeft, CheckCircle2, Undo2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { RJSFSchema } from "@rjsf/utils";
import api from "@shared/api/client";
import { formatApiError } from "@shared/api/errors";
import { aqlConfigKey, getAqlConfig } from "@shared/api/aql";
import { completeTask, failTask, getTaskFieldResults, submitQcBatch } from "@shared/api/tasks";
import { useRecordImage } from "@shared/hooks/useRecordImage";
import { useTaskHeartbeat } from "@shared/hooks/useTaskHeartbeat";
import type { Batch, DocRecord, DocumentType, FieldResult, Task, UserRecord } from "@shared/types";
import { FieldMarkControl, isEmptyValue, unclassedFields, type FieldMark } from "@shared/components/DefectClasses";
import OpenSeadragonViewer from "@shared/components/ImageViewer/OpenSeadragonViewer";
import SchemaForm from "@shared/components/SchemaForm";
import SplitWorkspace from "@shared/components/SplitWorkspace";
import StatusDot from "@shared/components/StatusDot";

// A QC task belongs to an open QC batch — listed, reviewable again, and
// handed on with Submit batch — while its batch is still open (GET
// /tasks/mine fills batch_status; an open batch is "indexing").
const inOpenBatch = (t: Task) => t.task_type === "qc" && t.batch_status === "indexing";
const isReviewed = (t: Task) => t.status === "completed" || t.status === "failed";

const REVIEW_LABEL: Record<string, string> = {
  pending: "Not reviewed",
  in_progress: "In review",
  completed: "Passed",
  failed: "Rejected",
};

function ReviewTag({ task }: { task: Task }) {
  if (task.status === "completed") return <Tag color="success">Passed</Tag>;
  if (task.status === "failed") return <Tag color="error">Rejected</Tag>;
  return <StatusDot filled={task.status === "in_progress"} label={REVIEW_LABEL[task.status] ?? task.status} />;
}

/** One open QC batch: its records with how each was reviewed, any of them
 * reopened, and Submit batch once they're all done. */
function QcBatchDetail({
  batchId,
  tasks,
  onBack,
  onOpen,
  opening,
}: {
  batchId: number;
  tasks: Task[];
  onBack: () => void;
  onOpen: (task: Task) => void;
  opening: boolean;
}) {
  const qc = useQueryClient();
  const sorted = [...tasks].sort((a, b) => a.record_id - b.record_id);
  const remaining = tasks.filter((t) => !isReviewed(t)).length;
  const rejected = tasks.filter((t) => t.status === "failed").length;

  const submit = useMutation({
    mutationFn: () => submitQcBatch(batchId),
    onSuccess: async () => {
      message.success("QC batch submitted");
      await qc.invalidateQueries({ queryKey: ["my-tasks"] });
      onBack();
    },
    onError: (e: unknown) => message.error(formatApiError(e, "Couldn't submit the batch")),
  });

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16, gap: 12, flexWrap: "wrap" }}>
        <Space>
          <Button icon={<ArrowLeft size={16} />} onClick={onBack}>
            Back to queue
          </Button>
          <Typography.Text strong>QC Batch #{batchId}</Typography.Text>
          <Typography.Text type="secondary">
            {tasks.length - remaining} of {tasks.length} reviewed
            {rejected > 0 ? ` · ${rejected} rejected` : ""}
          </Typography.Text>
        </Space>
        <Tooltip
          title={remaining > 0 ? `${remaining} record${remaining === 1 ? "" : "s"} still need${remaining === 1 ? "s" : ""} a review` : undefined}
        >
          <Popconfirm
            title="Submit this QC batch?"
            description="You can't change these reviews after submitting."
            okText="Submit batch"
            onConfirm={() => submit.mutate()}
            disabled={remaining > 0}
          >
            <Button
              type="primary"
              icon={<CheckCircle2 size={16} />}
              disabled={remaining > 0}
              loading={submit.isPending}
              data-testid="submit-qc-batch"
            >
              Submit batch
            </Button>
          </Popconfirm>
        </Tooltip>
      </div>
      <List
        dataSource={sorted}
        rowKey="id"
        renderItem={(task) => (
          <List.Item
            data-qc-task={task.id}
            actions={[
              <Button type={isReviewed(task) ? "default" : "primary"} loading={opening} onClick={() => onOpen(task)}>
                {isReviewed(task) ? "Review again" : task.status === "in_progress" ? "Resume" : "Start QC"}
              </Button>,
            ]}
          >
            <List.Item.Meta title={`Record #${task.record_id}`} description={<ReviewTag task={task} />} />
          </List.Item>
        )}
      />
    </div>
  );
}

export default function QCWorkspace() {
  const qc = useQueryClient();
  const [activeTask, setActiveTask] = useState<Task | null>(null);
  const [rejectModalOpen, setRejectModalOpen] = useState(false);
  const [rejectForm] = Form.useForm<{ reason: string }>();
  const [fieldMarks, setFieldMarks] = useState<Record<string, FieldMark>>({});
  // The open QC batch being worked (its record list), and the records
  // reviewed in this sitting, most recent last — what "Previous record"
  // steps back through.
  const [selectedBatchId, setSelectedBatchId] = useState<number | null>(null);
  const [finished, setFinished] = useState<number[]>([]);
  // How the active record was reviewed before it was reopened, if it was.
  const [reopenedFrom, setReopenedFrom] = useState<string | null>(null);

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
  useTaskHeartbeat(activeTask?.id, !!activeTask && activeTask.status === "in_progress");

  // Fetch record data
  const { data: record } = useQuery<DocRecord>({
    queryKey: ["record", activeTask?.record_id],
    queryFn: () =>
      api.get(`/records/${activeTask!.record_id}`).then((r) => r.data),
    enabled: !!activeTask,
    refetchInterval: 10_000,
  });

  // Fetch batch (for document_type_id) and the document type's schema, so
  // the indexed data can be rendered through the same SchemaForm the DE QA
  // screen uses — a plain Object.entries dump can't render array/object
  // fields (parcels, registered owners, caveators, ...) at all, it just
  // stringifies them to "[object Object]".
  const { data: batchData } = useQuery<Batch>({
    queryKey: ["batch", activeTask?.batch_id],
    queryFn: () => api.get(`/batches/${activeTask!.batch_id}`).then((r) => r.data),
    enabled: !!activeTask,
  });

  const { data: docType } = useQuery<DocumentType>({
    queryKey: ["doctype", batchData?.document_type_id],
    queryFn: () => api.get(`/document-types/${batchData!.document_type_id}`).then((r) => r.data),
    enabled: !!batchData?.document_type_id,
  });

  // Per-field defect marking (ISO 2859-1) is only offered on projects that
  // opted into ISO sampling — see LotSettingsDrawer.tsx for the same
  // sampling_mode gating pattern. Defaults to today's plain Pass/Reject flow
  // while this is loading/absent, never flashing the field-review UI
  // incorrectly for a manual-mode project.
  const { data: aqlConfig } = useQuery({
    queryKey: aqlConfigKey(batchData?.project_id),
    queryFn: () => getAqlConfig(batchData!.project_id),
    enabled: !!batchData?.project_id,
  });
  const isIso = aqlConfig?.sampling_mode === "iso";

  const schemaProperties = (docType?.json_schema?.properties as Record<string, { title?: string; "x-critical"?: boolean }>) ?? {};

  // A record reviewed before (and reopened) shows the marks it was given.
  const { data: earlierMarks, isFetched: earlierMarksFetched } = useQuery({
    queryKey: ["task-field-results", activeTask?.id],
    queryFn: () => getTaskFieldResults(activeTask!.id),
    enabled: !!activeTask && isIso && !!reopenedFrom,
    staleTime: 0,
  });

  // Every top-level field defaults to Accepted — the QC agent only has to
  // flip the ones they find defective, rather than affirmatively clicking
  // "accept" on every field of every record.
  useEffect(() => {
    if (!isIso || !docType) return;
    if (reopenedFrom && !earlierMarksFetched) return;
    const earlier = new Map((earlierMarks ?? []).map((m) => [m.field_key, m]));
    const initial: Record<string, FieldMark> = {};
    for (const key of Object.keys(schemaProperties)) {
      const m = earlier.get(key);
      initial[key] = m
        ? { status: m.status, error_type: m.error_type ?? undefined, note: m.note ?? undefined }
        : { status: "accepted" };
    }
    setFieldMarks(initial);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTask?.id, isIso, docType, earlierMarks, earlierMarksFetched, reopenedFrom]);

  // Cached at the app root (App.tsx queries the same ["me"] key on load) —
  // needed to tell "locked by me" from "locked by someone else": comparing
  // against activeTask.assigned_to instead of the real logged-in user
  // produces a false "locked by another user" banner whenever the two
  // diverge, even though the lock is genuinely held by the person viewing it.
  const { data: me } = useQuery<UserRecord>({
    queryKey: ["me"],
    queryFn: () => api.get("/users/me").then((r) => r.data),
  });

  const startMutation = useMutation({
    mutationFn: (task: Task) => api.post<Task>(`/tasks/${task.id}/start`),
    // Only GET /tasks/mine fills batch_status; keep the queue's.
    onSuccess: (res, task) => {
      setActiveTask({ ...res.data, batch_status: task.batch_status, flagged: task.flagged });
      qc.invalidateQueries({ queryKey: ["my-tasks"] });
      qc.invalidateQueries({ queryKey: ["record", res.data.record_id] });
      qc.invalidateQueries({ queryKey: ["task-field-results", res.data.id] });
    },
    onError: (err: unknown) => {
      message.error(formatApiError(err, "Could not start task"));
      setActiveTask(null);
      setReopenedFrom(null);
    },
  });

  /** Open a record of the queue: a pending or reviewed one is (re)started,
   * one already in progress is resumed as it is. */
  const openTask = (task: Task) => {
    setReopenedFrom(isReviewed(task) ? task.status : null);
    setActiveTask(task);
    if (task.status !== "in_progress") startMutation.mutate(task);
  };

  /** After a review is submitted: on to the next record of the batch still
   * to be reviewed, or back to the batch's list to check and submit it. */
  const advance = async () => {
    if (!activeTask) return;
    const done = activeTask;
    qc.invalidateQueries({ queryKey: ["task-field-results", done.id] });
    if (!inOpenBatch(done)) {
      setActiveTask(null);
      setReopenedFrom(null);
      await qc.invalidateQueries({ queryKey: ["my-tasks"] });
      return;
    }
    setFinished((ids) => [...ids.filter((id) => id !== done.id), done.id]);
    await qc.invalidateQueries({ queryKey: ["my-tasks"] });
    const fresh = qc.getQueryData<Task[]>(["my-tasks"]) ?? [];
    const next = fresh
      .filter((t) => t.batch_id === done.batch_id && t.id !== done.id && !isReviewed(t))
      .sort((a, b) => a.record_id - b.record_id)[0];
    if (next) {
      openTask(next);
    } else {
      message.info("All records reviewed — check them and press Submit batch when ready.");
      setSelectedBatchId(done.batch_id);
      setActiveTask(null);
      setReopenedFrom(null);
    }
  };

  const passMutation = useMutation({
    mutationFn: (taskId: number) =>
      api.post(`/tasks/${taskId}/complete`, { indexed_data: null }),
    onSuccess: () => {
      message.success("Record passed QC");
      advance();
    },
    onError: () => message.error("Failed to complete task"),
  });

  const rejectMutation = useMutation({
    mutationFn: ({ taskId, reason }: { taskId: number; reason: string }) =>
      api.post(`/tasks/${taskId}/fail`, { reason }),
    onSuccess: () => {
      message.success("Record rejected — your QC supervisor reviews it before it goes back for rework");
      setRejectModalOpen(false);
      rejectForm.resetFields();
      advance();
    },
    onError: (err: { response?: { data?: { detail?: string } } }) =>
      message.error(err.response?.data?.detail ?? "Rejection failed"),
  });

  const handleReject = async () => {
    const values = await rejectForm.validateFields();
    if (!activeTask) return;
    rejectMutation.mutate({ taskId: activeTask.id, reason: values.reason });
  };

  // ISO-mode submit: routes through fail_task (reusing its existing
  // rework-batch creation) whenever any field is marked defective, and
  // complete_task only when every field is accepted — keeps all
  // rework-routing logic in the one place it already lives, rather than
  // teaching complete_task to sometimes behave like fail_task.
  const submitReviewMutation = useMutation({
    mutationFn: () => {
      if (!activeTask) throw new Error("No active task");
      const fieldResults: FieldResult[] = Object.entries(fieldMarks).map(([field_key, mark]) => ({
        field_key,
        status: mark.status,
        note: mark.status === "defective" ? mark.note : undefined,
        error_type: mark.status === "defective" ? mark.error_type : undefined,
      }));
      const defective = fieldResults.filter((f) => f.status === "defective");
      if (defective.length > 0) {
        const reason = `${defective.length} field(s) marked defective: ${defective.map((f) => f.field_key).join(", ")}`;
        return failTask(activeTask.id, { reason, field_results: fieldResults });
      }
      return completeTask(activeTask.id, { field_results: fieldResults });
    },
    onSuccess: () => {
      message.success("QC review saved");
      advance();
    },
    onError: (err: unknown) => message.error(formatApiError(err, "Failed to submit review")),
  });

  // Open QC batches (records stay listed until the batch is submitted),
  // and any other QC task as a plain list.
  const batchGroups = new Map<number, Task[]>();
  for (const t of (tasks ?? []).filter(inOpenBatch)) {
    batchGroups.set(t.batch_id, [...(batchGroups.get(t.batch_id) ?? []), t]);
  }
  const otherTasks = (tasks ?? []).filter((t) => !inOpenBatch(t));

  // Batch view: one open QC batch's records.
  if (!activeTask && selectedBatchId != null) {
    return (
      <div style={{ padding: 24 }}>
        <Typography.Title level={4}>QC Task Queue</Typography.Title>
        <QcBatchDetail
          batchId={selectedBatchId}
          tasks={batchGroups.get(selectedBatchId) ?? []}
          onBack={() => setSelectedBatchId(null)}
          onOpen={openTask}
          opening={startMutation.isPending}
        />
      </div>
    );
  }

  // Task list panel
  if (!activeTask) {
    return (
      <div style={{ padding: 24 }}>
        <Typography.Title level={4}>QC Task Queue</Typography.Title>
        {tasksLoading ? (
          <Spin />
        ) : !tasks?.length ? (
          <Empty description="No QC tasks assigned to you" />
        ) : (
          <>
            {batchGroups.size > 0 && (
              <List
                header={<Typography.Text type="secondary">QC batches</Typography.Text>}
                dataSource={[...batchGroups.entries()].sort((a, b) => a[0] - b[0])}
                rowKey={([batchId]) => batchId}
                style={{ marginBottom: otherTasks.length > 0 ? 24 : 0 }}
                renderItem={([batchId, batchTasks]) => {
                  const reviewed = batchTasks.filter(isReviewed).length;
                  const all = reviewed === batchTasks.length;
                  return (
                    <List.Item
                      data-qc-batch={batchId}
                      actions={[
                        <Button type="primary" onClick={() => setSelectedBatchId(batchId)}>
                          {all ? "Check & submit" : reviewed > 0 ? "Continue" : "Open"}
                        </Button>,
                      ]}
                    >
                      <List.Item.Meta
                        title={<Typography.Text strong>QC Batch #{batchId}</Typography.Text>}
                        description={
                          <Space size={8}>
                            <Typography.Text type="secondary">
                              {reviewed} of {batchTasks.length} record{batchTasks.length === 1 ? "" : "s"} reviewed
                            </Typography.Text>
                            {all && <Tag color="processing">Ready to submit</Tag>}
                          </Space>
                        }
                      />
                    </List.Item>
                  );
                }}
              />
            )}
            {otherTasks.length > 0 && (
              <List
                header={batchGroups.size > 0 ? <Typography.Text type="secondary">Other records</Typography.Text> : undefined}
                dataSource={otherTasks}
                rowKey="id"
                renderItem={(task) => (
                  <List.Item
                    actions={[
                      <Button type="primary" loading={startMutation.isPending} onClick={() => openTask(task)}>
                        {task.status === "pending" ? "Start QC" : "Resume"}
                      </Button>,
                    ]}
                  >
                    <List.Item.Meta
                      title={`Record #${task.record_id}`}
                      description={
                        <>
                          Batch #{task.batch_id} ·{" "}
                          <StatusDot filled={task.status === "in_progress"} label={task.status} />
                        </>
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

  // "Previous record": the one reviewed just before this in this sitting,
  // or else the one before it in the batch.
  const inBatch = inOpenBatch(activeTask);
  const batchTasks = inBatch ? (batchGroups.get(activeTask.batch_id) ?? []) : [];
  const byId = new Map(batchTasks.map((t) => [t.id, t]));
  const fromHistory = [...finished].reverse().find((id) => id !== activeTask.id && byId.has(id));
  const previous = fromHistory != null
    ? byId.get(fromHistory)
    : batchTasks
        .filter((t) => t.record_id < activeTask.record_id && isReviewed(t))
        .sort((a, b) => b.record_id - a.record_id)[0];
  const leave = () => {
    setActiveTask(null);
    setReopenedFrom(null);
    if (inBatch) setSelectedBatchId(activeTask.batch_id);
    qc.invalidateQueries({ queryKey: ["my-tasks"] });
  };

  // Split-screen QC view
  // Guarded on `me` being loaded — comparing against undefined would flag
  // every locked record as "locked by other" during the brief window before
  // the ["me"] query resolves.
  const isLockedByOther = !!me && record?.locked_by != null && record.locked_by !== me.id;

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
      {reopenedFrom && (
        <Alert
          type="info"
          banner
          message={`You reviewed this record before (${reopenedFrom === "failed" ? "rejected" : "passed"}). Change anything that's wrong and press Done again — it counts once you submit the batch.`}
        />
      )}
      {/* Not current_version > 1: every indexed record is already at version 2. */}
      {record?.previously_sent_back && (
        <Alert
          type="warning"
          message={`This record was rejected before and has been corrected — this is version ${record.current_version}.`}
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
        <Button size="small" icon={<ArrowLeft size={14} />} onClick={leave}>
          {inBatch ? "Back to batch" : "Back to queue"}
        </Button>
        {previous && (
          <Tooltip title="Go back to the record before this one and review it again">
            <Button
              size="small"
              icon={<Undo2 size={14} />}
              loading={startMutation.isPending}
              onClick={() => {
                setFinished((ids) => ids.filter((id) => id !== previous.id));
                openTask(previous);
              }}
            >
              Previous record
            </Button>
          </Tooltip>
        )}
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
            onClick={() => startMutation.mutate(activeTask)}
          >
            Start QC
          </Button>
        )}
        {activeTask.status === "in_progress" && isIso && (
          <Button
            type="primary"
            loading={submitReviewMutation.isPending}
            disabled={!docType || unclassedFields(fieldMarks).length > 0}
            title={unclassedFields(fieldMarks).length > 0 ? "Choose what is wrong with each defective field" : undefined}
            onClick={() => submitReviewMutation.mutate()}
          >
            Done
          </Button>
        )}
        {activeTask.status === "in_progress" && !isIso && (
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
                  {docType ? (
                    <SchemaForm
                      schema={docType.json_schema as RJSFSchema}
                      initialValues={record.indexed_data}
                      onSubmit={() => {}}
                      formId={`qc-view-${activeTask.record_id}`}
                      readOnly
                    />
                  ) : (
                    <Spin style={{ margin: 16 }} />
                  )}
                </Card>
              ) : (
                <Typography.Text type="secondary">No indexed data yet.</Typography.Text>
              )}

              {isIso && (
                <Card size="small" title="Field Review" style={{ marginTop: 16 }}>
                  {!docType ? (
                    <Spin style={{ margin: 16 }} />
                  ) : Object.keys(schemaProperties).length === 0 ? (
                    <Typography.Text type="secondary">This document type has no fields to review.</Typography.Text>
                  ) : (
                    <Space direction="vertical" style={{ width: "100%" }} size={16}>
                      {Object.entries(schemaProperties).map(([key, field]) => (
                        <FieldMarkControl
                          key={key}
                          title={field.title || key}
                          isIdentifier={!!field["x-critical"]}
                          mark={fieldMarks[key] ?? { status: "accepted" }}
                          onChange={(m) => setFieldMarks((prev) => ({ ...prev, [key]: m }))}
                          valueEmpty={isEmptyValue((record?.indexed_data as Record<string, unknown> | undefined)?.[key])}
                          errorClasses={aqlConfig && "error_classes" in aqlConfig ? aqlConfig.error_classes : undefined}
                          notePlaceholder="Note (optional)"
                        />
                      ))}
                    </Space>
                  )}
                </Card>
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
