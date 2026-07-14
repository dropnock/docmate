import { useEffect, useRef } from "react";
import OpenSeadragon from "openseadragon";
import ViewerToolbar from "./ViewerToolbar";

interface Props {
  imageUrl: string;
}

export default function OpenSeadragonViewer({ imageUrl }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<OpenSeadragon.Viewer | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const options = {
      element: containerRef.current,
      // OSD 5.x defaults to a WebGL renderer, which can lose its context on
      // constrained/virtualized GPUs (common on back-office/VDI machines) or
      // once the browser's limited pool of simultaneous WebGL contexts is
      // exhausted, leaving the image blank. Canvas 2D has no such failure
      // mode and is plenty fast for a document image viewer.
      // (@types/openseadragon@3 predates this option on OSD 5 — it's a real
      // constructor option at runtime, the types just haven't caught up.)
      drawer: "canvas",
      tileSources: {
        type: "image",
        url: imageUrl,
      },
      showNavigationControl: false, // we render our own toolbar
      gestureSettingsMouse: {
        clickToZoom: false,
        dblClickToZoom: true,
        scrollToZoom: true,
        pinchToZoom: true,
      },
      minZoomImageRatio: 0.5,
      maxZoomPixelRatio: 10,
      visibilityRatio: 0.5,
      animationTime: 0.3,
      defaultZoomLevel: 0,
    };
    viewerRef.current = OpenSeadragon(options);

    return () => {
      viewerRef.current?.destroy();
      viewerRef.current = null;
    };
  }, [imageUrl]);

  const zoomIn = () => viewerRef.current?.viewport.zoomBy(1.5);
  const zoomOut = () => viewerRef.current?.viewport.zoomBy(1 / 1.5);
  const fitPage = () => viewerRef.current?.viewport.goHome(true);
  const fitWidth = () => {
    const vp = viewerRef.current?.viewport;
    if (!vp) return;
    const bounds = vp.getBounds();
    vp.fitHorizontally(true);
  };
  const togglePan = () => {
    // Pan is always enabled in OSD via mouse drag; this is a no-op placeholder
    // for a toolbar button that could toggle between zoom-on-click vs pan-on-click
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <ViewerToolbar onZoomIn={zoomIn} onZoomOut={zoomOut} onFitPage={fitPage} onFitWidth={fitWidth} />
      <div ref={containerRef} style={{ flex: 1, background: "#1a1a1a" }} />
    </div>
  );
}
