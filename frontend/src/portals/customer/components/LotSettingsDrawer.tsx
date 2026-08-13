import { useEffect, useState } from "react";
import {
  Alert, Button, Card, Col, Descriptions, Drawer, Form, InputNumber, Row,
  Segmented, Slider, Spin, Table, Tag, Typography, message,
} from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { formatApiError } from "@shared/api/errors";
import { aqlConfigKey, getAqlConfig, getSampleSizePreview, updateAqlConfig } from "@shared/api/aql";
import { applySample, lotDetailKey, getLot, sendForRemediation } from "@shared/api/lots";
import StatusDot from "@shared/components/StatusDot";
import type { AQLConfig } from "@shared/types";

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

  const { data: preview } = useQuery({
    queryKey: ["sample-size-preview", totalRecords, aqlConfig?.current_aql_level],
    queryFn: () => getSampleSizePreview(totalRecords, aqlConfig!.current_aql_level),
    enabled: !!lotDetail && notYetSampled && isIso && totalRecords > 0,
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

          {lotDetail.accuracy_rate !== null && lotDetail.accuracy_rate < 0.9 && (
            <Alert
              type="error"
              style={{ marginBottom: 16 }}
              message={`Lot failed QC (${(lotDetail.accuracy_rate * 100).toFixed(1)}% accuracy — threshold 90%)`}
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
                  <Descriptions.Item label="Current Status">
                    <Tag>{aqlConfig.current_status.toUpperCase()}</Tag>
                  </Descriptions.Item>
                  <Descriptions.Item label="Current AQL Level">{aqlConfig.current_aql_level}</Descriptions.Item>
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
                  <Row gutter={12}>
                    <Col span={8}>
                      <Form.Item name="normal_aql" label="Normal AQL %">
                        <InputNumber min={0} step={0.1} style={{ width: "100%" }} />
                      </Form.Item>
                    </Col>
                    <Col span={8}>
                      <Form.Item name="tightened_aql" label="Tightened AQL %">
                        <InputNumber min={0} step={0.1} style={{ width: "100%" }} />
                      </Form.Item>
                    </Col>
                    <Col span={8}>
                      <Form.Item name="reduced_aql" label="Reduced AQL %">
                        <InputNumber min={0} step={0.1} style={{ width: "100%" }} />
                      </Form.Item>
                    </Col>
                  </Row>
                  <Row gutter={12}>
                    <Col span={12}>
                      <Form.Item name="passes_to_reduce" label="Passes to Reduce">
                        <InputNumber min={1} style={{ width: "100%" }} />
                      </Form.Item>
                    </Col>
                    <Col span={12}>
                      <Form.Item name="failures_to_tighten" label="Failures to Tighten">
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

          <Card title="Records — which were chosen">
            <Table
              rowKey="record_id"
              size="small"
              dataSource={lotDetail.records}
              pagination={{ pageSize: 20 }}
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
        </>
      )}
    </Drawer>
  );
}
