import { Input, Radio, Segmented, Space, Table, Tag, Tooltip, Typography } from "antd";
import type { ClassResults, ErrorClasses, ErrorType, QcFieldStatus, Severity } from "@shared/types";

/** Critical / major / minor non-conformities — shared by the digitizing
 * supervisor's outgoing inspection and the customer's QC. The rules live in
 * backend/app/services/defect_class_service.py; classifyMark mirrors them
 * so a reviewer sees a mark's class before saving it. */

export const SEVERITIES: Severity[] = ["critical", "major", "minor"];

export const SEVERITY: Record<Severity, { label: string; color: string }> = {
  critical: { label: "Critical", color: "red" },
  major: { label: "Major", color: "orange" },
  minor: { label: "Minor", color: "gold" },
};

export const ERROR_TYPES: { value: ErrorType; label: string; hint: string }[] = [
  { value: "missing", label: "Missing", hint: "A value on the image was left out" },
  { value: "typo", label: "Typo", hint: "Typographic error: wrong, extra or swapped characters" },
  { value: "interpretation", label: "Interpretation", hint: "The source was read or understood wrongly" },
];

export const ERROR_TYPE_LABEL: Record<ErrorType, string> = {
  missing: "Missing data",
  typo: "Typographic error",
  interpretation: "Interpretation error",
};

/** The AQL columns of the ISO 2859-1 tables (aql_service.AVAILABLE_AQLS). */
export const AQLS = [0.65, 1.0, 1.5, 2.5, 4.0, 6.5];
export const AQL_OPTIONS = AQLS.map((v) => ({ value: v, label: `${v}%` }));
/** A form's stand-in for critical_aql null (Ac 0) — Select can't hold null. */
export const ZERO_TOLERANCE = "zero";
export const CRITICAL_AQL_OPTIONS = [{ value: ZERO_TOLERANCE, label: "Zero tolerance" }, ...AQL_OPTIONS];
export const DEFAULT_CLASS_AQLS = { critical: null, major: 1.5, minor: 4.0 } as const;

export const DEFAULT_ERROR_CLASSES: ErrorClasses = { missing: "major", typo: "minor", interpretation: "minor" };

export function classifyMark(isIdentifier: boolean, errorType: ErrorType | undefined | null, errorClasses?: ErrorClasses): Severity | undefined {
  if (isIdentifier) return "critical";
  if (!errorType) return undefined;
  return (errorClasses ?? DEFAULT_ERROR_CLASSES)[errorType];
}

/** Nothing indexed for the field — a defect there is most likely Missing. */
export function isEmptyValue(v: unknown): boolean {
  if (v === undefined || v === null) return true;
  if (typeof v === "string") return v.trim() === "";
  if (Array.isArray(v)) return v.length === 0;
  if (typeof v === "object") return Object.values(v as object).every(isEmptyValue);
  return false;
}

export function SeverityTag({ severity }: { severity: Severity }) {
  return <Tag color={SEVERITY[severity].color}>{SEVERITY[severity].label}</Tag>;
}

export interface FieldMark {
  status: QcFieldStatus;
  note?: string;
  error_type?: ErrorType;
}

/** One field's review: Accepted / Defective, and for a defect what was
 * wrong (Missing / Typo / Interpretation) — its class follows from that.
 * An empty field flipped to Defective starts as Missing. */
