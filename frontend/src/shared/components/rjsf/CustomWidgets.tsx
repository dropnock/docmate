import { useState, useEffect } from "react";
import { AutoComplete, Button, Input, Typography } from "antd";
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
  disabled,
  idSchema,
  rawErrors,
  ...rest
}: FieldProps) {
  // rjsf passes fieldPathId through ...props (untyped); it holds the correct root-relative
  // path to this field, e.g. ['volume_numbers']. Without it, passing [] as path tells rjsf
  // to replace the entire formData with just this array value, wiping other fields.
  const fieldPath =
    ((rest as Record<string, unknown>).fieldPathId as { path: (string | number)[] } | undefined)
      ?.path ?? [name];
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
    onChange(parsed as never, fieldPath);
  };

  const title = (schema.title as string | undefined) ?? name;
  const hasError = rawErrors && rawErrors.length > 0;

  return (
    <div style={{ marginBottom: 16 }}>
      <label
        htmlFor={idSchema?.$id}
        style={{ display: "block", marginBottom: 4, fontWeight: 500, color: hasError ? "#ff4d4f" : undefined }}
      >
        {title}
        {required && <span style={{ color: "#ff4d4f", marginLeft: 2 }}>*</span>}
      </label>
      <Input.TextArea
        id={idSchema?.$id}
        status={hasError ? "error" : undefined}
        value={inputValue}
        onChange={(e) => setInputValue(e.target.value)}
        onBlur={handleBlur}
        disabled={disabled}
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

// ─── Parcel Array Field ──────────────────────────────────────────────────────
// Handles arrays of objects that contain a volume-like and folio-like property.
// Accepts range notation in the Folio input: "400-450" expands to 51 individual
// {volume_number, folio_number} items. Volume is kept after Add so the user can
// quickly add further folio ranges for the same volume.
//
// Both inputs also commit on blur (handleAdd no-ops if either is still
// empty) — otherwise typing a pair and clicking straight to Submit without
// pressing "Add" silently discards it: the array stays empty, and a bare
// `required` (as opposed to `minItems`) on the field is satisfied by an
// empty-but-present array, so nothing catches the loss at validation time.
export function ParcelArrayField({
  schema,
  formData,
  onChange,
  name,
  required,
  disabled,
  idSchema,
  rawErrors,
  ...rest
}: FieldProps) {
  const fieldPath =
    ((rest as Record<string, unknown>).fieldPathId as { path: (string | number)[] } | undefined)
      ?.path ?? [name];

  const itemSchema = (schema as RJSFSchema).items as RJSFSchema | undefined;
  const itemProps = (itemSchema?.properties ?? {}) as Record<string, unknown>;
  const volumeKey = Object.keys(itemProps).find((k) => /volume/i.test(k)) ?? "volume_number";
  const folioKey = Object.keys(itemProps).find((k) => /folio/i.test(k)) ?? "folio_number";

  const currentItems = (formData ?? []) as Record<string, string>[];
  const [volumeInput, setVolumeInput] = useState("");
  const [folioInput, setFolioInput] = useState("");

  // Parse folio input — supports single values, comma-separated, and ranges (400-450).
  const parseFolios = (text: string): string[] => {
    const result: string[] = [];
    for (const part of text.split(",")) {
      const trimmed = part.trim();
      if (!trimmed) continue;
      const rangeMatch = trimmed.match(/^(\d+)\s*-\s*(\d+)$/);
      if (rangeMatch) {
        const start = parseInt(rangeMatch[1], 10);
        const end = parseInt(rangeMatch[2], 10);
        if (!isNaN(start) && !isNaN(end) && end >= start && end - start < 1000) {
          for (let i = start; i <= end; i++) result.push(String(i));
        }
      } else {
        result.push(trimmed);
      }
    }
    return result;
  };

  const handleAdd = () => {
    if (disabled || !volumeInput.trim() || !folioInput.trim()) return;
    const folios = parseFolios(folioInput);
    const newItems = folios.map((folio) => ({
      [volumeKey]: volumeInput.trim(),
      [folioKey]: folio,
    }));
    onChange([...currentItems, ...newItems] as never, fieldPath);
    // Keep volume so the user can add more folio ranges for the same volume.
    setFolioInput("");
  };

  const handleRemove = (idx: number) => {
    onChange(currentItems.filter((_, i) => i !== idx) as never, fieldPath);
  };

  const title = (schema.title as string | undefined) ?? name;
  const hasError = rawErrors && rawErrors.length > 0;

  return (
    <div style={{ marginBottom: 16 }}>
      <label
        htmlFor={idSchema?.$id}
        style={{ display: "block", marginBottom: 6, fontWeight: 500, color: hasError ? "#ff4d4f" : undefined }}
      >
        {title}
        {required && <span style={{ color: "#ff4d4f", marginLeft: 2 }}>*</span>}
      </label>

      {/* Input row */}
      <div style={{ display: "flex", gap: 6, marginBottom: 4 }}>
        <Input
          placeholder="Volume"
          value={volumeInput}
          onChange={(e) => setVolumeInput(e.target.value)}
          onBlur={handleAdd}
          disabled={disabled}
          style={{ width: 110, fontFamily: "monospace" }}
        />
        <Input
          placeholder="Folio — e.g. 400-450, 800"
          value={folioInput}
          onChange={(e) => setFolioInput(e.target.value)}
          onPressEnter={handleAdd}
          onBlur={handleAdd}
          disabled={disabled}
          style={{ flex: 1, fontFamily: "monospace" }}
        />
        <Button
          onClick={handleAdd}
          disabled={disabled || !volumeInput.trim() || !folioInput.trim()}
        >
          Add
        </Button>
      </div>
      <Typography.Text type="secondary" style={{ fontSize: 11, display: "block", marginBottom: 6 }}>
        Range notation: <code>400-450</code> creates one entry per folio. Comma-separated values also supported.
      </Typography.Text>

      {/* Current items */}
      {currentItems.length > 0 && (
        <div style={{ border: "1px solid #d9d9d9", borderRadius: 4, padding: "4px 8px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
            <Typography.Text type="secondary" style={{ fontSize: 11 }}>
              {currentItems.length} parcel{currentItems.length !== 1 ? "s" : ""}
            </Typography.Text>
            <Button
              size="small"
              type="link"
              danger
              disabled={disabled}
              onClick={() => onChange([] as never, fieldPath)}
            >
              Clear all
            </Button>
          </div>
          <div style={{ maxHeight: 220, overflowY: "auto" }}>
            {currentItems.map((item, i) => (
              <div
                key={i}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  padding: "2px 0",
                  borderTop: i > 0 ? "1px solid #f5f5f5" : undefined,
                }}
              >
                <Typography.Text style={{ fontSize: 12, fontFamily: "monospace" }}>
                  Vol {item[volumeKey]} · Folio {item[folioKey]}
                </Typography.Text>
                <Button
                  size="small"
                  type="text"
                  danger
                  disabled={disabled}
                  onClick={() => handleRemove(i)}
                  style={{ padding: "0 6px", height: "auto", lineHeight: 1.4 }}
                >
                  ×
                </Button>
              </div>
            ))}
          </div>
        </div>
      )}

      {hasError &&
        rawErrors!.map((e, i) => (
          <div key={i} style={{ color: "#ff4d4f", fontSize: 12, marginTop: 2 }}>
            {e}
          </div>
        ))}
    </div>
  );
}

// ─── Date Text Widget ────────────────────────────────────────────────────────
// Plain text input for date fields — allows free typing.
// Normalizes to YYYY-MM-DD on blur (accepts most date strings).
function normalizeToIso(input: string): string {
  const trimmed = input.trim();
  if (!trimmed) return "";
  // Already ISO — return as-is rather than round-tripping through `new
  // Date()`: a bare YYYY-MM-DD string is parsed as UTC midnight per spec,
  // so reading local y/m/d back out rolls the date back a day in any
  // timezone behind UTC.
  if (/^\d{4}-\d{2}-\d{2}$/.test(trimmed)) return trimmed;
  const d = new Date(trimmed);
  if (!isNaN(d.getTime())) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  }
  return trimmed;
}

