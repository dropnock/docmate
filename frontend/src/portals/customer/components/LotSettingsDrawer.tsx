import { useEffect, useState } from "react";
import {
  Alert, Button, Card, Col, Descriptions, Drawer, Form, InputNumber, Row,
  Segmented, Select, Slider, Spin, Table, Tag, Typography, message,
} from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { formatApiError } from "@shared/api/errors";
import { aqlConfigKey, getAqlConfig, getSampleSizePreview, updateAqlConfig } from "@shared/api/aql";
import { applySample, getQcFieldResults, lotDetailKey, getLot, sendForRemediation } from "@shared/api/lots";
import LotHistory from "@shared/components/LotHistory";
import LotQcRounds from "@shared/components/LotQcRounds";
import StatusDot from "@shared/components/StatusDot";
import InspectionLevelTag from "@shared/components/InspectionLevelTag";
import type { AQLConfig } from "@shared/types";

const AQL_OPTIONS = [0.65, 1.0, 1.5, 2.5].map((v) => ({ value: v, label: `${v}%` }));
const GENERAL_LEVEL_OPTIONS = [
  { value: "I", label: "I (smaller samples)" },
  { value: "II", label: "II (standard)" },
  { value: "III", label: "III (larger samples)" },
];

interface Props {
  lotId: number | undefined;
  projectId: number;
  isSupervisor: boolean;
  onClose: () => void;
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

export default function LotSettingsDrawer({ lotId, projectId, isSupervisor, onClose }: Props) {
  const qc = useQueryClient();
  const [configForm] = Form.useForm<AQLConfig>();
  const [manualRate, setManualRate] = useState<number>(10);

  const { data: lotDetail } = useQuery({
    queryKey: lotDetailKey(lotId),
    queryFn: () => getLot(lotId as number),
    enabled: !!lotId,
    refetchInterval: 10_000,
  });

  const { data: aqlConfig } = useQuery({
    queryKey: aqlConfigKey(projectId),
    queryFn: () => getAqlConfig(projectId),
    enabled: !!lotId,
  });

  useEffect(() => {
    if (aqlConfig) configForm.setFieldsValue(aqlConfig);
  }, [aqlConfig, configForm]);

  const totalRecords = lotDetail?.records.length ?? 0;
  const isIso = aqlConfig?.sampling_mode === "iso";
  const notYetSampled = lotDetail?.status === "released";

  // The level the digitizing supervisor set for this lot; lots inspected
  // before levels were set per lot fall back to the project's suggestion.
  const lotLevel = lotDetail?.inspection_level ?? aqlConfig?.current_status ?? "normal";

  const { data: preview } = useQuery({
    queryKey: ["sample-size-preview", totalRecords, aqlConfig?.current_aql_level, lotLevel, aqlConfig?.general_level],
    queryFn: () =>
      getSampleSizePreview(totalRecords, aqlConfig!.current_aql_level, lotLevel, aqlConfig!.general_level),
    enabled: !!lotDetail && notYetSampled && isIso && totalRecords > 0,
  });

  const { data: fieldResults } = useQuery({
    queryKey: ["qc-field-results", lotId],
    queryFn: () => getQcFieldResults(lotId as number),
    enabled: !!lotId && isIso && !notYetSampled,
    refetchInterval: 10_000,
  });

  const saveConfigMutation = useMutation({
    mutationFn: (values: Partial<AQLConfig>) => updateAqlConfig(projectId, values),
    onSuccess: () => {
      message.success("AQL configuration saved");
      qc.invalidateQueries({ queryKey: aqlConfigKey(projectId) });
    },
    onError: (e: unknown) => message.error(formatApiError(e, "Failed to save AQL configuration")),
  });

  const applySampleMutation = useMutation({
    mutationFn: (rate?: number) => applySample(lotId as number, rate),
    onSuccess: () => {
      message.success("Sample applied — records selected for QC");
      qc.invalidateQueries({ queryKey: lotDetailKey(lotId) });
      qc.invalidateQueries({ queryKey: ["customer-lots", projectId] });
    },
    onError: (e: unknown) => message.error(formatApiError(e, "Failed to apply sample")),
  });

  const remediationMutation = useMutation({
    mutationFn: () => sendForRemediation(lotId as number),
    onSuccess: () => {
      message.success("Lot sent for remediation");
      qc.invalidateQueries({ queryKey: lotDetailKey(lotId) });
      qc.invalidateQueries({ queryKey: ["customer-lots", projectId] });
    },
    onError: () => message.error("Failed to send for remediation"),
  });

  const sampledRecordIds = new Set((lotDetail?.records ?? []).filter((r) => r.is_sampled).map((r) => r.record_id));

  return (
    <Drawer
      title={lotDetail ? `Lot Settings: ${lotDetail.name}` : "Lot Settings"}
      open={!!lotId}
      onClose={onClose}
      width={680}
    >
      {!lotDetail ? (
        <Spin />
      ) : (
        <>
          <Row gutter={8} align="middle" style={{ marginBottom: 16 }}>
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
          </Row>

          {lotDetail.status === "failed" && (
            <Alert
              type="error"
              style={{ marginBottom: 16 }}
              message={
                isIso && fieldResults
                  ? fieldResults.any_critical_defect
                    ? "Lot failed QC — a critical field defect was found."
                    : `Lot failed QC — ${fieldResults.non_critical_defect_total} defect(s) found, exceeding the acceptance number (${fieldResults.acceptance_number}).`
                  : lotDetail.accuracy_rate !== null
                    ? `Lot failed QC (${(lotDetail.accuracy_rate * 100).toFixed(1)}% accuracy — threshold 90%)`
                    : "Lot failed QC."
              }
              action={
                <Button
                  danger
                  size="small"
                  loading={remediationMutation.isPending}
                  onClick={() => remediationMutation.mutate()}
                >
                  Send for Remediation
                </Button>
              }
            />
          )}

          <Card title="AQL Configuration (ISO 2859-1)" style={{ marginBottom: 16 }}>
            {!aqlConfig ? (
              <Spin size="small" />
            ) : (
              <>
                <Descriptions size="small" column={2} bordered style={{ marginBottom: 16 }}>
                  <Descriptions.Item label="Suggested level for next lot" span={2}>
                    <InspectionLevelTag level={aqlConfig.current_status} />
                  </Descriptions.Item>
                  <Descriptions.Item label="Consecutive Passes">{aqlConfig.consecutive_passes}</Descriptions.Item>
                  <Descriptions.Item label="Consecutive Failures">{aqlConfig.consecutive_failures}</Descriptions.Item>
                </Descriptions>

                <Form
                  form={configForm}
                  layout="vertical"
                  disabled={!isSupervisor}
                  onFinish={(values) => saveConfigMutation.mutate(values)}
                >
                  <Form.Item name="sampling_mode" label="Sampling Method">
                    <Segmented
                      options={[
                        { label: "ISO 2859-1 (computed)", value: "iso" },
                        { label: "Manual rate", value: "manual" },
                      ]}
                    />
                  </Form.Item>
                  <Form.Item
                    name="normal_aql"
                    label="Quality requirement (AQL %)"
                    tooltip="The project's acceptance quality limit. Normal, tightened and reduced inspection all use it, with ISO 2859-1's own sample sizes and acceptance numbers for each level."
                  >
                    <Select options={AQL_OPTIONS} style={{ width: 160 }} />
                  </Form.Item>
                  <Form.Item
                    name="general_level"
                    label="General inspection level"
                    tooltip="ISO 2859-1 Table 1 column that turns a lot's size into its sample size code letter. II is the standard's default; III draws larger samples (e.g. a 1,000-record lot is code K instead of J), I smaller ones. Applies to lots inspected from now on."
                  >
                    <Select options={GENERAL_LEVEL_OPTIONS} style={{ width: 200 }} />
                  </Form.Item>
                  <Row gutter={12}>
                    <Col span={12}>
                      <Form.Item name="passes_to_reduce" label="Passes to Reduce" tooltip="Consecutive accepted lots after which the suggested level moves from tightened back to normal, and from normal to reduced. Only a lot's first QC round counts. The digitizing supervisor sets each lot's level.">
                        <InputNumber min={1} style={{ width: "100%" }} />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item name="failures_to_tighten" label="Failures to Tighten" tooltip="Consecutive rejected lots after which the suggested level moves from normal to tightened. Under reduced inspection, any rejected lot suggests normal again.">
                        <InputNumber min={1} style={{ width: "100%" }} />
                      </Form.Item>
                    </Col>
                  </Row>
                  {isSupervisor && (
                    <Button type="primary" htmlType="submit" loading={saveConfigMutation.isPending}>
                      Save Configuration
                    </Button>
                  )}
                </Form>
              </>
            )}
          </Card>

          <Card title="Sample" style={{ marginBottom: 16 }}>
            <Typography.Text>
              Total records in lot: <strong>{totalRecords}</strong>
            </Typography.Text>

            {notYetSampled ? (
              isIso ? (
                <div style={{ marginTop: 12 }}>
                  {preview ? (
                    <Descriptions size="small" column={2} bordered>
                      <Descriptions.Item label="Inspection level" span={2}>
                        <InspectionLevelTag level={lotLevel} />
                      </Descriptions.Item>
                      <Descriptions.Item label="ISO Sample Size">{preview.sample_size}</Descriptions.Item>
                      <Descriptions.Item label="Acceptance Number">{preview.acceptance_number}</Descriptions.Item>
                    </Descriptions>
                  ) : (
                    <Spin size="small" />
                  )}
                  <div style={{ marginTop: 12 }}>
                    <Button
                      type="primary"
                      loading={applySampleMutation.isPending}
                      onClick={() => applySampleMutation.mutate(undefined)}
                    >
                      Apply ISO Sample
                    </Button>
                  </div>
                </div>
              ) : (
                <div style={{ marginTop: 12 }}>
                  <Row align="middle" gutter={16}>
                    <Col flex="auto">
                      <Slider
                        min={1}
                        max={100}
                        value={manualRate}
                        onChange={setManualRate}
                        marks={{ 5: "5%", 10: "10%", 20: "20%", 50: "50%", 100: "100%" }}
                      />
                    </Col>
                    <Col>
                      <InputNumber
                        min={1}
                        max={100}
                        value={manualRate}
                        onChange={(v) => setManualRate(v ?? 10)}
                        formatter={(v) => `${v}%`}
                        parser={(v) => Number(v?.replace("%", "")) as 1}
                        style={{ width: 80 }}
                      />
                    </Col>
                  </Row>
                  <Typography.Text type="secondary">
                    Will select ~{Math.ceil((manualRate / 100) * totalRecords)} records
                  </Typography.Text>
                  <div style={{ marginTop: 12 }}>
                    <Button
                      type="primary"
                      loading={applySampleMutation.isPending}
                      onClick={() => applySampleMutation.mutate(manualRate / 100)}
                    >
                      Apply Sample
                    </Button>
                  </div>
                </div>
              )
            ) : (
              <Descriptions size="small" column={2} bordered style={{ marginTop: 12 }}>
                {lotDetail.aql_status_snapshot && (
                  <Descriptions.Item label="Inspection level" span={2}>
                    <InspectionLevelTag level={lotDetail.aql_status_snapshot} />
                  </Descriptions.Item>
                )}
                <Descriptions.Item label="Sample Rate">
                  {lotDetail.sample_rate !== null ? `${(lotDetail.sample_rate * 100).toFixed(1)}%` : "—"}
                </Descriptions.Item>
                <Descriptions.Item label="Sample Size">{lotDetail.sample_size ?? "—"}</Descriptions.Item>
                {lotDetail.acceptance_number !== null && (
                  <Descriptions.Item label="Acceptance Number" span={2}>
                    {lotDetail.acceptance_number}
                  </Descriptions.Item>
                )}
              </Descriptions>
            )}
          </Card>

          {isIso && !notYetSampled && (
            <Card title="QC Field Defect Tabulation" style={{ marginBottom: 16 }}>
              {!fieldResults ? (
                <Spin size="small" />
              ) : fieldResults.fields.length === 0 ? (
                <Typography.Text type="secondary">No field marks recorded yet.</Typography.Text>
              ) : (
                <>
                  {fieldResults.any_critical_defect && (
                    <Alert
                      type="error"
                      style={{ marginBottom: 12 }}
                      message="A critical field was marked defective — this fails the lot outright, regardless of the acceptance number."
                    />
                  )}
                  <Typography.Text type="secondary">
                    Non-critical defects: {fieldResults.non_critical_defect_total} / acceptance number{" "}
                    {fieldResults.acceptance_number ?? "—"}
                  </Typography.Text>
                  <Table
                    style={{ marginTop: 12 }}
                    rowKey="field_key"
                    size="small"
                    dataSource={fieldResults.fields}
                    pagination={false}
                    columns={[
                      { title: "Field", dataIndex: "field_key" },
                      {
                        title: "Critical",
                        dataIndex: "is_critical",
                        render: (v: boolean) => (v ? <Tag color="red">Critical</Tag> : "—"),
                      },
                      { title: "Defective", dataIndex: "defective_count" },
                      { title: "Accepted", dataIndex: "accepted_count" },
                      {
                        title: "QC Agents",
                        dataIndex: "contributing_agents",
                        render: (agents: { id: number; full_name: string }[]) =>
                          agents.map((a) => a.full_name).join(", ") || "—",
                      },
                    ]}
                  />
                </>
              )}
            </Card>
          )}

          {lotDetail.qc_rounds?.length > 0 && (
            <Card title="Earlier QC rounds" style={{ marginBottom: 16 }}>
              <LotQcRounds rounds={lotDetail.qc_rounds} currentRound={lotDetail.qc_round} />
            </Card>
          )}

          <Card title="Records — which were chosen">
            <Table
              rowKey="record_id"
              size="small"
              dataSource={lotDetail.records}
              pagination={{ defaultPageSize: 20 }}
              rowClassName={(r) => (sampledRecordIds.has(r.record_id) ? "docmate-sampled-row" : "")}
              columns={[
                { title: "Record ID", dataIndex: "record_id", width: 90 },
                { title: "Identifier", dataIndex: "source_identifier", render: (v) => v ?? "—" },
                {
                  title: "Status",
                  dataIndex: "status",
                  render: (s: string) => <Tag>{s.replace(/_/g, " ")}</Tag>,
                },
                {
                  title: "QC Sample",
                  dataIndex: "is_sampled",
                  render: (v: boolean) => (v ? <StatusDot filled label="Sampled" /> : "—"),
                },
              ]}
            />
          </Card>
          <LotHistory lotId={lotDetail.id} />
        </>
      )}
    </Drawer>
  );
}
