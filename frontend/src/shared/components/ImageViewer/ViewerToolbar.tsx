import { Button, Space, Tooltip } from "antd";
import { ZoomIn, ZoomOut, Maximize, MoveHorizontal } from "lucide-react";

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
          <Button size="small" icon={<ZoomIn size={14} />} onClick={onZoomIn} />
        </Tooltip>
        <Tooltip title="Zoom Out">
          <Button size="small" icon={<ZoomOut size={14} />} onClick={onZoomOut} />
        </Tooltip>
        <Tooltip title="Fit Page">
          <Button size="small" icon={<Maximize size={14} />} onClick={onFitPage} />
        </Tooltip>
        <Tooltip title="Fit Width">
          <Button size="small" icon={<MoveHorizontal size={14} />} onClick={onFitWidth} />
        </Tooltip>
      </Space>
    </div>
  );
}
