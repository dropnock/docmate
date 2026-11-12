import { Collapse, Empty, Spin } from "antd";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getLotHistory, lotHistoryKey } from "@shared/api/lots";
import RecordTimeline from "@shared/components/RecordTimeline";

/** Collapsed "Lot history" panel for the lot drawers in both portals. The
 * audit trail is only fetched once the panel is opened. */
export default function LotHistory({ lotId }: { lotId: number }) {
  const [open, setOpen] = useState(false);
  const { data: events, isLoading } = useQuery({
    queryKey: lotHistoryKey(lotId),
    queryFn: () => getLotHistory(lotId),
    enabled: open,
  });

  return (
    <Collapse
      style={{ marginTop: 16 }}
      onChange={(keys) => setOpen((Array.isArray(keys) ? keys : [keys]).includes("history"))}
      items={[{
        key: "history",
        label: "Lot history",
        children: isLoading ? <Spin /> : events && events.length > 0
          ? <RecordTimeline events={events} />
          : <Empty description="No history yet" image={Empty.PRESENTED_IMAGE_SIMPLE} />,
      }]}
    />
  );
}
