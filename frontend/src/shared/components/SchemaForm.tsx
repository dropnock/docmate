import Form from "@rjsf/antd";
import validator from "@rjsf/validator-ajv8";
import type { RJSFSchema } from "@rjsf/utils";
import { RangeArrayField, DateTextWidget, buildAutoUiSchema } from "./rjsf/CustomWidgets";
import { WorkspaceErrorBoundary } from "./WorkspaceErrorBoundary";

interface Props {
  schema: RJSFSchema;
  initialValues?: Record<string, unknown>;
  onSubmit: (values: Record<string, unknown>) => void;
  /** Unique form id — the external sticky submit button uses this to trigger submission */
  formId: string;
}

export default function SchemaForm({ schema, initialValues, onSubmit, formId }: Props) {
  const uiSchema = buildAutoUiSchema(schema);

  return (
    <WorkspaceErrorBoundary>
      <Form
        schema={schema}
        validator={validator}
        formData={initialValues}
        uiSchema={uiSchema}
        onSubmit={({ formData }) => onSubmit(formData as Record<string, unknown>)}
        showErrorList={false}
        liveValidate={false}
        widgets={{ DateText: DateTextWidget }}
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        fields={{ RangeArray: RangeArrayField } as any}
      >
        {/* Hidden button wired to the form — clicked programmatically by the sticky footer button */}
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
}
