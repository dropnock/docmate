import { Alert, Typography } from "antd";
import type { ErrorSchema } from "@rjsf/utils";
import type { ReworkDefectField, ReworkDefects } from "@shared/types";
import { SeverityTag } from "./DefectClasses";

/** The rework banner of the indexer's and QA agent's workspace: what the
 * record was sent back for, field by field (outgoing inspection, customer
 * QC) or as the QA agent's or supervisor's note. */
export function ReworkDefectsBanner({ defects, version }: { defects: ReworkDefects; version?: number }) {
  const when = new Date(defects.at).toLocaleString();
  return (
    <Alert
      type="warning"
      banner
      message={
        <span>
          Rework — {defects.title}: sent back {defects.by ? `by ${defects.by} ` : ""}on {when}.
          {version ? ` Version ${version}; the previous data is pre-filled.` : ""}
        </span>
      }
      description={
        <div style={{ fontSize: 13 }}>
          {defects.note && <div style={{ whiteSpace: "pre-wrap" }}>{defects.note}</div>}
          {defects.fields.length > 0 && (
            <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
              {defects.fields.map((f) => (
                <li key={f.field_key}>
                  <Typography.Text strong>{f.label}</Typography.Text>
                  {f.error_label ? ` — ${f.error_label}` : ""}
                  {f.severity && <span style={{ marginLeft: 6 }}><SeverityTag severity={f.severity} /></span>}
                  {f.note && <Typography.Text type="secondary">{` “${f.note}”`}</Typography.Text>}
                </li>
              ))}
            </ul>
          )}
        </div>
      }
    />
  );
}

const unescape = (seg: string) => seg.replace(/~1/g, "/").replace(/~0/g, "~");

/** The defects as RJSF extra errors, so each sent-back field shows its note
 * in the form (a 3-segment key's middle segment is the list entry). */
export function reworkFieldErrors(fields: ReworkDefectField[]): ErrorSchema {
  const out: Record<string, unknown> = {};
  for (const f of fields) {
    const segs = f.field_key.split("/").map(unescape);
    let node = out;
    for (const seg of segs) {
      node[seg] = (node[seg] as Record<string, unknown>) ?? {};
      node = node[seg] as Record<string, unknown>;
    }
    const errs = ((node.__errors as string[]) ??= []);
    errs.push(`Sent back: ${[f.error_label, f.note].filter(Boolean).join(" — ") || "check this field"}`);
  }
  return out as ErrorSchema;
}
