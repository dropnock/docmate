import {
  Alert, Button, Col, Divider, Drawer, Empty, Form, Input, Modal, Popconfirm, Row,
  Select, Space, Spin, Table, Tag, Tooltip, Typography, message,
} from "antd";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnType } from "antd/es/table";
import api from "@shared/api/client";
import { formatApiError } from "@shared/api/errors";
import {
  listInspections, lotHistoryKey, lotInspectionsKey, recallLot, removeLotRecords, startInspection,
} from "@shared/api/lots";
import { useCabinets } from "@shared/hooks/useCabinets";
import LotHistory from "@shared/components/LotHistory";
import LotInspectionReport from "@shared/components/LotInspectionReport";
import StatusDot from "@shared/components/StatusDot";
import LotInspectionWorkspace from "../components/LotInspectionWorkspace";
import type { CabinetRecord, Lot, LotDetail, LotInspectionStatus } from "@shared/types";

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
  const [aqlChoice, setAqlChoice] = useState<number | undefined>();
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
    queryKey: ["cabinet-records", cabinet?.id, "qa_passed"],
    queryFn: () =>
      api.get(`/cabinets/${cabinet!.id}/records?status=qa_passed`).then((r) => r.data),
    enabled: !!cabinet && createOpen,
  });

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

  const startInspectionMutation = useMutation({
    mutationFn: () => startInspection(detailLotId!, aqlChoice ?? inspectionList!.default_aql_level),
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
          pagination={{ pageSize: 20 }}
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
            <Row justify="space-between" style={{ marginBottom: 8 }}>
              <Typography.Text type="secondary">
                {qaPassedRecords.length} QA-passed records available
              </Typography.Text>
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
              pagination={{ pageSize: 20 }}
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
          setAqlChoice(undefined);
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

            <Divider orientation="left" style={{ marginTop: 0 }}>Outgoing inspection (ISO 2859-1)</Divider>
            {lotDetail.status === "draft" && !activeInspection && !inspectionPassed && (
              recordsInRework > 0 ? (
                <Alert
                  type="warning"
                  style={{ marginBottom: 12 }}
                  message={`${recordsInRework} record(s) are in rework — re-inspect once they're back through QA.`}
                />
              ) : (
                <Space style={{ marginBottom: 12 }} wrap>
                  <Typography.Text>AQL</Typography.Text>
                  <Select
                    style={{ width: 110 }}
                    value={aqlChoice ?? inspectionList?.default_aql_level}
                    onChange={setAqlChoice}
                    options={(inspectionList?.available_aql_levels ?? []).map((v) => ({ value: v, label: String(v) }))}
                  />
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
                No inspection yet. Starting one draws a random ISO 2859-1 sample for you to review.
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
                  { title: "AQL", dataIndex: "aql_level" },
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
              pagination={{ pageSize: 20 }}
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
