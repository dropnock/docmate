import {
  Alert, Button, Col, Divider, Drawer, Empty, Form, Input, InputNumber, Modal, Popconfirm, Radio, Row,
  Select, Space, Spin, Table, Tag, Tooltip, Typography, message,
} from "antd";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnType } from "antd/es/table";
import api from "@shared/api/client";
import { formatApiError } from "@shared/api/errors";
import {
  listInspections, lotHistoryKey, lotInspectionsKey, recallLot, removeLotRecords, resubmitLot, startInspection,
} from "@shared/api/lots";
import { useCabinets } from "@shared/hooks/useCabinets";
import LotHistory from "@shared/components/LotHistory";
import LotInspectionReport from "@shared/components/LotInspectionReport";
import LotQcRounds from "@shared/components/LotQcRounds";
import StatusDot from "@shared/components/StatusDot";
import HelpTip from "@shared/components/HelpTip";
import InspectionLevelTag, { INSPECTION_LEVEL } from "@shared/components/InspectionLevelTag";
import LotInspectionWorkspace from "../components/LotInspectionWorkspace";
import type { CabinetRecord, InspectionLevel, Lot, LotDetail, LotInspectionStatus } from "@shared/types";

const INSPECTION_LEVELS: InspectionLevel[] = ["normal", "tightened", "reduced"];

interface Props {
  projectId: number;
}

const LOT_STATUS_LABEL: Record<string, string> = {
  draft: "Draft",
  released: "Released",
  qc_in_progress: "QC In Progress",
  passed: "Passed",
  failed: "Failed",
  remediation: "Remediation",
};

const LOT_STATUS_FILLED = new Set(["passed"]);

const INSPECTION_TAG: Record<LotInspectionStatus, { color: string; label: string }> = {
  in_progress: { color: "blue", label: "In progress" },
  passed: { color: "green", label: "Passed" },
  failed: { color: "red", label: "Failed" },
  cancelled: { color: "default", label: "Cancelled" },
  superseded: { color: "default", label: "Superseded" },
};

