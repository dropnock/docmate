import { Badge, Card, Col, Row, Statistic, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import api from "@shared/api/client";
import PathToCompletion from "@shared/components/PathToCompletion";
import type { BurnupPoint, ProjectKPIs } from "@shared/types";

interface Props { projectId: number }

export default function ProjectKPIDashboard({ projectId }: Props) {
  const { data: kpis } = useQuery<ProjectKPIs>({
    queryKey: ["kpis", projectId],
    queryFn: () => api.get(`/analytics/project-kpis/${projectId}`).then((r) => r.data),
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

  return (
    <div>
      <Typography.Title level={4}>Project KPIs</Typography.Title>
      <Row gutter={[16, 16]}>
        <Col span={6}>
          <Card>
            <Statistic title="Completion" value={kpis?.completion_pct} suffix="%" precision={1} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="Records Remaining" value={kpis?.records_remaining} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="Days to Deadline" value={kpis?.days_to_proposed_end ?? "—"} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic
              title="On Track"
              value={kpis?.on_track === null ? "—" : kpis?.on_track ? "Yes" : "No"}
              valueStyle={{ color: kpis?.on_track ? "#3f8600" : "#cf1322" }}
            />
          </Card>
        </Col>

        <Col span={6}>
          <Card>
            <Statistic title="Daily Throughput" value={kpis?.daily_throughput_rate} suffix="rec/day" precision={1} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="Projected End" value={kpis?.projected_end_date ?? "—"} />
          </Card>
        </Col>
        <Col span={6}>
          <Card title="AQL Status">
            <Tag color={aql?.current_status === "tightened" ? "red" : aql?.current_status === "reduced" ? "green" : "blue"}>
              {aql?.current_status?.toUpperCase()}
            </Tag>
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
    </div>
  );
}
