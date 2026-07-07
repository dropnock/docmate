import { Line } from "@ant-design/charts";
import type { BurnupPoint } from "@shared/types";

interface Props {
  data: BurnupPoint[];
  proposedEndDate?: string | null;
  totalRecords?: number;
}

export default function PathToCompletion({ data, proposedEndDate, totalRecords }: Props) {
  // Flatten into two series for the chart
  const chartData = [
    ...data
      .filter((p) => p.completed !== null)
      .map((p) => ({ date: p.date, value: p.completed!, series: "Actual" })),
    ...data
      .filter((p) => p.projected !== null)
      .map((p) => ({ date: p.date, value: p.projected!, series: "Projected" })),
  ];

  const config = {
    data: chartData,
    xField: "date",
    yField: "value",
    seriesField: "series",
    smooth: true,
    annotations: [
      ...(proposedEndDate
        ? [
            {
              type: "line" as const,
              start: [proposedEndDate, "min"] as [string, string],
              end: [proposedEndDate, "max"] as [string, string],
              style: { stroke: "#0F172A", lineDash: [4, 4] },
            },
          ]
        : []),
      ...(totalRecords
        ? [
            {
              type: "line" as const,
              start: ["min", totalRecords] as [string, number],
              end: ["max", totalRecords] as [string, number],
              style: { stroke: "#94A3B8", lineDash: [4, 4] },
            },
          ]
        : []),
    ],
    yAxis: { title: { text: "Records Completed" } },
    xAxis: { title: { text: "Date" } },
  };

  return <Line {...config} />;
}