export default function LotManager({ projectId }: Props) {
  const qc = useQueryClient();
  const [createOpen, setCreateOpen] = useState(false);
  const [detailLotId, setDetailLotId] = useState<number | undefined>();
  const [selectedRecordIds, setSelectedRecordIds] = useState<number[]>([]);
  const [lotName, setLotName] = useState("");
  const [lotDescription, setLotDescription] = useState("");
  const [levelChoice, setLevelChoice] = useState<InspectionLevel | undefined>();
  const [workspaceInspectionId, setWorkspaceInspectionId] = useState<number | undefined>();
  const [reportOpen, setReportOpen] = useState(false);
  const [recallOpen, setRecallOpen] = useState(false);
  const [recallReason, setRecallReason] = useState("");
  const [removeSelection, setRemoveSelection] = useState<number[]>([]);

  const { data: lots = [], isLoading: lotLoading } = useQuery<Lot[]>({
    queryKey: ["lots", projectId],
    queryFn: () => api.get(`/lots/project/${projectId}`).then((r) => r.data),
    refetchInterval: 15_000,
  });

  // One cabinet per project — auto-load
  const { data: cabinets = [] } = useCabinets(projectId);
  const cabinet = cabinets[0];

  // QA-passed records in the project cabinet
  const { data: qaPassedRecords = [], isLoading: recLoading } = useQuery<CabinetRecord[]>({
    queryKey: ["cabinet-records", cabinet?.id, "qa_passed", "not_in_lot"],
    queryFn: () =>
      api.get(`/cabinets/${cabinet!.id}/records?status=qa_passed&not_in_lot=true`).then((r) => r.data),
    enabled: !!cabinet && createOpen,
  });

  // The "Records in lot" spinner: growing it adds the next unselected records
  // in list order to whatever is already picked; shrinking it drops the
  // selected records furthest down the list. Ticking rows moves it too.
  const setRecordCount = (n: number) => {
    const want = Math.max(0, Math.min(Math.floor(n), qaPassedRecords.length));
    const picked = new Set(selectedRecordIds);
    if (want > picked.size) {
      for (const r of qaPassedRecords) {
        if (picked.size >= want) break;
        picked.add(r.id);
      }
    } else {
      const inOrder = qaPassedRecords.filter((r) => picked.has(r.id)).map((r) => r.id);
      inOrder.slice(want).forEach((id) => picked.delete(id));
    }
    setSelectedRecordIds(qaPassedRecords.filter((r) => picked.has(r.id)).map((r) => r.id));
  };

  const { data: lotDetail } = useQuery<LotDetail>({
    queryKey: ["lot-detail", detailLotId],
    queryFn: () => api.get(`/lots/${detailLotId}`).then((r) => r.data),
    enabled: !!detailLotId,
    refetchInterval: 10_000,
  });

  const { data: inspectionList } = useQuery({
    queryKey: lotInspectionsKey(detailLotId),
    queryFn: () => listInspections(detailLotId!),
    enabled: !!detailLotId,
  });
  const inspections = inspectionList?.inspections ?? [];
  const activeInspection = inspections.find((i) => i.status === "in_progress");
  // Latest attempt that wasn't cancelled or superseded — the one release is
  // gated on (lot_inspection_service.assert_releasable).
  const latestEffective = [...inspections].reverse().find((i) => i.status !== "cancelled" && i.status !== "superseded");
  const inspectionPassed = latestEffective?.status === "passed";
  const recordsInRework = (lotDetail?.records ?? []).filter((r) => r.status !== "qa_passed").length;
  // A remediated lot can be resubmitted once nothing is still in rework:
  // records the customer passed stay qc_passed until resubmit resets them.
  const recordsAwaitingRework = (lotDetail?.records ?? [])
    .filter((r) => r.status !== "qa_passed" && r.status !== "qc_passed").length;

  const startInspectionMutation = useMutation({
    mutationFn: () => startInspection(detailLotId!, levelChoice ?? inspectionList!.suggested_level),
    onSuccess: (inspection) => {
      message.success(`Random sample of ${inspection.sample_size} record(s) drawn`);
      qc.invalidateQueries({ queryKey: lotInspectionsKey(detailLotId) });
      setWorkspaceInspectionId(inspection.id);
    },
    onError: (err: unknown) => message.error(formatApiError(err, "Failed to start inspection")),
  });

  const createMutation = useMutation({
    mutationFn: () =>
      api.post("/lots", {
        project_id: projectId,
        name: lotName,
        description: lotDescription || undefined,
        record_ids: selectedRecordIds,
      }),
    onSuccess: () => {
      message.success("Lot created");
      qc.invalidateQueries({ queryKey: ["lots", projectId] });
      setCreateOpen(false);
      setLotName("");
      setLotDescription("");
      setSelectedRecordIds([]);
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(err.response?.data?.detail ?? "Failed to create lot");
    },
  });

  const releaseMutation = useMutation({
    mutationFn: (lotId: number) => api.post(`/lots/${lotId}/release`),
    onSuccess: () => {
      message.success("Lot released to customer");
      qc.invalidateQueries({ queryKey: ["lots", projectId] });
      qc.invalidateQueries({ queryKey: ["lot-detail", detailLotId] });
    },
    onError: (err: unknown) => message.error(formatApiError(err, "Failed to release lot")),
  });

  const invalidateLot = () => {
    qc.invalidateQueries({ queryKey: ["lots", projectId] });
    qc.invalidateQueries({ queryKey: ["lot-detail", detailLotId] });
    qc.invalidateQueries({ queryKey: lotInspectionsKey(detailLotId) });
    qc.invalidateQueries({ queryKey: lotHistoryKey(detailLotId) });
  };

  const recallMutation = useMutation({
    mutationFn: () => recallLot(detailLotId!, recallReason.trim()),
    onSuccess: () => {
      message.success("Lot recalled — it's a draft again and needs a fresh inspection");
      setRecallOpen(false);
      setRecallReason("");
      invalidateLot();
    },
    onError: (err: unknown) => message.error(formatApiError(err, "Failed to recall lot")),
  });

  const resubmitMutation = useMutation({
    mutationFn: () => resubmitLot(detailLotId!),
    onSuccess: () => {
      message.success("Lot resubmitted — inspect it again before releasing");
      invalidateLot();
    },
    onError: (err: unknown) => message.error(formatApiError(err, "Failed to resubmit lot")),
  });

  const removeRecordsMutation = useMutation({
    mutationFn: () => removeLotRecords(detailLotId!, removeSelection),
    onSuccess: () => {
      message.success(`${removeSelection.length} record(s) removed from the lot`);
      setRemoveSelection([]);
      invalidateLot();
    },
    onError: (err: unknown) => message.error(formatApiError(err, "Failed to remove records")),
  });

  const columns: ColumnType<Lot>[] = [
    { title: "ID", dataIndex: "id", width: 60 },
    { title: "Name", dataIndex: "name" },
    {
      title: "Status",
      dataIndex: "status",
      render: (s: string) => (
        <StatusDot filled={LOT_STATUS_FILLED.has(s)} label={LOT_STATUS_LABEL[s] ?? s.replace(/_/g, " ")} />
      ),
    },
    {
      title: "Inspection level",
      dataIndex: "inspection_level",
      render: (v: InspectionLevel | null) => <InspectionLevelTag level={v} />,
    },
    {
      title: "Accuracy",
      dataIndex: "accuracy_rate",
      render: (v: number | null) =>
        v !== null ? `${(v * 100).toFixed(1)}%` : "—",
    },
    {
      title: "Actions",
      key: "actions",
      render: (_: unknown, lot: Lot) => (
        <Space>
          <Button size="small" onClick={() => setDetailLotId(lot.id)}>
            View
          </Button>
          {lot.status === "draft" && (
            <Button size="small" type="primary" onClick={() => setDetailLotId(lot.id)}>
              Inspect &amp; Release
            </Button>
          )}
          {lot.status !== "draft" && (
            <Button
              size="small"
              onClick={() => {
                setDetailLotId(lot.id);
                setReportOpen(true);
              }}
            >
              Report
            </Button>
          )}
        </Space>
      ),
    },
  ];

  const recordColumns: ColumnType<LotDetail["records"][0]>[] = [
    { title: "Record ID", dataIndex: "record_id", width: 90 },
    { title: "Identifier", dataIndex: "source_identifier", render: (v) => v ?? "—" },
    { title: "Filename", dataIndex: "original_filename", render: (v) => v ?? "—" },
    {
      title: "Status",
      dataIndex: "status",
      render: (s: string) => <Tag>{s.replace(/_/g, " ")}</Tag>,
    },
    {
      title: "Sampled",
      dataIndex: "is_sampled",
      render: (v: boolean) => v ? <StatusDot filled label="Yes" /> : "—",
    },
  ];

  return (
    <div>
      <Row justify="space-between" align="middle" style={{ marginBottom: 16 }}>
        <Col>
          <Typography.Title level={4} style={{ margin: 0 }}>Lots</Typography.Title>
        </Col>
        <Col>
          <Button type="primary" onClick={() => setCreateOpen(true)}>
            Create Lot
          </Button>
        </Col>
      </Row>

      {lotLoading ? (
        <Spin />
      ) : lots.length === 0 ? (
        <Empty description="No lots yet. Create one from QA-passed cabinet records." />
      ) : (
        <Table
          rowKey="id"
          columns={columns}
          dataSource={lots}
          size="middle"
          pagination={{ defaultPageSize: 20 }}
        />
      )}

      {/* Create Lot Drawer */}
      <Drawer
        title="Create Lot"
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        width={560}
        extra={
          <Button
            type="primary"
            loading={createMutation.isPending}
            disabled={!lotName || selectedRecordIds.length === 0}
            onClick={() => createMutation.mutate()}
          >
            Create ({selectedRecordIds.length} records)
          </Button>
        }
      >
        <Form layout="vertical">
          <Form.Item label="Lot Name" required>
            <Input value={lotName} onChange={(e) => setLotName(e.target.value)} />
          </Form.Item>
          <Form.Item label="Description">
            <Input.TextArea rows={2} value={lotDescription} onChange={(e) => setLotDescription(e.target.value)} />
          </Form.Item>
        </Form>

        {recLoading ? (
          <Spin />
        ) : qaPassedRecords.length === 0 ? (
          <Empty description="No QA-passed records in this project's cabinet yet." />
        ) : (
          <>
            <Row justify="space-between" align="middle" style={{ marginBottom: 8 }}>
              <Space>
                <Typography.Text>Records in lot</Typography.Text>
                <InputNumber
                  aria-label="Records in lot"
                  min={0}
                  max={qaPassedRecords.length}
                  precision={0}
                  value={selectedRecordIds.length}
                  onChange={(v) => setRecordCount(v ?? 0)}
                  style={{ width: 100 }}
                />
                <Typography.Text type="secondary">of {qaPassedRecords.length} QA-passed available</Typography.Text>
              </Space>
              <Space>
                <Button
                  size="small"
                  onClick={() => setSelectedRecordIds(qaPassedRecords.map((r) => r.id))}
                >
                  Select All
                </Button>
                <Button size="small" onClick={() => setSelectedRecordIds([])}>
                  Clear
                </Button>
              </Space>
            </Row>
            <Table
              rowKey="id"
              size="small"
              dataSource={qaPassedRecords}
              pagination={{ defaultPageSize: 20 }}
              rowSelection={{
                selectedRowKeys: selectedRecordIds,
                onChange: (keys) => setSelectedRecordIds(keys as number[]),
              }}
              columns={[
                { title: "ID", dataIndex: "id", width: 70 },
                { title: "Identifier", dataIndex: "source_identifier", render: (v) => v ?? "—" },
                { title: "Filename", dataIndex: "original_filename", render: (v) => v ?? "—" },
              ]}
            />
          </>
        )}
      </Drawer>

      {/* Lot Detail Drawer */}
      <Drawer
        title={lotDetail ? `Lot: ${lotDetail.name}` : "Lot Detail"}
        open={!!detailLotId}
        onClose={() => {
          setDetailLotId(undefined);
          setLevelChoice(undefined);
          setReportOpen(false);
          setRemoveSelection([]);
        }}
        width={720}
      >
        {!lotDetail ? (
          <Spin />
        ) : (
          <>
            <Row gutter={16} style={{ marginBottom: 16 }}>
              <Col>
                <StatusDot
                  filled={LOT_STATUS_FILLED.has(lotDetail.status)}
                  label={LOT_STATUS_LABEL[lotDetail.status] ?? lotDetail.status.replace(/_/g, " ")}
                />
              </Col>
              {lotDetail.accuracy_rate !== null && (
                <Col>
                  <Typography.Text strong>
                    Accuracy: {(lotDetail.accuracy_rate * 100).toFixed(1)}%
                  </Typography.Text>
                </Col>
              )}
              {lotDetail.status === "draft" && (
                <Col>
                  <Tooltip
                    title={
                      !inspectionPassed
                        ? "The lot must pass an outgoing inspection first"
                        : recordsInRework > 0
                          ? `${recordsInRework} record(s) still in rework`
                          : undefined
                    }
                  >
                    <Button
                      type="primary"
                      size="small"
                      disabled={!inspectionPassed || recordsInRework > 0}
                      loading={releaseMutation.isPending}
                      onClick={() => releaseMutation.mutate(lotDetail.id)}
                    >
                      Release to Customer
                    </Button>
                  </Tooltip>
                </Col>
              )}
              {lotDetail.status === "remediation" && (
                <Col>
                  <Popconfirm
                    title="Resubmit this lot?"
                    description="The failed QC round is archived and the lot goes back to draft. It needs a fresh outgoing inspection, and the customer samples it again."
                    okText="Resubmit"
                    disabled={recordsAwaitingRework > 0}
                    onConfirm={() => resubmitMutation.mutate()}
                  >
                    <Tooltip title={recordsAwaitingRework > 0 ? `${recordsAwaitingRework} record(s) still in rework` : undefined}>
                      <Button
                        type="primary"
                        size="small"
                        disabled={recordsAwaitingRework > 0}
                        loading={resubmitMutation.isPending}
                      >
                        Resubmit
                      </Button>
                    </Tooltip>
                  </Popconfirm>
                </Col>
              )}
              {lotDetail.status === "released" && lotDetail.sample_size === null && (
                <Col>
                  <Tooltip title="Take the lot back before the customer samples it">
                    <Button danger size="small" onClick={() => setRecallOpen(true)}>
                      Recall
                    </Button>
                  </Tooltip>
                </Col>
              )}
            </Row>

            {lotDetail.status === "remediation" && recordsAwaitingRework > 0 && (
              <Alert
                type="warning"
                style={{ marginBottom: 12 }}
                message={`The customer failed this lot. ${recordsAwaitingRework} rejected record(s) are back with QA; resubmit once they pass.`}
              />
            )}
            <LotQcRounds rounds={lotDetail.qc_rounds ?? []} currentRound={lotDetail.qc_round} />

            <Divider orientation="left" style={{ marginTop: 0 }}>
              Outgoing inspection (ISO 2859-1)
              <HelpTip>
                Starting an inspection draws a random ISO 2859-1 sample of the lot for you to review. The lot can only
                be released once an inspection passes.
              </HelpTip>
            </Divider>
            {lotDetail.status === "draft" && !activeInspection && !inspectionPassed && (
              recordsInRework > 0 ? (
                <Alert
                  type="warning"
                  style={{ marginBottom: 12 }}
                  message={`${recordsInRework} record(s) are in rework — re-inspect once they're back through QA.`}
                />
              ) : (
                <Space direction="vertical" style={{ marginBottom: 12, width: "100%" }}>
                  <Space wrap align="center">
                    <Typography.Text>Inspection level</Typography.Text>
                    <HelpTip>
                      Sets which ISO 2859-1 table this lot is sampled from, for your inspection and for the
                      customer's QC. The suggestion follows the standard's switching rules from the customer's
                      recent lot results; you can choose another level. AQL {inspectionList?.aql_level} and
                      general inspection level {inspectionList?.general_level} (project settings).
                    </HelpTip>
                  </Space>
                  <Radio.Group
                    value={levelChoice ?? inspectionList?.suggested_level}
                    onChange={(e) => setLevelChoice(e.target.value)}
                  >
                    {INSPECTION_LEVELS.map((lvl) => {
                      const plan = inspectionList?.level_plans[lvl];
                      return (
                        <Radio.Button key={lvl} value={lvl} style={{ height: "auto", lineHeight: 1.4, padding: "4px 12px" }}>
                          <div>
                            {INSPECTION_LEVEL[lvl].label}
                            {inspectionList?.suggested_level === lvl && (
                              <Tag color="gold" style={{ marginLeft: 6, marginRight: 0 }}>Suggested</Tag>
                            )}
                          </div>
                          {plan && (
                            <div style={{ fontSize: 12, opacity: 0.75 }}>
                              n={plan.sample_size}, Ac {plan.acceptance_number}/Re {plan.rejection_number}
                            </div>
                          )}
                        </Radio.Button>
                      );
                    })}
                  </Radio.Group>
                  {levelChoice && inspectionList && levelChoice !== inspectionList.suggested_level && (
                    <Typography.Text type="warning">
                      You're overriding the suggested level ({INSPECTION_LEVEL[inspectionList.suggested_level].label});
                      the choice is recorded on the lot's audit trail.
                    </Typography.Text>
                  )}
                  <Button
                    type="primary"
                    loading={startInspectionMutation.isPending}
                    disabled={!inspectionList}
                    onClick={() => startInspectionMutation.mutate()}
                  >
                    {inspections.length ? "Re-inspect (new random sample)" : "Start inspection"}
                  </Button>
                </Space>
              )
            )}
            {lotDetail.status === "draft" && inspectionPassed && recordsInRework > 0 && (
              <Alert
                type="info"
                style={{ marginBottom: 12 }}
                message={`Inspection passed — release unlocks once the ${recordsInRework} reworked record(s) are back through QA.`}
              />
            )}
            {inspections.length === 0 ? (
              <Typography.Paragraph type="secondary">
                No inspection yet.
              </Typography.Paragraph>
            ) : (
              <Table
                rowKey="id"
                size="small"
                style={{ marginBottom: 16 }}
                pagination={false}
                dataSource={[...inspections].reverse()}
                columns={[
                  { title: "#", dataIndex: "attempt_number", width: 40 },
                  {
                    title: "Result",
                    dataIndex: "status",
                    render: (s: LotInspectionStatus) => <Tag color={INSPECTION_TAG[s].color}>{INSPECTION_TAG[s].label}</Tag>,
                  },
                  {
                    title: "Level",
                    dataIndex: "inspection_level",
                    render: (v: InspectionLevel, i) => (
                      <span>
                        <InspectionLevelTag level={v} />
                        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                          AQL {i.aql_level} · General level {i.general_level}
                        </Typography.Text>
                      </span>
                    ),
                  },
                  {
                    title: "Plan",
                    key: "plan",
                    render: (_: unknown, i) => `n=${i.sample_size}, Ac ${i.acceptance_number}/Re ${i.rejection_number}`,
                  },
                  {
                    title: "Non-conformities",
                    key: "nc",
                    render: (_: unknown, i) =>
                      i.critical_defect_count === null ? "—" : `${i.critical_defect_count} crit / ${i.minor_defect_count} minor`,
                  },
                  {
                    title: "",
                    key: "open",
                    render: (_: unknown, i) => (
                      <Button size="small" type={i.status === "in_progress" ? "primary" : "default"} onClick={() => setWorkspaceInspectionId(i.id)}>
                        {i.status === "in_progress" ? "Continue review" : "View"}
                      </Button>
                    ),
                  },
                ]}
              />
            )}
            {inspections.length > 0 && (
              <Button style={{ marginBottom: 16 }} onClick={() => setReportOpen(true)}>
                View inspection report
              </Button>
            )}

            <Divider orientation="left">Records</Divider>
            {lotDetail.status === "draft" && !activeInspection && removeSelection.length > 0 && (
              <Popconfirm
                title={`Remove ${removeSelection.length} record(s) from this lot?`}
                description={inspectionPassed
                  ? "The passed inspection will be superseded; the lot must be inspected again."
                  : "The records stay QA-passed and can go into another lot."}
                okText="Remove"
                okButtonProps={{ danger: true }}
                onConfirm={() => removeRecordsMutation.mutate()}
              >
                <Button danger size="small" style={{ marginBottom: 8 }} loading={removeRecordsMutation.isPending}>
                  Remove {removeSelection.length} from lot
                </Button>
              </Popconfirm>
            )}
            <Table
              rowKey="record_id"
              size="small"
              dataSource={lotDetail.records}
              columns={recordColumns}
              pagination={{ defaultPageSize: 20 }}
              rowSelection={lotDetail.status === "draft" && !activeInspection ? {
                selectedRowKeys: removeSelection,
                onChange: (keys) => setRemoveSelection(keys as number[]),
              } : undefined}
            />
            <LotHistory lotId={lotDetail.id} />
          </>
        )}
      </Drawer>

      <Drawer
        open={!!detailLotId && workspaceInspectionId !== undefined}
        onClose={() => setWorkspaceInspectionId(undefined)}
        width="100%"
        closable={false}
        destroyOnClose
        styles={{ body: { padding: 0 } }}
      >
        {detailLotId && workspaceInspectionId !== undefined && (
          <LotInspectionWorkspace
            lotId={detailLotId}
            inspectionId={workspaceInspectionId}
            onClose={() => setWorkspaceInspectionId(undefined)}
          />
        )}
      </Drawer>

      <Modal
        title="Recall lot"
        open={!!detailLotId && recallOpen}
        onCancel={() => setRecallOpen(false)}
        okText="Recall lot"
        okButtonProps={{ danger: true, disabled: !recallReason.trim(), loading: recallMutation.isPending }}
        onOk={() => recallMutation.mutate()}
        destroyOnClose
      >
        <Typography.Paragraph>
          The lot goes back to draft and disappears from the customer portal. Its passed inspection is
          superseded, so it must be inspected again before it can be released.
        </Typography.Paragraph>
        <Form layout="vertical">
          <Form.Item
            label="Reason"
            required
            extra="Kept on the lot's audit trail. The customer sees it in the lot history once the lot is released again."
          >
            <Input.TextArea
              rows={3}
              maxLength={1024}
              value={recallReason}
              onChange={(e) => setRecallReason(e.target.value)}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="Outgoing Inspection Report"
        open={!!detailLotId && reportOpen}
        onCancel={() => setReportOpen(false)}
        footer={null}
        width={960}
        destroyOnClose
      >
        {detailLotId && <LotInspectionReport lotId={detailLotId} />}
      </Modal>
    </div>
  );
}
