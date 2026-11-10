import { useRef } from "react";
import { Alert, Button, Empty, Spin } from "antd";
import { useQuery } from "@tanstack/react-query";
import { getInspectionReport, lotInspectionReportKey } from "@shared/api/lots";
import { formatApiError } from "@shared/api/errors";
import type { LotInspectionDetail, LotInspectionStatus } from "@shared/types";

/** The digitizing supervisor's outgoing ISO 2859-1 inspection report for a
 * lot, shared by both portals (the customer sees it once the lot is
 * released — see lots.py's get_inspection_report). Rendered as plain
 * HTML tables with inline styles rather than antd components so that
 * "Print / Save as PDF" — which copies this markup into a bare window —
 * prints exactly what's on screen. */

const STATUS_LABEL: Record<LotInspectionStatus, string> = {
  in_progress: "In progress",
  passed: "Accepted",
  failed: "Rejected",
  cancelled: "Cancelled",
};

const STATUS_COLOR: Record<LotInspectionStatus, string> = {
  in_progress: "#1E40AF",
  passed: "#15803D",
  failed: "#B91C1C",
  cancelled: "#64748B",
};

const table: React.CSSProperties = { width: "100%", borderCollapse: "collapse", fontSize: 13, marginBottom: 16 };
const th: React.CSSProperties = {
  textAlign: "left", padding: "6px 8px", borderBottom: "2px solid #CBD5E1", background: "#F1F5F9",
};
const td: React.CSSProperties = { padding: "6px 8px", borderBottom: "1px solid #E2E8F0", verticalAlign: "top" };
const h3: React.CSSProperties = { fontSize: 15, margin: "20px 0 8px" };

function fmt(dt: string | null) {
  return dt ? new Date(dt).toLocaleString() : "—";
}

function StatusBadge({ status }: { status: LotInspectionStatus }) {
  return (
    <span
      style={{
        display: "inline-block", padding: "2px 10px", borderRadius: 4, fontWeight: 600, fontSize: 13,
        color: "#FFFFFF", background: STATUS_COLOR[status],
      }}
    >
      {STATUS_LABEL[status]}
    </span>
  );
}

