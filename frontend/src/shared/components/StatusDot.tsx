import { Space } from "antd";

interface Props {
  filled: boolean;
  label: string;
}

/** Status indicator without color-coding: a small primary-blue dot (filled
 * for the positive/ready state, hollow otherwise) plus a text label. */
export default function StatusDot({ filled, label }: Props) {
  return (
    <Space size={6}>
      <span
        style={{
          display: "inline-block",
          width: 8,
          height: 8,
          borderRadius: "50%",
          background: filled ? "#1E40AF" : "#FFFFFF",
          border: "1.5px solid #1E40AF",
        }}
      />
      <span style={{ color: "#0F172A", fontSize: 13 }}>{label}</span>
    </Space>
  );
}
