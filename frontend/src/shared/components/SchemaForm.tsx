import Form from "@rjsf/antd";
import validator from "@rjsf/validator-ajv8";
import { getDefaultFormState, type RJSFSchema } from "@rjsf/utils";
import { useState, useEffect, useMemo, useRef, forwardRef, useImperativeHandle } from "react";
import { RangeArrayField, DateTextWidget, buildAutoUiSchema } from "./rjsf/CustomWidgets";
import { WorkspaceErrorBoundary } from "./WorkspaceErrorBoundary";

export interface SchemaFormHandle {
  getValues: () => Record<string, unknown>;
}

interface Props {
  schema: RJSFSchema;
  initialValues?: Record<string, unknown>;
  onSubmit: (values: Record<string, unknown>) => void;
  formId: string;
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

const SchemaForm = forwardRef<SchemaFormHandle, Props>(function SchemaForm(
  { schema, initialValues, onSubmit, formId }: Props,
  ref,
) {
  const processedSchema = useMemo(() => preprocessSchema(schema), [schema]);
  const uiSchema = useMemo(() => buildAutoUiSchema(processedSchema), [processedSchema]);

  // Local form state — prevents the parent's record polling from resetting user edits
  const [formData, setFormData] = useState<Record<string, unknown>>(() =>
    initialFormData(processedSchema, initialValues)
  );
  const seenInitial = useRef(initialValues !== undefined);

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
      <Form
        schema={processedSchema}
        validator={validator}
        formData={formData}
        onChange={({ formData: fd }) => setFormData(isObjectRecord(fd) ? fd : {})}
        uiSchema={uiSchema}
        onSubmit={({ formData: fd }) => onSubmit(isObjectRecord(fd) ? fd : formData)}
        showErrorList={false}
        liveValidate={false}
        widgets={{ DateText: DateTextWidget }}
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        fields={{ RangeArray: RangeArrayField } as any}
      >
        <button
          id={`${formId}-submit`}
          type="submit"
          style={{ display: "none" }}
          aria-hidden
          tabIndex={-1}
        />
      </Form>
    </WorkspaceErrorBoundary>
  );
});

export default SchemaForm;
