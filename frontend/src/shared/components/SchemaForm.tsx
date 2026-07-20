import Form from "@rjsf/antd";
import validator from "@rjsf/validator-ajv8";
import { getDefaultFormState, type RJSFSchema } from "@rjsf/utils";
import { useState, useEffect, useMemo, useRef, forwardRef, useImperativeHandle, type KeyboardEvent } from "react";
import { RangeArrayField, ParcelArrayField, DateTextWidget, CountryWidget, buildAutoUiSchema } from "./rjsf/CustomWidgets";
import { WorkspaceErrorBoundary } from "./WorkspaceErrorBoundary";

export interface SchemaFormHandle {
  getValues: () => Record<string, unknown>;
}

interface Props {
  schema: RJSFSchema;
  initialValues?: Record<string, unknown>;
  onSubmit: (values: Record<string, unknown>) => void;
  formId: string;
  // Renders every field (including array/object fields like ParcelArrayField)
  // fully expanded but non-interactive — same widgets and structure as the
  // editable form, just disabled. RJSF's disabled prop cascades to every
  // field/widget automatically, and the custom widgets already read it
  // correctly (see CustomWidgets.tsx's ui:disabled handling).
  readOnly?: boolean;
}

function humanize(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function isObjectRecord(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

// Walk the schema tree:
//  1. Replace local $refs with inlined definitions while preserving sibling keys
//  2. Add human-readable titles to properties that don't already have one
//  3. Give array/object fields stable defaults so RJSF add buttons create usable rows
function preprocessSchema(schema: RJSFSchema): RJSFSchema {
  const defs: Record<string, unknown> = {
    ...(schema.definitions as Record<string, unknown> ?? {}),
    ...(schema.$defs as Record<string, unknown> ?? {}),
  };

  function walk(node: unknown): unknown {
    if (!node || typeof node !== "object") return node;
    if (Array.isArray(node)) return node.map((v) => walk(v));

    const obj = node as Record<string, unknown>;
    let source = obj;

    // Resolve local $ref. If the schema has siblings next to $ref, keep them.
    if ("$ref" in obj) {
      const ref = obj.$ref as string;
      const key =
        ref.startsWith("#/definitions/") ? ref.slice("#/definitions/".length) :
        ref.startsWith("#/$defs/") ? ref.slice("#/$defs/".length) : null;
      if (key && key in defs && isObjectRecord(defs[key])) {
        const { $ref: _ref, ...siblings } = obj;
        source = { ...(defs[key] as Record<string, unknown>), ...siblings };
      }
    }

    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(source)) {
      if (k === "properties" && v && typeof v === "object" && !Array.isArray(v)) {
        const props: Record<string, unknown> = {};
        for (const [propKey, propVal] of Object.entries(v as Record<string, unknown>)) {
          const walked = walk(propVal) as Record<string, unknown>;
          props[propKey] = walked?.title ? walked : { ...walked, title: humanize(propKey) };
        }
        out[k] = props;
      } else {
        out[k] = walk(v);
      }
    }

    // A field marked "x-hidden" has no widget rendered for it (see
    // buildAutoUiSchema), so it can never be filled in — if it's also
    // listed as required, submit would be permanently blocked. Strip it
    // from this node's required list rather than trust admin-authored
    // schemas to avoid the combination.
    if (Array.isArray(out.required) && isObjectRecord(out.properties)) {
      const properties = out.properties as Record<string, unknown>;
      out.required = (out.required as unknown[]).filter((name) => {
        const prop = properties[name as string];
        return !(isObjectRecord(prop) && prop["x-hidden"]);
      });
    }

    if (out.type === "array" && out.default === undefined) {
      out.default = [];
    }
    if (out.type === "array" && isObjectRecord(out.items)) {
      const items = out.items as Record<string, unknown>;
      if (items.type === "object" && items.default === undefined) {
        out.items = { ...items, default: {} };
      }
    }
    if (out.type === "object" && out.default === undefined && out.properties) {
      out.default = {};
    }

    return out;
  }

  return walk(schema) as RJSFSchema;
}

function initialFormData(schema: RJSFSchema, values?: Record<string, unknown>): Record<string, unknown> {
  const defaults = getDefaultFormState(validator, schema, values ?? {}, schema);
  return isObjectRecord(defaults) ? defaults : {};
}

const FOCUSABLE_SELECTOR =
  'input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [role="combobox"]:not([aria-disabled="true"])';

// Enter moves focus to the next field instead of the browser's default
// implicit-submit behavior (this form has a real <button type="submit">, so
// without this every plain input would submit the whole record on Enter).
// Fields render in schema-property order with no ui:order override anywhere
// in this codebase, so DOM order already matches visual/schema order — a
// plain traversal is correct without any separate ordering logic.
function handleEnterNavigation(e: KeyboardEvent<HTMLDivElement>, root: HTMLDivElement | null) {
  if (e.key !== "Enter" || !root) return;
  const target = e.target as HTMLElement;
  if (target.tagName === "TEXTAREA") return; // let newline happen
  if (target.closest("[data-enter-skip]")) return; // widget handles its own Enter (e.g. ParcelArrayField)
  e.preventDefault();
  const focusable = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
    (el) => el.tabIndex !== -1 && el.offsetParent !== null // skip the hidden submit button etc.
  );
  const idx = focusable.indexOf(target);
  if (idx === -1 || idx === focusable.length - 1) return; // unknown target or last field: no-op
  focusable[idx + 1]?.focus();
}

const SchemaForm = forwardRef<SchemaFormHandle, Props>(function SchemaForm(
  { schema, initialValues, onSubmit, formId, readOnly }: Props,
  ref,
) {
  const processedSchema = useMemo(() => preprocessSchema(schema), [schema]);
  const uiSchema = useMemo(() => buildAutoUiSchema(processedSchema), [processedSchema]);

  // Local form state — prevents the parent's record polling from resetting user edits
  const [formData, setFormData] = useState<Record<string, unknown>>(() =>
    initialFormData(processedSchema, initialValues)
  );
  const seenInitial = useRef(initialValues !== undefined);
  const formRootRef = useRef<HTMLDivElement>(null);

  useImperativeHandle(ref, () => ({ getValues: () => formData }), [formData]);

  useEffect(() => {
    // Pre-fill only once when initialValues first becomes available (rework case)
    if (!seenInitial.current && initialValues !== undefined) {
      setFormData(initialFormData(processedSchema, initialValues));
      seenInitial.current = true;
    }
  }, [initialValues, processedSchema]);

  return (
    <WorkspaceErrorBoundary>
      <div ref={formRootRef} onKeyDown={(e) => handleEnterNavigation(e, formRootRef.current)}>
        <Form
          schema={processedSchema}
          validator={validator}
          formData={formData}
          onChange={({ formData: fd }) => setFormData(isObjectRecord(fd) ? fd : {})}
          uiSchema={uiSchema}
          onSubmit={({ formData: fd }) => onSubmit(isObjectRecord(fd) ? fd : formData)}
          showErrorList={false}
          liveValidate={false}
          disabled={readOnly}
          widgets={{ DateText: DateTextWidget, Country: CountryWidget }}
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          fields={{ RangeArray: RangeArrayField, ParcelArray: ParcelArrayField } as any}
        >
          <button
            id={`${formId}-submit`}
            type="submit"
            style={{ display: "none" }}
            aria-hidden
            tabIndex={-1}
          />
        </Form>
      </div>
    </WorkspaceErrorBoundary>
  );
});

export default SchemaForm;
