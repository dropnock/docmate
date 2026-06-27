import { Button, DatePicker, Form, Input, InputNumber, Select } from "antd";
import dayjs from "dayjs";

interface JsonSchemaField {
  type: string;
  title?: string;
  enum?: string[];
  format?: string;
}

interface JsonSchema {
  properties?: Record<string, JsonSchemaField>;
  required?: string[];
}

interface Props {
  schema: JsonSchema;
  initialValues?: Record<string, unknown>;
  onSubmit: (values: Record<string, unknown>) => void;
  loading?: boolean;
  submitLabel?: string;
}

function renderField(name: string, field: JsonSchemaField, required: boolean) {
  const label = field.title || name;
  const rules = required ? [{ required: true }] : [];

  if (field.enum) {
    return (
      <Form.Item key={name} name={name} label={label} rules={rules}>
        <Select options={field.enum.map((v) => ({ label: v, value: v }))} />
      </Form.Item>
    );
  }
  if (field.type === "integer" || field.type === "number") {
    return (
      <Form.Item key={name} name={name} label={label} rules={rules}>
        <InputNumber style={{ width: "100%" }} />
      </Form.Item>
    );
  }
  if (field.format === "date") {
    return (
      <Form.Item key={name} name={name} label={label} rules={rules}>
        <DatePicker style={{ width: "100%" }} />
      </Form.Item>
    );
  }
  return (
    <Form.Item key={name} name={name} label={label} rules={rules}>
      <Input />
    </Form.Item>
  );
}

export default function SchemaForm({ schema, initialValues, onSubmit, loading, submitLabel = "Submit" }: Props) {
  const [form] = Form.useForm();

  // Pre-fill initial values, converting date strings to dayjs
  const normalizedInitial: Record<string, unknown> = {};
  if (initialValues && schema.properties) {
    for (const [k, v] of Object.entries(initialValues)) {
      const field = schema.properties[k];
      if (field?.format === "date" && typeof v === "string") {
        normalizedInitial[k] = dayjs(v);
      } else {
        normalizedInitial[k] = v;
      }
    }
  }

  return (
    <Form
      form={form}
      layout="vertical"
      initialValues={normalizedInitial}
      onFinish={(values) => {
        // Serialize dayjs values back to strings
        const serialized: Record<string, unknown> = {};
        for (const [k, v] of Object.entries(values)) {
          serialized[k] = dayjs.isDayjs(v) ? (v as ReturnType<typeof dayjs>).format("YYYY-MM-DD") : v;
        }
        onSubmit(serialized);
      }}
    >
      {schema.properties &&
        Object.entries(schema.properties).map(([name, field]) =>
          renderField(name, field, schema.required?.includes(name) ?? false)
        )}
      <Button type="primary" htmlType="submit" loading={loading} block>
        {submitLabel}
      </Button>
    </Form>
  );
}