export function DateTextWidget({ value, onChange, id, required, disabled, rawErrors }: WidgetProps) {
  const [local, setLocal] = useState<string>(value ?? "");

  useEffect(() => {
    setLocal(value ?? "");
  }, [value]);

  const hasError = rawErrors && rawErrors.length > 0;

  return (
    <Input
      id={id}
      required={required}
      disabled={disabled}
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

// ─── Country Widget ──────────────────────────────────────────────────────────
// Editable autocomplete dropdown for any field named "country" (or *_country).
// The user can type to filter the list or enter a free-form value not in the list.
const COUNTRIES = [
  "Afghanistan", "Albania", "Algeria", "Andorra", "Angola",
  "Antigua and Barbuda", "Argentina", "Armenia", "Australia", "Austria",
  "Azerbaijan", "Bahamas", "Bahrain", "Bangladesh", "Barbados",
  "Belarus", "Belgium", "Belize", "Benin", "Bhutan",
  "Bolivia", "Bosnia and Herzegovina", "Botswana", "Brazil", "Brunei",
  "Bulgaria", "Burkina Faso", "Burundi", "Cabo Verde", "Cambodia",
  "Cameroon", "Canada", "Central African Republic", "Chad", "Chile",
  "China", "Colombia", "Comoros", "Congo", "Costa Rica",
  "Croatia", "Cuba", "Cyprus", "Czechia", "Democratic Republic of the Congo",
  "Denmark", "Djibouti", "Dominica", "Dominican Republic", "Ecuador",
  "Egypt", "El Salvador", "Equatorial Guinea", "Eritrea", "Estonia",
  "Eswatini", "Ethiopia", "Fiji", "Finland", "France",
  "Gabon", "Gambia", "Georgia", "Germany", "Ghana",
  "Greece", "Grenada", "Guatemala", "Guinea", "Guinea-Bissau",
  "Guyana", "Haiti", "Honduras", "Hungary", "Iceland",
  "India", "Indonesia", "Iran", "Iraq", "Ireland",
  "Israel", "Italy", "Jamaica", "Japan", "Jordan",
  "Kazakhstan", "Kenya", "Kiribati", "Kuwait", "Kyrgyzstan",
  "Laos", "Latvia", "Lebanon", "Lesotho", "Liberia",
  "Libya", "Liechtenstein", "Lithuania", "Luxembourg", "Madagascar",
  "Malawi", "Malaysia", "Maldives", "Mali", "Malta",
  "Marshall Islands", "Mauritania", "Mauritius", "Mexico", "Micronesia",
  "Moldova", "Monaco", "Mongolia", "Montenegro", "Morocco",
  "Mozambique", "Myanmar", "Namibia", "Nauru", "Nepal",
  "Netherlands", "New Zealand", "Nicaragua", "Niger", "Nigeria",
  "North Korea", "North Macedonia", "Norway", "Oman", "Pakistan",
  "Palau", "Palestine", "Panama", "Papua New Guinea", "Paraguay",
  "Peru", "Philippines", "Poland", "Portugal", "Qatar",
  "Romania", "Russia", "Rwanda", "Saint Kitts and Nevis", "Saint Lucia",
  "Saint Vincent and the Grenadines", "Samoa", "San Marino", "Sao Tome and Principe", "Saudi Arabia",
  "Senegal", "Serbia", "Seychelles", "Sierra Leone", "Singapore",
  "Slovakia", "Slovenia", "Solomon Islands", "Somalia", "South Africa",
  "South Korea", "South Sudan", "Spain", "Sri Lanka", "Sudan",
  "Suriname", "Sweden", "Switzerland", "Syria", "Taiwan",
  "Tajikistan", "Tanzania", "Thailand", "Timor-Leste", "Togo",
  "Tonga", "Trinidad and Tobago", "Tunisia", "Turkey", "Turkmenistan",
  "Tuvalu", "Uganda", "Ukraine", "United Arab Emirates", "United Kingdom",
  "United States", "Uruguay", "Uzbekistan", "Vanuatu", "Vatican City",
  "Venezuela", "Vietnam", "Yemen", "Zambia", "Zimbabwe",
];

export function CountryWidget({ value, onChange, id, required, disabled, rawErrors }: WidgetProps) {
  const [inputValue, setInputValue] = useState<string>(value ?? "");

  useEffect(() => {
    setInputValue(value ?? "");
  }, [value]);

  const hasError = rawErrors && rawErrors.length > 0;

  const filtered = COUNTRIES.filter((c) =>
    c.toLowerCase().includes(inputValue.toLowerCase())
  ).map((c) => ({ value: c, label: c }));

  return (
    <AutoComplete
      id={id}
      value={inputValue}
      options={filtered}
      onChange={(val: string) => {
        setInputValue(val);
        onChange(val || undefined);
      }}
      placeholder="Select or type a country"
      style={{ width: "100%" }}
      status={hasError ? "error" : undefined}
      disabled={disabled}
      allowClear
    />
  );
}

// ─── Auto uiSchema ───────────────────────────────────────────────────────────
// Recursively builds uiSchema hints from the schema so no stored changes are needed.
// Handles nested objects and array-of-object items.

const TEXTAREA_KEYS = ["description", "notes", "note", "comment", "comments",
  "remarks", "details", "reason", "summary", "narrative", "text"];

function isTextarea(key: string, field: RJSFSchema): boolean {
  if (field.type !== "string" || field.enum || field.format) return false;
  const lower = key.toLowerCase();
  return TEXTAREA_KEYS.some((k) => lower === k || lower.endsWith(`_${k}`) || lower.startsWith(`${k}_`));
}

function isCountry(key: string, field: RJSFSchema): boolean {
  if (field.type !== "string" || field.enum || field.format) return false;
  const lower = key.toLowerCase();
  return lower === "country" || lower.endsWith("_country") || lower.startsWith("country_");
}

function hasVolumeFolioPair(itemSchema: RJSFSchema): boolean {
  if (itemSchema.type !== "object" || !itemSchema.properties) return false;
  const keys = Object.keys(itemSchema.properties as Record<string, unknown>);
  return keys.some((k) => /volume/i.test(k)) && keys.some((k) => /folio/i.test(k));
}

function buildUiForProperties(props: Record<string, unknown>): Record<string, unknown> {
  const ui: Record<string, unknown> = {};
  for (const [key, rawField] of Object.entries(props)) {
    if (!rawField || typeof rawField !== "object" || Array.isArray(rawField)) continue;
    const field = rawField as RJSFSchema;
    let fieldUi: Record<string, unknown> = {};

    if (
      field.type === "array" &&
      typeof field.items === "object" &&
      field.items !== null &&
      !Array.isArray(field.items)
    ) {
      const items = field.items as RJSFSchema;
      if (["string", "integer", "number"].includes(items.type as string)) {
        fieldUi = { "ui:field": "RangeArray" };
      } else if (items.type === "object" && items.properties) {
        if (hasVolumeFolioPair(items)) {
          fieldUi = { "ui:field": "ParcelArray" };
        } else {
          const itemsUi = buildUiForProperties(items.properties as Record<string, unknown>);
          if (Object.keys(itemsUi).length > 0) fieldUi = { items: itemsUi };
        }
      }
    } else if (field.type === "string" && field.format === "date") {
      fieldUi = { "ui:widget": "DateText" };
    } else if (isCountry(key, field)) {
      fieldUi = { "ui:widget": "Country" };
    } else if (isTextarea(key, field)) {
      fieldUi = { "ui:widget": "textarea", "ui:options": { rows: 3 } };
    } else if (field.type === "object" && field.properties) {
      const nested = buildUiForProperties(field.properties as Record<string, unknown>);
      if (Object.keys(nested).length > 0) fieldUi = nested;
    }

    // Admin-authored "x-hidden"/"x-disabled" on the property itself (see
    // CabinetManager's schema editor helper text) — plain JSON Schema
    // keywords AJV ignores for validation, read here to drive the widget.
    // Hidden wins over whatever widget was picked above since there's
    // nothing to render; the matching required field is stripped in
    // SchemaForm's preprocessSchema so a hidden field can never block submit.
    const flags = field as unknown as Record<string, unknown>;
    if (flags["x-hidden"]) {
      fieldUi = { ...fieldUi, "ui:widget": "hidden" };
    } else if (flags["x-disabled"]) {
      fieldUi = { ...fieldUi, "ui:disabled": true };
    }

    if (Object.keys(fieldUi).length > 0) ui[key] = fieldUi;
  }
  return ui;
}

export function buildAutoUiSchema(schema: RJSFSchema): Record<string, unknown> {
  try {
    if (schema.type !== "object" || !schema.properties) return {};
    return buildUiForProperties(schema.properties as Record<string, unknown>);
  } catch {
    return {};
  }
}
