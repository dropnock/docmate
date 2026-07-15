import { Button, Space, Tooltip, Typography } from "antd";
import { ZoomIn, ZoomOut, Maximize, MoveHorizontal, ChevronLeft, ChevronRight } from "lucide-react";

interface Props {
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFitPage: () => void;
  onFitWidth: () => void;
  page?: number;
  pageCount?: number;
  onPrevPage?: () => void;
  onNextPage?: () => void;
}

export default function ViewerToolbar({
  onZoomIn,
  onZoomOut,
  onFitPage,
  onFitWidth,
  page = 0,
  pageCount = 1,
  onPrevPage,
  onNextPage,
}: Props) {
  return (
    <div
      style={{
        padding: "4px 8px",
        background: "#262626",
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        gap: 4,
      }}
    >
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
      {pageCount > 1 && (
        <Space>
          <Tooltip title="Previous Page">
            <Button
              size="small"
              icon={<ChevronLeft size={14} />}
              disabled={page <= 0}
              onClick={onPrevPage}
            />
          </Tooltip>
          <Typography.Text style={{ color: "#fff", fontSize: 12 }}>
            Page {page + 1} / {pageCount}
          </Typography.Text>
          <Tooltip title="Next Page">
            <Button
              size="small"
              icon={<ChevronRight size={14} />}
              disabled={page >= pageCount - 1}
              onClick={onNextPage}
            />
          </Tooltip>
        </Space>
      )}
    </div>
  );
}
