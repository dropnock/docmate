import { Table, Tag, Typography } from "antd";
import dayjs from "dayjs";
import type { LotQcRound } from "@shared/types";

/** Earlier customer QC rounds of a lot (each one failed, then resubmitted
 * by the digitizing team). Renders nothing for a lot on its first round. */
export default function LotQcRounds({ rounds, currentRound }: { rounds: LotQcRound[]; currentRound: number }) {
  if (rounds.length === 0) return null;
  return (
    <div style={{ marginBottom: 16 }}>
      <Typography.Text type="secondary">
        This lot is on QC round {currentRound}. Earlier rounds:
      </Typography.Text>
      <Table
        rowKey="round_number"
        size="small"
        pagination={false}
        dataSource={rounds}
        style={{ marginTop: 8 }}
        columns={[
          { title: "Round", dataIndex: "round_number", width: 70 },
          {
            title: "Result",
            dataIndex: "status",
            render: (s: string) => <Tag color={s === "passed" ? "green" : "red"}>{s === "passed" ? "Passed" : "Failed"}</Tag>,
          },
          {
            title: "Sample",
            key: "sample",
            render: (_: unknown, r: LotQcRound) => r.sample_size === null ? "—" : `${r.sample_size} of ${r.record_count}`,
          },
          {
            title: "Accuracy",
            dataIndex: "accuracy_rate",
            render: (v: number | null) => (v === null ? "—" : `${(v * 100).toFixed(1)}%`),
          },
          {
            title: "Defects",
            key: "defects",
            render: (_: unknown, r: LotQcRound) =>
              r.critical_defect_count === null ? "—" : `${r.critical_defect_count} crit / ${r.minor_defect_count} minor`,
          },
          {
            title: "QC completed",
            dataIndex: "qc_completed_at",
            render: (v: string | null) => (v ? dayjs(v).format("YYYY-MM-DD HH:mm") : "—"),
          },
        ]}
      />
    </div>
  );
}