function Attempt({ inspection }: { inspection: LotInspectionDetail }) {
  const done = inspection.status === "passed" || inspection.status === "failed";
  const nonconforming = inspection.items.filter((i) => i.status === "defective");
  return (
    <section style={{ border: "1px solid #E2E8F0", borderRadius: 6, padding: 16, marginBottom: 20, breakInside: "avoid" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
        <strong style={{ fontSize: 16 }}>Inspection #{inspection.attempt_number}</strong>
        <StatusBadge status={inspection.status} />
      </div>

      <table style={table}>
        <tbody>
          <tr>
            <td style={td}><strong>Inspector</strong></td><td style={td}>{inspection.inspector_name ?? "—"}</td>
            <td style={td}><strong>Started / completed</strong></td>
            <td style={td}>{fmt(inspection.started_at)} / {fmt(inspection.completed_at)}</td>
          </tr>
          <tr>
            <td style={td}><strong>Lot size (N)</strong></td><td style={td}>{inspection.lot_size}</td>
            <td style={td}><strong>Sample size code letter</strong></td><td style={td}>{inspection.code_letter}</td>
          </tr>
          <tr>
            <td style={td}><strong>AQL</strong></td><td style={td}>{inspection.aql_level}</td>
            <td style={td}><strong>Sample size (n)</strong></td>
            <td style={td}>
              {inspection.sample_size}
              {inspection.sample_size >= inspection.lot_size ? " (100% inspection)" : ""}
            </td>
          </tr>
          <tr>
            <td style={td}><strong>Acceptance number (Ac)</strong></td><td style={td}>{inspection.acceptance_number}</td>
            <td style={td}><strong>Rejection number (Re)</strong></td><td style={td}>{inspection.rejection_number}</td>
          </tr>
          {done && (
            <tr>
              <td style={td}><strong>Non-conformities</strong></td>
              <td style={td}>
                {inspection.critical_defect_count} critical, {inspection.minor_defect_count} non-critical
              </td>
              <td style={td}><strong>Non-conforming records</strong></td>
              <td style={td}>{inspection.defective_record_count} of {inspection.sample_size}</td>
            </tr>
          )}
          <tr>
            <td style={td}><strong>Random selection seed</strong></td>
            <td style={{ ...td, fontFamily: "monospace", fontSize: 12 }} colSpan={3}>{inspection.random_seed}</td>
          </tr>
        </tbody>
      </table>

      {done && (
        <p style={{ fontSize: 13, margin: "0 0 12px" }}>
          <strong>Decision: </strong>
          {inspection.critical_defect_count
            ? "Rejected — a critical-field non-conformity fails the lot regardless of the acceptance number."
            : inspection.status === "passed"
              ? `Accepted — ${inspection.minor_defect_count} non-conformit${inspection.minor_defect_count === 1 ? "y" : "ies"} ≤ Ac ${inspection.acceptance_number}.`
              : `Rejected — ${inspection.minor_defect_count} non-conformities ≥ Re ${inspection.rejection_number}.`}
          {nonconforming.length > 0 && " Non-conforming records were returned for rework before handover."}
        </p>
      )}
      {inspection.summary_note && (
        <p style={{ fontSize: 13, margin: "0 0 12px" }}><strong>Inspector's note: </strong>{inspection.summary_note}</p>
      )}

      {inspection.field_tally.length > 0 && (
        <>
          <h3 style={h3}>Non-conformities by field</h3>
          <table style={table}>
            <thead>
              <tr><th style={th}>Field</th><th style={th}>Classification</th><th style={th}>Count</th></tr>
            </thead>
            <tbody>
              {inspection.field_tally.map((t) => (
                <tr key={t.field_key}>
                  <td style={td}>{t.title}</td>
                  <td style={td}>{t.is_critical ? "Critical" : "Non-critical"}</td>
                  <td style={td}>{t.defective_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      <h3 style={h3}>Sampled records</h3>
      <table style={table}>
        <thead>
          <tr>
            <th style={th}>Record</th><th style={th}>Identifier</th><th style={th}>Version</th>
            <th style={th}>Result</th><th style={th}>Findings</th>
          </tr>
        </thead>
        <tbody>
          {inspection.items.map((item) => {
            const defects = item.field_results.filter((f) => f.status === "defective");
            return (
              <tr key={item.record_id}>
                <td style={td}>#{item.record_id}</td>
                <td style={td}>{item.source_identifier ?? item.original_filename ?? "—"}</td>
                <td style={td}>v{item.record_version}</td>
                <td style={{ ...td, color: item.status === "defective" ? "#B91C1C" : undefined }}>
                  {item.status === "pending" ? "Not reviewed" : item.status === "defective" ? "Non-conforming" : "Conforming"}
                </td>
                <td style={td}>
                  {defects.length === 0
                    ? "—"
                    : defects.map((d) => (
                        <div key={d.field_key}>
                          {d.title}{d.is_critical ? " (critical)" : ""}{d.note ? ` — ${d.note}` : ""}
                        </div>
                      ))}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

export default function LotInspectionReport({ lotId }: { lotId: number }) {
  const printRef = useRef<HTMLDivElement>(null);
  const { data: report, isLoading, error } = useQuery({
    queryKey: lotInspectionReportKey(lotId),
    queryFn: () => getInspectionReport(lotId),
  });

  const handlePrint = () => {
    if (!printRef.current || !report) return;
    const win = window.open("", "_blank");
    if (!win) return;
    // innerHTML is React-rendered (already escaped); the lot name goes in
    // via document.title rather than string-built markup.
    win.document.write(
      `<!doctype html><html><head><meta charset="utf-8"><title></title>` +
        `<style>body{font-family:system-ui,sans-serif;color:#0F172A;margin:24px}@page{margin:16mm}</style>` +
        `</head><body>${printRef.current.innerHTML}</body></html>`,
    );
    win.document.close();
    win.document.title = `Inspection report — ${report.lot_name}`;
    win.focus();
    win.print();
  };

  if (isLoading) return <Spin />;
  if (error) return <Alert type="error" message={formatApiError(error, "Could not load the inspection report")} />;
  if (!report) return null;

  // Most recent attempt first on screen and on paper — that's the one the
  // handover decision rests on; earlier attempts follow as history.
  const attempts = [...report.inspections].reverse();

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 12 }}>
        <Button onClick={handlePrint} disabled={attempts.length === 0}>Print / Save as PDF</Button>
      </div>
      {attempts.length === 0 ? (
        <Empty description="No outgoing inspection has been carried out on this lot yet." />
      ) : (
        <div ref={printRef}>
          <h2 style={{ fontSize: 18, margin: "0 0 4px" }}>Outgoing Inspection Report</h2>
          <p style={{ fontSize: 13, color: "#475569", margin: "0 0 16px" }}>{report.standard}</p>
          <table style={table}>
            <tbody>
              <tr>
                <td style={td}><strong>Project</strong></td><td style={td}>{report.project_name ?? "—"}</td>
                <td style={td}><strong>Lot</strong></td><td style={td}>{report.lot_name} (#{report.lot_id})</td>
              </tr>
              <tr>
                <td style={td}><strong>Records in lot</strong></td><td style={td}>{report.lot_size}</td>
                <td style={td}><strong>Released to customer</strong></td><td style={td}>{fmt(report.released_at)}</td>
              </tr>
              <tr>
                <td style={td}><strong>Final inspection result</strong></td>
                <td style={td} colSpan={3}>
                  {report.final_status ? <StatusBadge status={report.final_status} /> : "—"}
                  {report.inspections.length > 1 && ` (after ${report.inspections.length} attempts)`}
                </td>
              </tr>
            </tbody>
          </table>
          {attempts.map((inspection) => (
            <Attempt key={inspection.id} inspection={inspection} />
          ))}
        </div>
      )}
    </div>
  );
}
