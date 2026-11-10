import {
  Alert, Button, Card, Empty, Input, Modal, Popconfirm, Segmented, Space, Spin, Tag, Typography, message,
} from "antd";
import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { RJSFSchema } from "@rjsf/utils";
import api from "@shared/api/client";
import { formatApiError } from "@shared/api/errors";
import {
  cancelInspection, completeInspection, getInspection, lotDetailKey, lotInspectionKey,
  lotInspectionReportKey, lotInspectionsKey, reviewInspectionItem,
} from "@shared/api/lots";
import { useRecordImage } from "@shared/hooks/useRecordImage";
import OpenSeadragonViewer from "@shared/components/ImageViewer/OpenSeadragonViewer";
import SchemaForm from "@shared/components/SchemaForm";
import SplitWorkspace from "@shared/components/SplitWorkspace";
import type { DocRecord, DocumentType, FieldResult, InspectionItem, QcFieldStatus } from "@shared/types";

interface Props {
  lotId: number;
  inspectionId: number;
  onClose: () => void;
}

type Marks = Record<string, { status: QcFieldStatus; note?: string }>;

const ITEM_TAG: Record<InspectionItem["status"], { color: string; label: string }> = {
  pending: { color: "default", label: "To review" },
  accepted: { color: "green", label: "Conforming" },
  defective: { color: "red", label: "Non-conforming" },
};

/** The digitizing supervisor's review of an outgoing inspection's random
 * sample: one sampled record at a time, image beside its indexed data, every
 * top-level field marked accepted/defective — same marking model as the
 * customer's QCWorkspace, but saved against the inspection
 * (lot_inspection_service.review_item) rather than a QC task, and
 * re-reviewable until the inspection is completed. */
