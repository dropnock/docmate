import { Card, Col, Empty, Row, Statistic, Table, Tag, Typography } from "antd";
import dayjs from "dayjs";
import { useQuery } from "@tanstack/react-query";
import type { ColumnType } from "antd/es/table";
import api from "@shared/api/client";
import PathToCompletion from "@shared/components/PathToCompletion";
import PageHeader from "@shared/components/PageHeader";
import PageSkeleton from "@shared/components/PageSkeleton";
import type { BurnupPoint, Lot, ProjectKPIs, QcProjectSummary } from "@shared/types";

interface Props { projectId: number }

const AQL_STATUS_LABEL: Record<string, string> = {
  normal: "Normal",
  tightened: "Tightened",
  reduced: "Reduced",
};

const VISIBLE_LOT_STATUSES = new Set(["released", "qc_in_progress", "passed", "failed", "remediation"]);

export default function CustomerProjectKPIDashboard({ projectId }: Props) {
  const { data: kpis, isLoading } = useQuery<ProjectKPIs>({
    queryKey: ["kpis", projectId],
    queryFn: () => api.get(`/analytics/project-kpis/${projectId}`).then((r) => r.data),
    refetchInterval: 60_000,
  });

  const { data: qcSummary } = useQuery<QcProjectSummary>({
    queryKey: ["qc-summary", projectId],
    queryFn: () => api.get(`/analytics/project-kpis/${projectId}/qc-summary`).then((r) => r.data),
    refetchInterval: 60_000,
  });

  const { data: burnup } = useQuery<BurnupPoint[]>({
    queryKey: ["burnup", projectId],
    queryFn: () => api.get(`/analytics/project-kpis/${projectId}/burnup`).then((r) => r.data),
    refetchInterval: 60_000,
  });

  const { data: aql } = useQuery({
    queryKey: ["aql", projectId],
    queryFn: () => api.get(`/projects/${projectId}/aql`).then((r) => r.data),
    refetchInterval: 60_000,
  });

  const { data: lots = [] } = useQuery<Lot[]>({
    queryKey: ["customer-lots", projectId],
    queryFn: () => api.get(`/lots/project/${projectId}`).then((r) => r.data),
    refetchInterval: 15_000,
  });

  const processedLots = lots
    .filter((l) => VISIBLE_LOT_STATUSES.has(l.status))
    .sort((a, b) => (b.released_at ?? "").localeCompare(a.released_at ?? ""));

  if (isLoading) {
    return (
      <div>
        <PageHeader title="Project KPIs" />
        <PageSkeleton variant="cards" count={8} />
      </div>
    );
  }

  const columns: ColumnType<Lot>[] = [
    { title: "Lot", dataIndex: "name" },
    {
      title: "Released",
      dataIndex: "released_at",
      render: (v: string | null) => (v ? dayjs(v).format("YYYY-MM-DD") : "—"),
    },
    {
      title: "AQL Status",
      dataIndex: "aql_status_snapshot",
      render: (v: string | null) => (v ? <Tag>{AQL_STATUS_LABEL[v] ?? v.toUpperCase()}</Tag> : "—"),
    },
    {
      title: "QC Completed",
      dataIndex: "qc_completed_at",
      render: (v: string | null) => (v ? dayjs(v).format("YYYY-MM-DD HH:mm") : "—"),
    },
    {
      title: "Result",
      dataIndex: "status",
      render: (s: string) =>
        s === "passed" ? (
          <Tag color="green">Passed</Tag>
        ) : s === "failed" || s === "remediation" ? (
          <Tag color="red">Failed</Tag>
        ) : (
          "—"
        ),
    },
    {
      title: "Critical Errors",
      dataIndex: "critical_defect_count",
      render: (v: number | null) => v ?? "—",
    },
    {
      title: "Minor Errors",
      dataIndex: "minor_defect_count",
      render: (v: number | null) => v ?? "—",
    },
  ];

  return (
    <div>
      <PageHeader title="Project KPIs" />
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} md={8} lg={6}>
          <Card>
            <Statistic title="Completion" value={kpis?.completion_pct} suffix="%" precision={1} />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={8} lg={6}>
          <Card>
            <Statistic title="Records Remaining" value={kpis?.records_remaining} />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={8} lg={6}>
          <Card>
            <Statistic title="Days to Deadline" value={kpis?.days_to_proposed_end ?? "—"} />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={8} lg={6}>
          <Card>
            <Statistic
              title="On Track"
              value={kpis?.on_track === null ? "—" : kpis?.on_track ? "Yes" : "No"}
              valueStyle={{ color: "#0F172A" }}
            />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={8} lg={6}>
          <Card>
            <Statistic title="Daily Throughput" value={kpis?.daily_throughput_rate} suffix="rec/day" precision={1} />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={8} lg={6}>
          <Card>
            <Statistic title="Projected End" value={kpis?.projected_end_date ?? "—"} />
          </Card>
        </Col>

        <Col xs={24} sm={12} md={8} lg={6}>
          <Card>
            <Statistic title="Lots Quality Checked" value={qcSummary?.lots_quality_checked} />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={8} lg={6}>
          <Card>
            <Statistic title="Lots Rejected" value={qcSummary?.lots_rejected} />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={8} lg={6}>
          <Card>
            <Statistic title="Records Passed" value={qcSummary?.records_passed} />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={8} lg={6}>
          <Card>
            <Statistic title="QC Daily Throughput" value={qcSummary?.qc_daily_throughput} suffix="rec/day" precision={1} />
          </Card>
        </Col>
        <Col xs={24} sm={12} md={8} lg={6}>
          <Card title="AQL Status">
            <Tag>{aql?.current_status?.toUpperCase()}</Tag>
            <div style={{ marginTop: 8, fontSize: 12 }}>Level: {aql?.current_aql_level}</div>
            <div style={{ fontSize: 12 }}>Consecutive failures: {aql?.consecutive_failures}</div>
          </Card>
        </Col>
      </Row>

      <Card style={{ marginTop: 24 }} title="Path to Completion">
        {burnup && (
          <PathToCompletion
            data={burnup}
            proposedEndDate={kpis?.proposed_end_date}
            totalRecords={kpis?.total_records}
          />
        )}
      </Card>

      <Card style={{ marginTop: 24 }} title="Lots Processed">
        {processedLots.length === 0 ? (
          <Empty description="No lots released yet" />
        ) : (
          <>
            <Typography.Text type="secondary">{processedLots.length} lot(s)</Typography.Text>
            <Table
              style={{ marginTop: 12 }}
              rowKey="id"
              size="small"
              dataSource={processedLots}
              pagination={{ pageSize: 20 }}
              columns={columns}
            />
          </>
        )}
      </Card>
    </div>
  );
}
