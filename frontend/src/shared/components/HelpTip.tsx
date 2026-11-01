import type { ReactNode } from "react";
import { Tooltip } from "antd";
import { Info } from "lucide-react";

const ICON_STYLE = { color: "#94A3B8", cursor: "help", verticalAlign: "-2px" } as const;

/** Help text behind an info icon, shown on hover or keyboard focus, so
 * screens aren't cluttered with explanations people only need once.
 * Keep warnings, errors and live status visible — this is for "how it
 * works" notes only. */
export default function HelpTip({ children, size = 14 }: { children: ReactNode; size?: number }) {
  return (
    <Tooltip title={children} overlayStyle={{ maxWidth: 380 }}>
      <span
        role="img"
        aria-label="Help"
        tabIndex={0}
        style={{ display: "inline-flex", marginLeft: 6 }}
        // Inside an Upload.Dragger or a clickable header, hovering shouldn't
        // turn into a click on what's underneath.
        onClick={(e) => e.stopPropagation()}
      >
        <Info size={size} style={ICON_STYLE} />
      </span>
    </Tooltip>
  );
}

/** For Form.Item's `tooltip` prop: the same icon and width beside the label. */
export function helpTooltip(title: ReactNode) {
  return { title, icon: <Info size={14} style={ICON_STYLE} />, overlayStyle: { maxWidth: 380 } };
}
