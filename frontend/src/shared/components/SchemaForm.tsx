import Form from "@rjsf/antd";
import validator from "@rjsf/validator-ajv8";
import type { RJSFSchema } from "@rjsf/utils";
import { Button } from "antd";

interface Props {
  schema: RJSFSchema;
  initialValues?: Record<string, unknown>;
  onSubmit: (values: Record<string, unknown>) => void;
  loading?: boolean;
  submitLabel?: string;
}

export default function SchemaForm({
  schema,
  initialValues,
  onSubmit,
  loading,
  submitLabel = "Submit",
}: Props) {
  return (
    <Form
      schema={schema}
      validator={validator}
      formData={initialValues}
      onSubmit={({ formData }) => onSubmit(formData as Record<string, unknown>)}
      showErrorList={false}
      liveValidate={false}
    >
      <Button type="primary" htmlType="submit" loading={loading} block style={{ marginTop: 8 }}>
        {submitLabel}
      </Button>
    </Form>
  );
}
