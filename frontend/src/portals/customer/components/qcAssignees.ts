import type { AvailableStaff } from "@shared/types";

/** Options for picking who gets QC work: QC agents first, then the
 * customer's supervisors (who can take batches themselves), "you" marked. */
export function qcAssigneeOptions(staff: AvailableStaff[], meId?: number) {
  const label = (s: AvailableStaff) =>
    `${s.full_name}${s.id === meId ? " (you)" : ""}`;
  const agents = staff.filter((s) => s.role !== "customer_supervisor");
  const supervisors = staff.filter((s) => s.role === "customer_supervisor");
  return [
    ...(agents.length ? [{ label: "QC agents", options: agents.map((s) => ({ label: label(s), value: s.id })) }] : []),
    ...(supervisors.length
      ? [{ label: "Supervisors", options: supervisors.map((s) => ({ label: label(s), value: s.id })) }]
      : []),
  ];
}

export function qcAssigneeName(staff: AvailableStaff[], id: number, meId?: number) {
  const s = staff.find((x) => x.id === id);
  if (!s) return String(id);
  return `${s.full_name}${id === meId ? " (you)" : ""}`;
}
