import {
  Alert, Button, Card, Empty, Input, Modal, Segmented, Space, Spin, Tag, Tooltip, Typography, message,
} from "antd";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { RJSFSchema } from "@rjsf/utils";
import api from "@shared/api/client";
import { formatApiError } from "@shared/api/errors";
import {
  decideLot, getQcReview, lotDetailKey, overrideQcMark, overrideRecordOutcome, qcReviewKey,
} from "@shared/api/lots";
import { useRecordImage } from "@shared/hooks/useRecordImage";
import OpenSeadragonViewer from "@shared/components/ImageViewer/OpenSeadragonViewer";
import SchemaForm from "@shared/components/SchemaForm";
import SplitWorkspace from "@shared/components/SplitWorkspace";
import {
  ClassResultsTable, ClassTally, ERROR_TYPE_LABEL, FieldMarkControl, SeverityTag, type FieldMark,
} from "@shared/components/DefectClasses";
import { labelFor, reviewFields } from "@shared/reviewFields";
import type { DocumentType, QcReviewField, QcReviewMark, QcReviewRecord, Severity } from "@shared/types";

/** A field's ruling being edited: the mark plus the reason for changing it. */
interface Draft {
  mark: FieldMark;
  reason: string;
}

function sameMark(a: { status: string; error_type?: string | null }, b: { status: string; error_type?: string | null }) {
  return a.status === b.status && (a.status === "accepted" || (a.error_type ?? null) === (b.error_type ?? null));
}

function MarkSummary({ mark }: { mark: QcReviewMark }) {
  if (mark.status === "accepted") return <Tag color="green">Accepted</Tag>;
  return (
    <Space size={4} wrap>
      <Tag color="red">Defective</Tag>
      {mark.error_type && <Typography.Text>{ERROR_TYPE_LABEL[mark.error_type]}</Typography.Text>}
      {mark.severity && <SeverityTag severity={mark.severity as Severity} />}
    </Space>
  );
}

/** The customer QC supervisor's review of a lot whose sample has been fully
 * reviewed: every sampled record with the reviewer's marks, which they may
 * uphold, dismiss or change (with a reason). The counts and the ISO verdict
 * follow their changes; Confirm decision applies the verdict and sends the
 * records still rejected back for rework (lot_service.decide_lot). */
