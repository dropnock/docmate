import { Card, DatePicker, Select, Tabs, Typography } from "antd";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import api from "@shared/api/client";
import ProductivityTable from "@shared/components/ProductivityTable";
import type { Shift, StaffMetric } from "@shared/types";
import dayjs from "dayjs";

interface Props { projectId: number }

export default function StaffProductivityDashboard({ projectId }: Props) {
  const [shiftId, setShiftId] = useState<number | undefined>();
  const [date, setDate] = useState(dayjs().format("YYYY-MM-DD"));

  const { data: shifts } = useQuery<Shift[]>({
    queryKey: ["shifts"],
    queryFn: () => api.get("/shifts").then((r) => r.data),
  });

  const { data, isLoading } = useQuery<StaffMetric[]>({
    queryKey: ["productivity", projectId, shiftId, date],
    queryFn: () =>
      api
        .get("/analytics/staff-productivity", { params: { project_id: projectId, shift_id: shiftId, date } })
        .then((r) => r.data),
    refetchInterval: 30_000,
  });

  return (
    <Card>
      <Typography.Title level={4}>Staff Productivity</Typography.Title>
      <div style={{ display: "flex", gap: 12, marginBottom: 16 }}>
        <Select
          placeholder="Filter by shift"
          allowClear
          style={{ width: 200 }}
          options={shifts?.map((s) => ({ label: s.name, value: s.id }))}
          onChange={setShiftId}
        />
        <DatePicker
          value={dayjs(date)}
          onChange={(d) => d && setDate(d.format("YYYY-MM-DD"))}
        />
      </div>
      <Tabs
        items={[
          {
            key: "indexing",
            label: "Indexing",
            children: <ProductivityTable data={data ?? []} loading={isLoading} taskType="indexing" />,
          },
          {
            key: "qa",
            label: "QA",
            children: <ProductivityTable data={data ?? []} loading={isLoading} taskType="qa" />,
          },
        ]}
      />
    </Card>
  );
}
