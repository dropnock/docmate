import { useState } from "react";
import { Button, Empty, Spin, Table, Tooltip } from "antd";
import { useQuery } from "@tanstack/react-query";
import type { ColumnType } from "antd/es/table";
import api from "@shared/api/client";
import PageHeader from "@shared/components/PageHeader";
import StatusDot from "@shared/components/StatusDot";
import LotSettingsDrawer from "../components/LotSettingsDrawer";
import LotAssignQcDrawer from "../components/LotAssignQcDrawer";
import type { Lot } from "@shared/types";

interface Props {
  projectId: number;
  role: string;
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

export default function CustomerLotManager({ projectId, role }: Props) {
  const isSupervisor = role === "customer_supervisor";
  const [settingsLotId, setSettingsLotId] = useState<number | undefined>();
  const [assignLotId, setAssignLotId] = useState<number | undefined>();

  const { data: lots = [], isLoading: lotLoading } = useQuery<Lot[]>({
    queryKey: ["customer-lots", projectId],
    queryFn: () => api.get(`/lots/project/${projectId}`).then((r) => r.data),
    refetchInterval: 15_000,
  });

  const visibleLots = lots.filter((l) =>
    ["released", "qc_in_progress", "passed", "failed", "remediation"].includes(l.status)
  );

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
      title: "Sample",
      dataIndex: "sample_rate",
      render: (v: number | null, row: Lot) =>
        v !== null
          ? `${(v * 100).toFixed(0)}% (${row.sample_size} records)`
          : "—",
    },
    {
      title: "Accuracy",
      dataIndex: "accuracy_rate",
      render: (v: number | null) =>
        v !== null ? (
          <StatusDot filled={v >= 0.9} label={`${(v * 100).toFixed(1)}%`} />
        ) : "—",
    },
    ...(isSupervisor
      ? [{
          title: "",
          key: "action",
          render: (_: unknown, lot: Lot) => (
            <>
              <Button size="small" onClick={() => setSettingsLotId(lot.id)} style={{ marginRight: 8 }}>
                Settings
              </Button>
              <Tooltip title={lot.status === "released" ? "Apply a sample first" : undefined}>
                <Button
                  size="small"
                  disabled={lot.status === "released"}
                  onClick={() => setAssignLotId(lot.id)}
                >
                  Assign to QC
                </Button>
              </Tooltip>
            </>
          ),
        }]
      : []),
  ];

  return (
    <div>
      <PageHeader title="Lots" />

      {lotLoading ? (
        <Spin />
      ) : visibleLots.length === 0 ? (
        <Empty description="No lots released to your organisation yet" />
      ) : (
        <Table
          rowKey="id"
          columns={columns}
          dataSource={visibleLots}
          size="middle"
          pagination={{ pageSize: 20 }}
        />
      )}

      <LotSettingsDrawer
        lotId={settingsLotId}
        projectId={projectId}
        isSupervisor={isSupervisor}
        onClose={() => setSettingsLotId(undefined)}
      />
      <LotAssignQcDrawer
        lotId={assignLotId}
        projectId={projectId}
        onClose={() => setAssignLotId(undefined)}
      />
    </div>
  );
}