export default function LotQcReview() {
  const { lotId: lotIdParam } = useParams();
  const lotId = Number(lotIdParam);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [selectedId, setSelectedId] = useState<number | undefined>();
  const [showAll, setShowAll] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [recordDraft, setRecordDraft] = useState<{ passed: boolean; reason: string } | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const { data: review, isLoading, error } = useQuery({
    queryKey: qcReviewKey(lotId),
    queryFn: () => getQcReview(lotId),
    enabled: Number.isFinite(lotId),
  });
  const awaiting = review?.status === "awaiting_decision";

  const records = useMemo(() => {
    const all = review?.records ?? [];
    // Records with defects (or that the reviewer rejected) first.
    const flagged = (r: QcReviewRecord) => r.defect_count > 0 || r.reviewer_passed === false || r.overridden;
    const sorted = [...all].sort((a, b) => Number(flagged(b)) - Number(flagged(a)) || a.record_id - b.record_id);
    return showAll ? sorted : sorted.filter(flagged);
  }, [review, showAll]);

  useEffect(() => {
    if (!review || selectedId !== undefined) return;
    setSelectedId(records[0]?.record_id ?? review.records[0]?.record_id);
  }, [review, records, selectedId]);

  const record = review?.records.find((r) => r.record_id === selectedId);

  const { data: docType } = useQuery<DocumentType>({
    queryKey: ["doctype", record?.document_type_id],
    queryFn: () => api.get(`/document-types/${record!.document_type_id}`).then((r) => r.data),
    enabled: !!record?.document_type_id,
  });
  const {
    data: viewData, isLoading: viewLoading, page, setPage, pageCount,
  } = useRecordImage(selectedId, selectedId !== undefined);

  // The record's fields in form order (shared/reviewFields.ts): one per
  // plain field, one per part of each list entry.
  const derivedFields = useMemo(
    () => (docType && record ? reviewFields(docType.json_schema, record.indexed_data) : []),
    [docType, record],
  );

  // Start each record's drafts from the marks that count now.
  const resetDrafts = (r: QcReviewRecord | undefined) => {
    if (!r) return;
    const next: Record<string, Draft> = {};
    for (const f of r.fields) {
      next[f.field_key] = {
        mark: { status: f.effective.status, error_type: f.effective.error_type ?? undefined },
        reason: f.override?.note ?? "",
      };
    }
    setDrafts(next);
    setRecordDraft({ passed: r.status === "qc_passed", reason: "" });
  };
  useEffect(() => {
    resetDrafts(record);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [record?.record_id, review]);

  const changedFields = (record?.fields ?? []).filter((f) => {
    const d = drafts[f.field_key];
    if (!d) return false;
    return !sameMark(d.mark, f.effective) || (f.override !== null && d.reason.trim() !== (f.override.note ?? ""));
  });
  const recordChanged = !!record && !review?.iso && recordDraft !== null &&
    recordDraft.passed !== (record.status === "qc_passed");
  const dirty = changedFields.length > 0 || recordChanged;

  const needsReason = (f: QcReviewField) => {
    const d = drafts[f.field_key];
    return !!d && !sameMark(d.mark, f.reviewer) && !d.reason.trim();
  };
  const missingReason = changedFields.some(needsReason);
  const missingErrorType = !!review?.classes && changedFields.some(
    (f) => drafts[f.field_key].mark.status === "defective" && !drafts[f.field_key].mark.error_type,
  );
  const recordNeedsReason = recordChanged && record!.reviewer_passed !== recordDraft!.passed && !recordDraft!.reason.trim();

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: qcReviewKey(lotId) });
    qc.invalidateQueries({ queryKey: lotDetailKey(lotId) });
    qc.invalidateQueries({ queryKey: ["qc-field-results", lotId] });
  };

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (!record) return;
      if (review?.iso) {
        for (const f of changedFields) {
          const d = drafts[f.field_key];
          await overrideQcMark(lotId, record.record_id, f.field_key, {
            status: d.mark.status,
            error_type: d.mark.status === "defective" ? d.mark.error_type ?? null : null,
            note: d.reason.trim() || null,
          });
        }
      } else if (recordDraft) {
        await overrideRecordOutcome(lotId, record.record_id, recordDraft.passed, recordDraft.reason.trim() || null);
      }
    },
    onSuccess: () => {
      message.success("Changes saved");
      invalidate();
    },
    onError: (e: unknown) => {
      message.error(formatApiError(e, "Couldn't save the changes"));
      invalidate();
    },
  });

  const decideMutation = useMutation({
    mutationFn: () => decideLot(lotId),
    onSuccess: (lot) => {
      setConfirmOpen(false);
      invalidate();
      qc.invalidateQueries({ queryKey: ["customer-lots", lot.project_id] });
      if (lot.status === "passed") message.success("Lot accepted");
      else message.warning("Lot rejected — send it for remediation from the lot's settings");
      navigate(`/lots?project=${lot.project_id}`);
    },
    onError: (e: unknown) => message.error(formatApiError(e, "Couldn't confirm the decision")),
  });

  const selectRecord = (id: number) => {
    if (id === selectedId) return;
    if (!dirty) {
      setSelectedId(id);
      return;
    }
    Modal.confirm({
      title: "Discard unsaved changes?",
      content: "Your changes to this record haven't been saved.",
      okText: "Discard",
      okButtonProps: { danger: true },
      onOk: () => setSelectedId(id),
    });
  };

  if (isLoading) return <Spin style={{ display: "block", margin: "80px auto" }} />;
  if (error || !review) {
    return (
      <div style={{ padding: 24 }}>
        <Alert type="error" showIcon message={formatApiError(error, "Couldn't load the lot's QC results")} />
      </div>
    );
  }

  const rejectedRecords = review.records.filter((r) => r.status === "qc_failed");
  const changedRecords = review.records.filter((r) => r.overridden).length;
  const counts = review.classes
    ? (Object.fromEntries(Object.entries(review.classes).map(([k, c]) => [k, c.found ?? 0])) as Record<Severity, number>)
    : null;
  const verdict = review.accepted ? "pass" : "fail";

  // Form order first; any mark on a field the record no longer has (its data
  // changed, or it was marked as a whole list before fields were split per
  // entry) follows.
  const markedKeys = new Set((record?.fields ?? []).map((f) => f.field_key));
  const derivedKeys = derivedFields.map((f) => f.key).filter((k) => markedKeys.has(k));
  const fieldOrder = [...derivedKeys, ...[...markedKeys].filter((k) => !derivedKeys.includes(k))];
  const emptyByKey = new Map(derivedFields.map((f) => [f.key, f.empty]));

  return (
    <div style={{ height: "calc(100vh - 64px)", display: "flex", flexDirection: "column" }}>
      <div
        style={{
          display: "flex", alignItems: "center", gap: 12, padding: "8px 16px",
          borderBottom: "1px solid #E2E8F0", background: "#FFFFFF", flexWrap: "wrap",
        }}
      >
        <Button size="small" onClick={() => navigate(`/lots?project=${review.project_id}`)}>← Back to lots</Button>
        <Typography.Text strong>QC review: {review.lot_name}</Typography.Text>
        {review.qc_round > 1 && <Tag>Round {review.qc_round}</Tag>}
        {review.classes && counts ? (
          <ClassTally classes={review.classes} counts={counts} />
        ) : review.iso ? (
          <Typography.Text type="secondary">
            {review.any_critical_defect ? "Critical defect found" : `${review.non_critical_defect_total ?? 0} defect(s)`} · Ac{" "}
            {review.acceptance_number}
          </Typography.Text>
        ) : (
          <Typography.Text type="secondary">
            Accuracy {((review.accuracy_rate ?? 0) * 100).toFixed(1)}% (pass at 90%)
          </Typography.Text>
        )}
        <Tag color={review.accepted ? "green" : "red"}>
          {awaiting ? `Lot will ${verdict}` : `Lot ${review.status}`}
        </Tag>
        <Typography.Text type="secondary">
          {rejectedRecords.length} of {review.records.length} records rejected
          {changedRecords > 0 && ` · ${changedRecords} changed by you`}
        </Typography.Text>
        <div style={{ flex: 1 }} />
        {awaiting && (
          <Tooltip title={dirty ? "Save or discard your changes to this record first" : undefined}>
            <Button type="primary" disabled={dirty} onClick={() => setConfirmOpen(true)}>
              Confirm decision
            </Button>
          </Tooltip>
        )}
      </div>

      {!awaiting && (
        <Alert
          type="info"
          banner
          message={
            review.status === "qc_in_progress"
              ? "The sample is still being reviewed. You can check the marks; changes open once every record is reviewed."
              : "This lot has been decided — the marks are read-only."
          }
        />
      )}

      <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
        <div style={{ width: 260, borderRight: "1px solid #E2E8F0", display: "flex", flexDirection: "column", background: "#F8FAFC" }}>
          <div style={{ padding: 8, borderBottom: "1px solid #E2E8F0" }}>
            <Segmented
              block
              size="small"
              value={showAll ? "all" : "defects"}
              onChange={(v) => setShowAll(v === "all")}
              options={[
                { label: "With defects", value: "defects" },
                { label: `All (${review.records.length})`, value: "all" },
              ]}
            />
          </div>
          <div style={{ flex: 1, overflowY: "auto" }}>
            {records.length === 0 && (
              <Typography.Paragraph type="secondary" style={{ padding: 12 }}>
                No defects were found in the sample.
              </Typography.Paragraph>
            )}
            {records.map((r) => (
              <div
                key={r.record_id}
                data-review-record={r.record_id}
                onClick={() => selectRecord(r.record_id)}
                style={{
                  padding: "10px 12px", cursor: "pointer", borderBottom: "1px solid #E2E8F0",
                  background: r.record_id === selectedId ? "#E8EEF6" : undefined,
                }}
              >
                <div style={{ fontWeight: 500 }}>#{r.record_id} {r.source_identifier ?? ""}</div>
                <Space size={4} style={{ marginTop: 4 }} wrap>
                  {r.status === "qc_failed" ? (
                    <Tag color="red">{review.iso ? `${r.defect_count} defect${r.defect_count === 1 ? "" : "s"}` : "Rejected"}</Tag>
                  ) : (
                    <Tag color="green">Passed</Tag>
                  )}
                  {r.overridden && <Tag color="blue">Changed</Tag>}
                </Space>
                {r.reviewer_name && (
                  <div style={{ color: "#64748B", fontSize: 12, marginTop: 2 }}>Reviewed by {r.reviewer_name}</div>
                )}
              </div>
            ))}
          </div>
        </div>

        <div style={{ flex: 1, overflow: "hidden" }}>
          {!record ? (
            <Empty style={{ marginTop: 48 }} description="Select a record" />
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
                      title={`Record ${record.record_id}`}
                    />
                  ) : (
                    <OpenSeadragonViewer imageUrl={viewData.objectUrl} page={page} pageCount={pageCount} onPageChange={setPage} />
                  )
                ) : (
                  <div style={{ padding: 24, color: "#64748B" }}>No image available for this record.</div>
                )
              }
              right={
                <div style={{ padding: 16, overflowY: "auto", height: "100%" }}>
                  <Typography.Title level={5}>Indexed Data</Typography.Title>
                  <Card size="small">
                    {!docType ? (
                      <Spin style={{ margin: 16 }} />
                    ) : (
                      <SchemaForm
                        key={record.record_id}
                        schema={docType.json_schema as RJSFSchema}
                        initialValues={record.indexed_data ?? {}}
                        onSubmit={() => {}}
                        formId={`qc-review-${record.record_id}`}
                        readOnly
                      />
                    )}
                  </Card>

                  {review.iso ? (
                    <Card
                      size="small"
                      title={`Field review${record.reviewer_name ? ` · reviewed by ${record.reviewer_name}` : ""}`}
                      style={{ marginTop: 16 }}
                    >
                      <Space direction="vertical" style={{ width: "100%" }} size={12}>
                        {fieldOrder.map((key) => {
                          const f = record.fields.find((x) => x.field_key === key)!;
                          const d = drafts[key];
                          if (!d) return null;
                          const differs = !sameMark(d.mark, f.reviewer);
                          const flagged = f.reviewer.status === "defective" || d.mark.status === "defective" || !!f.override;
                          return (
                            <div
                              key={key}
                              data-review-field={key}
                              style={{
                                padding: flagged ? "10px 12px" : "4px 12px",
                                borderRadius: 8,
                                background: flagged ? "#FEF2F2" : undefined,
                                border: flagged ? "1px solid #FECACA" : "1px solid transparent",
                              }}
                            >
                              <FieldMarkControl
                                extra={
                                  <div style={{ margin: "2px 0 6px", color: "#475569" }}>
                                    <Space size={6} wrap>
                                      <span>Reviewer:</span>
                                      <MarkSummary mark={f.reviewer} />
                                      {f.reviewer.note && <Typography.Text italic>“{f.reviewer.note}”</Typography.Text>}
                                    </Space>
                                  </div>
                                }
                                title={f.label || labelFor(docType?.json_schema, key)}
                                isIdentifier={f.is_critical}
                                mark={d.mark}
                                onChange={(m) => setDrafts((prev) => ({ ...prev, [key]: { ...prev[key], mark: m } }))}
                                disabled={!awaiting}
                                valueEmpty={emptyByKey.get(key) ?? true}
                                errorClasses={review.error_classes}
                                hideNote
                              />
                              {differs && (
                                <Input
                                  style={{ marginTop: 8 }}
                                  disabled={!awaiting}
                                  status={needsReason(f) ? "warning" : undefined}
                                  placeholder="Reason for changing the reviewer's mark (required)"
                                  value={d.reason}
                                  maxLength={1024}
                                  onChange={(e) => setDrafts((prev) => ({ ...prev, [key]: { ...prev[key], reason: e.target.value } }))}
                                />
                              )}
                              {f.override && f.overridden_by && (
                                <Typography.Text type="secondary" style={{ display: "block", marginTop: 4, fontSize: 12 }}>
                                  Changed by {f.overridden_by}
                                  {f.overridden_at ? ` on ${new Date(f.overridden_at).toLocaleString()}` : ""}
                                </Typography.Text>
                              )}
                            </div>
                          );
                        })}
                      </Space>
                    </Card>
                  ) : (
                    <Card size="small" title="Record result" style={{ marginTop: 16 }}>
                      <Space direction="vertical" style={{ width: "100%" }}>
                        <Space size={6}>
                          <span>Reviewer{record.reviewer_name ? ` (${record.reviewer_name})` : ""}:</span>
                          {record.reviewer_passed ? <Tag color="green">Passed</Tag> : <Tag color="red">Rejected</Tag>}
                        </Space>
                        <Segmented
                          disabled={!awaiting}
                          value={recordDraft?.passed ? "passed" : "rejected"}
                          onChange={(v) => setRecordDraft((prev) => ({ passed: v === "passed", reason: prev?.reason ?? "" }))}
                          options={[{ label: "Passed", value: "passed" }, { label: "Rejected", value: "rejected" }]}
                        />
                        {recordDraft && recordDraft.passed !== record.reviewer_passed && (
                          <Input
                            disabled={!awaiting}
                            placeholder="Reason for changing the reviewer's result (required)"
                            value={recordDraft.reason}
                            maxLength={1024}
                            onChange={(e) => setRecordDraft({ ...recordDraft, reason: e.target.value })}
                          />
                        )}
                      </Space>
                    </Card>
                  )}

                  {awaiting && (
                    <Space style={{ marginTop: 16 }}>
                      <Tooltip
                        title={
                          missingErrorType
                            ? "Choose what is wrong with each defective field"
                            : missingReason || recordNeedsReason
                              ? "Give a reason for each change"
                              : undefined
                        }
                      >
                        <Button
                          type="primary"
                          disabled={!dirty || missingReason || missingErrorType || recordNeedsReason}
                          loading={saveMutation.isPending}
                          onClick={() => saveMutation.mutate()}
                        >
                          Save changes
                        </Button>
                      </Tooltip>
                      <Button disabled={!dirty} onClick={() => resetDrafts(record)}>Discard</Button>
                    </Space>
                  )}
                </div>
              }
            />
          )}
        </div>
      </div>

      <Modal
        title={`Confirm the decision on ${review.lot_name}`}
        open={confirmOpen}
        onCancel={() => setConfirmOpen(false)}
        onOk={() => decideMutation.mutate()}
        confirmLoading={decideMutation.isPending}
        okText={review.accepted ? "Confirm — accept lot" : "Confirm — reject lot"}
        okButtonProps={{ danger: !review.accepted }}
      >
        {review.classes ? (
          <div style={{ marginBottom: 12 }}>
            <ClassResultsTable classes={review.classes} />
          </div>
        ) : review.iso ? (
          <Typography.Paragraph>
            {review.any_critical_defect ? "A critical-field defect was found." : `${review.non_critical_defect_total ?? 0} defect(s) against an acceptance number of ${review.acceptance_number}.`}
          </Typography.Paragraph>
        ) : (
          <Typography.Paragraph>
            Accuracy {((review.accuracy_rate ?? 0) * 100).toFixed(1)}% — the lot passes at 90%.
          </Typography.Paragraph>
        )}
        <Alert
          type={review.accepted ? "success" : "error"}
          message={review.accepted ? "The lot will be accepted." : "The lot will be rejected."}
          description={
            <>
              {rejectedRecords.length > 0
                ? `${rejectedRecords.length} rejected record(s) will be sent back to the digitizing team for rework.`
                : "No records will be sent for rework."}
              {changedRecords > 0 && ` You changed the reviewers' marks on ${changedRecords} record(s); the changes are recorded in each record's history.`}
            </>
          }
        />
      </Modal>
    </div>
  );
}
