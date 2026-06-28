import { useState, useEffect } from "react";
import { Input, Typography } from "antd";
import type { FieldProps, WidgetProps, RJSFSchema } from "@rjsf/utils";

// ─── Range Array Field ───────────────────────────────────────────────────────
// Replaces the default multi-item add/remove UI for primitive arrays.
// Applied automatically to any field with type:"array" and primitive items.
// Accepts comma-separated values and range notation: "700-750, 800, 805-810"
// which auto-expands to [700,701,...750, 800, 805,...810] on blur.
export function RangeArrayField({
  schema,
  formData,
  onChange,
  name,
  required,
  idSchema,
  rawErrors,
}: FieldProps) {
  const itemSchema = (schema as RJSFSchema).items as RJSFSchema | undefined;
  const isInt = itemSchema?.type === "integer";
  const isNum = itemSchema?.type === "number";
  const isStr = itemSchema?.type === "string";

  const toDisplay = (arr: (string | number)[]) => arr.join(", ");
  const initArr = (formData ?? []) as (string | number)[];

  const [inputValue, setInputValue] = useState(() => toDisplay(initArr));
  const [count, setCount] = useState(initArr.length);

  const formDataKey = JSON.stringify(formData ?? []);
  useEffect(() => {
    const arr = (formData ?? []) as (string | number)[];
    setInputValue(toDisplay(arr));
    setCount(arr.length);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [formDataKey]);

  const parse = (text: string): (string | number)[] => {
    const result: (string | number)[] = [];
    for (const part of text.split(",")) {
      const trimmed = part.trim();
      if (!trimmed) continue;
      const rangeMatch = trimmed.match(/^(-?\d+)\s*-\s*(-?\d+)$/);
      if (rangeMatch && (isInt || isNum)) {
        const start = parseInt(rangeMatch[1], 10);
        const end = parseInt(rangeMatch[2], 10);
        if (!isNaN(start) && !isNaN(end) && end >= start && end - start <= 9999) {
          for (let i = start; i <= end; i++) result.push(i);
        }
      } else if (isInt) {
        const n = parseInt(trimmed, 10);
        if (!isNaN(n)) result.push(n);
      } else if (isNum) {
        const n = parseFloat(trimmed);
        if (!isNaN(n)) result.push(n);
      } else if (isStr) {
        result.push(trimmed);
      }
    }
    return result;
  };

  const handleBlur = () => {
    const parsed = parse(inputValue);
    setInputValue(toDisplay(parsed));
    setCount(parsed.length);
    // rjsf 6 FieldProps.onChange requires (value, path, errorSchema?, id?)
    onChange(parsed as never, []);
  };

  const title = (schema.title as string | undefined) ?? name;
  const hasError = rawErrors && rawErrors.length > 0;

  return (
    <div style={{ marginBottom: 16 }}>
      <label
        htmlFor={idSchema.$id}
        style={{ display: "block", marginBottom: 4, fontWeight: 500, color: hasError ? "#ff4d4f" : undefined }}
      >
        {title}
        {required && <span style={{ color: "#ff4d4f", marginLeft: 2 }}>*</span>}
      </label>
      <Input.TextArea
        id={idSchema.$id}
        status={hasError ? "error" : undefined}
        value={inputValue}
        onChange={(e) => setInputValue(e.target.value)}
        onBlur={handleBlur}
        placeholder={isStr ? "alpha, beta, gamma" : "700-750, 800, 805-810"}
        rows={2}
        style={{ fontFamily: "monospace", fontSize: 13 }}
      />
      {hasError ? (
        rawErrors!.map((e, i) => (
          <div key={i} style={{ color: "#ff4d4f", fontSize: 12, marginTop: 2 }}>
            {e}
          </div>
        ))
      ) : (
        <Typography.Text type="secondary" style={{ fontSize: 11 }}>
          {count > 0 ? `${count} value${count !== 1 ? "s" : ""} · ` : ""}
          {isStr ? "Comma-separated." : "Comma-separated or ranges (e.g. 700-750)."}
        </Typography.Text>
      )}
    </div>
  );
}

// ─── Date Text Widget ────────────────────────────────────────────────────────
// Plain text input for date fields — allows free typing.
// Normalizes to YYYY-MM-DD on blur (accepts most date strings).
function normalizeToIso(input: string): string {
  if (!input.trim()) return "";
  const d = new Date(input);
  if (!isNaN(d.getTime())) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  }
  return input;
}

export function DateTextWidget({ value, onChange, id, required, rawErrors }: WidgetProps) {
  const [local, setLocal] = useState<string>(value ?? "");

  useEffect(() => {
    setLocal(value ?? "");
  }, [value]);

  const hasError = rawErrors && rawErrors.length > 0;

  return (
    <Input
      id={id}
      required={required}
      status={hasError ? "error" : undefined}
      value={local}
      onChange={(e) => setLocal(e.target.value)}
      onBlur={() => {
        const norm = normalizeToIso(local);
        setLocal(norm);
        onChange(norm || undefined);
      }}
      placeholder="YYYY-MM-DD  (or any recognisable date format)"
      style={{ fontFamily: "monospace" }}
    />
  );
}

// ─── Auto uiSchema ───────────────────────────────────────────────────────────
// Scans top-level properties and injects custom ui:field / ui:widget hints
// so no stored schema changes are required.
export function buildAutoUiSchema(schema: RJSFSchema): Record<string, unknown> {
  if (schema.type !== "object" || !schema.properties) return {};
  const ui: Record<string, unknown> = {};
  for (const [key, rawField] of Object.entries(schema.properties)) {
    const field = rawField as RJSFSchema;
    if (
      field.type === "array" &&
      typeof field.items === "object" &&
      field.items !== null &&
      !Array.isArray(field.items) &&
      ["string", "integer", "number"].includes(
        (field.items as RJSFSchema).type as string
      )
    ) {
      ui[key] = { "ui:field": "RangeArray" };
    } else if (field.type === "string" && field.format === "date") {
      ui[key] = { "ui:widget": "DateText" };
    }
  }
  return ui;
}
