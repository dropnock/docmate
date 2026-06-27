import { Button, Space, Tooltip } from "antd";
import {
  ZoomInOutlined,
  ZoomOutOutlined,
  ExpandOutlined,
  ColumnWidthOutlined,
} from "@ant-design/icons";

interface Props {
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFitPage: () => void;
  onFitWidth: () => void;
}

export default function ViewerToolbar({ onZoomIn, onZoomOut, onFitPage, onFitWidth }: Props) {
  return (
    <div style={{ padding: "4px 8px", background: "#262626", display: "flex", gap: 4 }}>
      <Space>
        <Tooltip title="Zoom In">
          <Button size="small" icon={<ZoomInOutlined />} onClick={onZoomIn} />
        </Tooltip>
        <Tooltip title="Zoom Out">
          <Button size="small" icon={<ZoomOutOutlined />} onClick={onZoomOut} />
        </Tooltip>
        <Tooltip title="Fit Page">
          <Button size="small" icon={<ExpandOutlined />} onClick={onFitPage} />
        </Tooltip>
        <Tooltip title="Fit Width">
          <Button size="small" icon={<ColumnWidthOutlined />} onClick={onFitWidth} />
        </Tooltip>
      </Space>
    </div>
  );
}