export default function LotInspectionWorkspace({ lotId, inspectionId, onClose }: Props) {
  const qc = useQueryClient();
  const [selectedId, setSelectedId] = useState<number | undefined>();
  const [marks, setMarks] = useState<Marks>({});
  const [completeOpen, setCompleteOpen] = useState(false);
  const [summaryNote, setSummaryNote] = useState("");

  const { data: inspection, isLoading } = useQuery({
    queryKey: lotInspectionKey(lotId, inspectionId),
    queryFn: () => getInspection(lotId, inspectionId),
  });
  const inProgress = inspection?.status === "in_progress";

  // Land on the first unreviewed record when opening (or resuming).
  useEffect(() => {
    if (!inspection || selectedId !== undefined) return;
    const first = inspection.items.find((i) => i.status === "pending") ?? inspection.items[0];
    setSelectedId(first?.record_id);
  }, [inspection, selectedId]);

  const item = inspection?.items.find((i) => i.record_id === selectedId);

  const { data: record } = useQuery<DocRecord>({
    queryKey: ["record", selectedId],
    queryFn: () => api.get(`/records/${selectedId}`).then((r) => r.data),
    enabled: selectedId !== undefined,
  });
  const { data: docType } = useQuery<DocumentType>({
    queryKey: ["doctype", item?.document_type_id],
    queryFn: () => api.get(`/document-types/${item!.document_type_id}`).then((r) => r.data),
    enabled: !!item?.document_type_id,
  });
  const {
    data: viewData, isLoading: viewLoading, page, setPage, pageCount,
  } = useRecordImage(selectedId, selectedId !== undefined);

  const schemaProperties = useMemo(
    () => (docType?.json_schema?.properties as Record<string, { title?: string; "x-critical"?: boolean }>) ?? {},
    [docType],
  );

  // Previous marks if this record was already reviewed, otherwise every
  // field defaults to Accepted so only the faulty ones need flipping.
  useEffect(() => {
    if (!item) return;
    const initial: Marks = {};
    for (const key of Object.keys(schemaProperties)) initial[key] = { status: "accepted" };
    for (const fr of item.field_results) initial[fr.field_key] = { status: fr.status, note: fr.note ?? undefined };
    setMarks(initial);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item?.record_id, item?.reviewed_at, schemaProperties]);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: lotInspectionKey(lotId, inspectionId) });
    qc.invalidateQueries({ queryKey: lotInspectionsKey(lotId) });
    qc.invalidateQueries({ queryKey: lotInspectionReportKey(lotId) });
    qc.invalidateQueries({ queryKey: lotDetailKey(lotId) });
  };

  const saveMutation = useMutation({
    mutationFn: () => {
      const fieldResults: FieldResult[] = Object.entries(marks).map(([field_key, m]) => ({
        field_key, status: m.status, note: m.status === "defective" ? m.note : undefined,
      }));
      return reviewInspectionItem(lotId, inspectionId, selectedId!, fieldResults);
    },
    onSuccess: async () => {
      message.success("Review saved");
      const refreshed = await qc.fetchQuery({
        queryKey: lotInspectionKey(lotId, inspectionId),
        queryFn: () => getInspection(lotId, inspectionId),
      });
      invalidate();
      const next = refreshed.items.find((i) => i.status === "pending");
      if (next) setSelectedId(next.record_id);
    },
    onError: (err: unknown) => message.error(formatApiError(err, "Failed to save review")),
  });

  const completeMutation = useMutation({
    mutationFn: () => completeInspection(lotId, inspectionId, summaryNote),
    onSuccess: (result) => {
      setCompleteOpen(false);
      invalidate();
      if (result.status === "passed") {
        message.success("Inspection passed — the lot can be released once any reworked records are back");
      } else {
        message.warning("Inspection failed — non-conforming records sent to rework; re-inspect once they're back");
      }
      onClose();
    },
    onError: (err: unknown) => message.error(formatApiError(err, "Failed to complete inspection")),
  });

  const cancelMutation = useMutation({
    mutationFn: () => cancelInspection(lotId, inspectionId),
    onSuccess: () => {
      invalidate();
      message.info("Inspection cancelled");
      onClose();
    },
    onError: (err: unknown) => message.error(formatApiError(err, "Failed to cancel inspection")),
  });

  if (isLoading || !inspection) return <Spin style={{ padding: 24 }} />;

  const reviewed = inspection.items.filter((i) => i.status !== "pending").length;
  const allReviewed = reviewed === inspection.items.length;
  // Same rule complete_inspection applies server-side, previewed so the
  // supervisor sees the verdict before committing to it.
  const defectiveMarks = inspection.items.flatMap((i) => i.field_results.filter((f) => f.status === "defective"));
  const critical = defectiveMarks.filter((f) => f.is_critical).length;
  const minor = defectiveMarks.length - critical;
  const wouldPass = critical === 0 && minor <= inspection.acceptance_number;
  const nonconformingRecords = inspection.items.filter((i) => i.status === "defective").length;

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column" }}>
      <div
        style={{
          display: "flex", alignItems: "center", gap: 12, padding: "8px 16px",
          borderBottom: "1px solid #E2E8F0", background: "#FFFFFF", flexWrap: "wrap",
        }}
      >
        <Button size="small" onClick={onClose}>← Back to lot</Button>
        <Typography.Text strong>Inspection #{inspection.attempt_number}</Typography.Text>
        <Typography.Text type="secondary">
          AQL {inspection.aql_level} · code {inspection.code_letter} · n={inspection.sample_size} of N=
          {inspection.lot_size} · Ac {inspection.acceptance_number} / Re {inspection.rejection_number}
        </Typography.Text>
        <Tag color={allReviewed ? "green" : "blue"}>{reviewed} / {inspection.items.length} reviewed</Tag>
        <div style={{ flex: 1 }} />
        {inProgress && (
          <>
            <Popconfirm
              title="Cancel this inspection?"
              description="The attempt stays on the report as cancelled; a fresh sample is drawn next time."
              onConfirm={() => cancelMutation.mutate()}
            >
              <Button danger loading={cancelMutation.isPending}>Cancel inspection</Button>
            </Popconfirm>
            <Button type="primary" disabled={!allReviewed} onClick={() => setCompleteOpen(true)}>
              Complete inspection
            </Button>
          </>
        )}
      </div>

      {!inProgress && (
        <Alert type="info" banner message={`This inspection is ${inspection.status} — marks are read-only.`} />
      )}

      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
        <div style={{ width: 240, borderRight: "1px solid #E2E8F0", overflowY: "auto", background: "#F8FAFC" }}>
          {inspection.items.map((i) => (
            <div
              key={i.record_id}
              onClick={() => setSelectedId(i.record_id)}
              style={{
                padding: "10px 12px", cursor: "pointer", borderBottom: "1px solid #E2E8F0",
                background: i.record_id === selectedId ? "#DBEAFE" : undefined,
              }}
            >
              <div style={{ fontWeight: 500 }}>#{i.record_id} {i.source_identifier ?? ""}</div>
              <Tag color={ITEM_TAG[i.status].color} style={{ marginTop: 4 }}>{ITEM_TAG[i.status].label}</Tag>
            </div>
          ))}
        </div>

        <div style={{ flex: 1, overflow: "hidden" }}>
          {!item ? (
            <Empty style={{ marginTop: 48 }} description="Select a sampled record" />
          ) : (
            <SplitWorkspace
              left={
                viewLoading ? (
                  <Spin style={{ padding: 24 }} />
                ) : viewData?.objectUrl ? (
                  viewData.contentType === "application/pdf" ? (
                    <iframe
                      src={viewData.objectUrl}
                      style={{ width: "100%", height: "100%", border: "none" }}
                      title={`Record ${item.record_id}`}
                    />
                  ) : (
                    <OpenSeadragonViewer
                      imageUrl={viewData.objectUrl}
                      page={page}
                      pageCount={pageCount}
                      onPageChange={setPage}
                    />
                  )
                ) : (
                  <div style={{ padding: 24, color: "#64748B" }}>No image available for this record.</div>
                )
              }
              right={
                <div style={{ padding: 16, overflowY: "auto", height: "100%" }}>
                  {record && record.current_version !== item.record_version && (
                    <Alert
                      type="warning"
                      style={{ marginBottom: 12 }}
                      message={`Sampled at v${item.record_version}; the record is now v${record.current_version} (reworked since).`}
                    />
                  )}
                  <Typography.Title level={5}>Indexed Data</Typography.Title>
                  <Card size="small">
                    {!docType || !record ? (
                      <Spin style={{ margin: 16 }} />
                    ) : (
                      <SchemaForm
                        key={item.record_id}
                        schema={docType.json_schema as RJSFSchema}
                        initialValues={record.indexed_data ?? {}}
                        onSubmit={() => {}}
                        formId={`inspection-view-${item.record_id}`}
                        readOnly
                      />
                    )}
                  </Card>

                  <Card size="small" title="Field Review" style={{ marginTop: 16 }}>
                    {!docType ? (
                      <Spin style={{ margin: 16 }} />
                    ) : (
                      <Space direction="vertical" style={{ width: "100%" }} size={16}>
                        {Object.keys(schemaProperties).length === 0 && (
                          <Typography.Text type="secondary">This document type has no fields to review.</Typography.Text>
                        )}
                        {Object.entries(schemaProperties).map(([key, field]) => {
                          const mark = marks[key] ?? { status: "accepted" as QcFieldStatus };
                          return (
                            <div key={key}>
                              <Space align="center" style={{ marginBottom: 6 }}>
                                <Typography.Text strong>{field.title || key}</Typography.Text>
                                {field["x-critical"] && <Tag color="red">Critical</Tag>}
                              </Space>
                              <br />
                              <Segmented
                                disabled={!inProgress}
                                value={mark.status}
                                onChange={(status) =>
                                  setMarks((prev) => ({ ...prev, [key]: { ...prev[key], status: status as QcFieldStatus } }))
                                }
                                options={[
                                  { label: "Accepted", value: "accepted" },
                                  { label: "Defective", value: "defective" },
                                ]}
                              />
                              {mark.status === "defective" && (
                                <Input
                                  disabled={!inProgress}
                                  placeholder="Describe the error (shown on the inspection report)"
                                  style={{ marginTop: 8 }}
                                  value={mark.note ?? ""}
                                  onChange={(e) =>
                                    setMarks((prev) => ({ ...prev, [key]: { ...prev[key], note: e.target.value } }))
                                  }
                                />
                              )}
                            </div>
                          );
                        })}
                        {inProgress && (
                          <Button type="primary" loading={saveMutation.isPending} onClick={() => saveMutation.mutate()}>
                            {item.status === "pending" ? "Save review & next" : "Update review"}
                          </Button>
                        )}
                      </Space>
                    )}
                  </Card>
                </div>
              }
            />
          )}
        </div>
      </div>

      <Modal
        title={`Complete inspection #${inspection.attempt_number}`}
        open={completeOpen}
        onCancel={() => setCompleteOpen(false)}
        onOk={() => completeMutation.mutate()}
        confirmLoading={completeMutation.isPending}
        okText={wouldPass ? "Complete — accept lot" : "Complete — reject lot"}
        okButtonProps={{ danger: !wouldPass }}
      >
        <Typography.Paragraph>
          {critical} critical and {minor} non-critical non-conformities across {nonconformingRecords} of{" "}
          {inspection.sample_size} sampled records (Ac {inspection.acceptance_number}, Re {inspection.rejection_number}).
        </Typography.Paragraph>
        <Alert
          type={wouldPass ? "success" : "error"}
          style={{ marginBottom: 12 }}
          message={
            wouldPass
              ? "The lot will be accepted for release."
              : critical > 0
                ? "The lot will be rejected — critical-field defects fail it regardless of Ac."
                : "The lot will be rejected — non-conformities reach the rejection number."
          }
          description={
            nonconformingRecords > 0
              ? `${nonconformingRecords} non-conforming record(s) will be sent back to indexing for rework. ` +
                (wouldPass
                  ? "Release waits until they're back through QA."
                  : "Start a new inspection once they're back through QA.")
              : undefined
          }
        />
        <Input.TextArea
          rows={3}
          maxLength={2048}
          placeholder="Summary note for the report (optional)"
          value={summaryNote}
          onChange={(e) => setSummaryNote(e.target.value)}
        />
      </Modal>
    </div>
  );
}
