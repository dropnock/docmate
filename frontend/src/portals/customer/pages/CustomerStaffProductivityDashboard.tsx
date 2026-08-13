import { Card, DatePicker, Typography } from "antd";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import api from "@shared/api/client";
import ProductivityTable from "@shared/components/ProductivityTable";
import type { QcStaffMetric } from "@shared/types";
import dayjs from "dayjs";

interface Props {
  projectId: number;
}

export default function CustomerStaffProductivityDashboard({ projectId }: Props) {
  const [date, setDate] = useState(dayjs().format("YYYY-MM-DD"));

  const { data, isLoading } = useQuery<QcStaffMetric[]>({
    queryKey: ["qc-productivity", projectId, date],
    queryFn: () =>
      api
        .get("/analytics/qc-staff-productivity", { params: { project_id: projectId, date } })
        .then((r) => r.data),
    refetchInterval: 30_000,
  });

  return (
    <Card>
      <Typography.Title level={4}>QC Staff Productivity</Typography.Title>
      <div style={{ display: "flex", gap: 12, marginBottom: 16 }}>
        <DatePicker value={dayjs(date)} onChange={(d) => d && setDate(d.format("YYYY-MM-DD"))} />
      </div>
      <ProductivityTable data={data ?? []} loading={isLoading} taskType="qc" />
    </Card>
  );
}
