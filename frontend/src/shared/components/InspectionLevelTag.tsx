import { Tag } from "antd";
import type { InspectionLevel } from "@shared/types";

/** ISO 2859-1 inspection level of a lot: which sampling table (normal,
 * tightened or reduced) its sample size and Ac/Re came from. */
export const INSPECTION_LEVEL: Record<InspectionLevel, { label: string; color: string; help: string }> = {
  normal: { label: "Normal", color: "blue", help: "Standard sample size and acceptance numbers." },
  tightened: { label: "Tightened", color: "orange", help: "Stricter acceptance numbers, used after rejected lots." },
  reduced: { label: "Reduced", color: "green", help: "Smaller sample, used after a run of accepted lots." },
};

export default function InspectionLevelTag({ level }: { level: InspectionLevel | null | undefined }) {
  if (!level) return <>—</>;
  const l = INSPECTION_LEVEL[level];
  return <Tag color={l.color}>{l.label}</Tag>;
}
