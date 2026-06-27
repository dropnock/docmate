import Split from "react-split";

interface Props {
  left: React.ReactNode;
  right: React.ReactNode;
  initialSizes?: [number, number];
}

export default function SplitWorkspace({ left, right, initialSizes = [50, 50] }: Props) {
  return (
    <Split
      sizes={initialSizes}
      minSize={200}
      gutterSize={6}
      style={{ display: "flex", height: "100%", overflow: "hidden" }}
    >
      <div style={{ overflow: "hidden", height: "100%" }}>{left}</div>
      <div style={{ overflow: "auto", height: "100%" }}>{right}</div>
    </Split>
  );
}
