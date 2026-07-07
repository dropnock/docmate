import { Card, Col, Row, Skeleton } from "antd";

interface Props {
  variant?: "page" | "cards" | "table";
  count?: number;
}

/** Shared loading placeholder — used as the Suspense fallback for lazy-loaded
 * routes, and as a drop-in replacement for bare <Spin/> call sites so loading
 * states look like part of the page instead of a blank flash. */
export default function PageSkeleton({ variant = "page", count = 4 }: Props) {
  if (variant === "cards") {
    return (
      <Row gutter={16}>
        {Array.from({ length: count }).map((_, i) => (
          <Col key={i} xs={24} sm={12} md={8} lg={6} style={{ marginBottom: 16 }}>
            <Card>
              <Skeleton active paragraph={{ rows: 1 }} />
            </Card>
          </Col>
        ))}
      </Row>
    );
  }

  if (variant === "table") {
    return (
      <Card>
        <Skeleton active title={false} paragraph={{ rows: count, width: "100%" }} />
      </Card>
    );
  }

  return <Skeleton active paragraph={{ rows: 6 }} style={{ padding: 24 }} />;
}