export function FieldMarkControl({
  title, isIdentifier, mark, onChange, disabled, valueEmpty, errorClasses, notePlaceholder,
}: {
  title: string;
  isIdentifier: boolean;
  mark: FieldMark;
  onChange: (m: FieldMark) => void;
  disabled?: boolean;
  valueEmpty?: boolean;
  errorClasses?: ErrorClasses;
  notePlaceholder?: string;
}) {
  const severity = mark.status === "defective" ? classifyMark(isIdentifier, mark.error_type, errorClasses) : undefined;
  return (
    <div data-field-mark={title}>
      <Space align="center" style={{ marginBottom: 6 }} wrap>
        <Typography.Text strong>{title}</Typography.Text>
        {isIdentifier && (
          <Tooltip title="Identifier field: any error here is critical">
            <Tag color="red">Identifier</Tag>
          </Tooltip>
        )}
      </Space>
      <br />
      <Segmented
        disabled={disabled}
        value={mark.status}
        onChange={(status) =>
          onChange(
            status === "defective"
              ? { ...mark, status: "defective", error_type: mark.error_type ?? (valueEmpty ? "missing" : undefined) }
              : { ...mark, status: "accepted" },
          )
        }
        options={[
          { label: "Accepted", value: "accepted" },
          { label: "Defective", value: "defective" },
        ]}
      />
      {mark.status === "defective" && (
        <div style={{ marginTop: 8 }}>
          <Space wrap align="center">
            <Radio.Group
              size="small"
              optionType="button"
              buttonStyle="solid"
              disabled={disabled}
              value={mark.error_type}
              onChange={(e) => onChange({ ...mark, error_type: e.target.value })}
            >
              {ERROR_TYPES.map((t) => (
                <Tooltip key={t.value} title={t.hint}>
                  <Radio.Button value={t.value}>{t.label}</Radio.Button>
                </Tooltip>
              ))}
            </Radio.Group>
            {severity ? (
              <SeverityTag severity={severity} />
            ) : (
              <Typography.Text type="warning">Choose what is wrong</Typography.Text>
            )}
          </Space>
          <Input
            disabled={disabled}
            placeholder={notePlaceholder ?? "Describe the error (optional)"}
            style={{ marginTop: 8 }}
            value={mark.note ?? ""}
            onChange={(e) => onChange({ ...mark, note: e.target.value })}
          />
        </div>
      )}
    </div>
  );
}

/** Defective fields still waiting for an error type. */
export function unclassedFields(marks: Record<string, FieldMark>): string[] {
  return Object.entries(marks)
    .filter(([, m]) => m.status === "defective" && !m.error_type)
    .map(([k]) => k);
}

/** "Critical 0/0 · Major 1/0 · Minor 2/3" — found against Ac per class;
 * red once a class reaches its rejection number. */
export function ClassTally({ classes, counts }: { classes: ClassResults; counts: Record<Severity, number> }) {
  return (
    <Space size={4} wrap>
      {SEVERITIES.map((s) => {
        const over = counts[s] > classes[s].acceptance_number;
        return (
          <Tooltip
            key={s}
            title={`${counts[s]} ${SEVERITY[s].label.toLowerCase()} non-conformit${counts[s] === 1 ? "y" : "ies"} found; accept ≤ ${classes[s].acceptance_number}, reject ≥ ${classes[s].rejection_number}`}
          >
            <Tag color={over ? "error" : "default"} data-class-tally={s}>
              {SEVERITY[s].label} {counts[s]} / {classes[s].acceptance_number}
            </Tag>
          </Tooltip>
        );
      })}
    </Space>
  );
}

export function countBySeverity(severities: (Severity | null | undefined)[]): Record<Severity, number> {
  const out: Record<Severity, number> = { critical: 0, major: 0, minor: 0 };
  for (const s of severities) if (s) out[s] += 1;
  return out;
}

export function classesAccepted(classes: ClassResults, counts: Record<Severity, number>): boolean {
  return SEVERITIES.every((s) => counts[s] <= classes[s].acceptance_number);
}

/** Class · AQL · Ac · Re · Found · Result, for reports and lot results.
 * hideAql: customers see Ac/Re but not the AQL numbers. */
export function ClassResultsTable({ classes, hideAql }: { classes: ClassResults; hideAql?: boolean }) {
  const rows = SEVERITIES.map((s) => ({ key: s, severity: s, ...classes[s] }));
  return (
    <Table
      size="small"
      pagination={false}
      dataSource={rows}
      columns={[
        { title: "Class", dataIndex: "severity", render: (s: Severity) => <SeverityTag severity={s} /> },
        ...(hideAql
          ? []
          : [{ title: "AQL", dataIndex: "aql", render: (v: number | null) => (v === null ? "Zero tolerance" : v) }]),
        { title: "Ac", dataIndex: "acceptance_number" },
        { title: "Re", dataIndex: "rejection_number" },
        { title: "Found", dataIndex: "found", render: (v: number | null | undefined) => v ?? "—" },
        {
          title: "Result",
          dataIndex: "accepted",
          render: (v: boolean | null | undefined) =>
            v === true ? <Tag color="green">Within limit</Tag> : v === false ? <Tag color="red">Rejects lot</Tag> : "—",
        },
      ]}
    />
  );
}
