import type { ReactNode } from "react";
import { Row, Typography } from "antd";

interface Props {
  title: string;
  description?: string;
  extra?: ReactNode;
}

/** Replaces two competing per-page title conventions (Typography.Title used
 * directly vs. a raw styled span) with one shared pattern: title (+ optional
 * description) on the left, an actions slot on the right. */
export default function PageHeader({ title, description, extra }: Props) {
  return (
    <Row justify="space-between" align="top" style={{ marginBottom: 16 }}>
      <div>
        <Typography.Title level={4} style={{ margin: 0 }}>
          {title}
        </Typography.Title>
        {description && (
          <Typography.Text type="secondary">{description}</Typography.Text>
        )}
      </div>
      {extra}
    </Row>
  );
}
