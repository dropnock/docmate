import { useMutation, useQuery } from "@tanstack/react-query";
import { Alert, Button, Select, Space, Spin, Tooltip, Typography, message } from "antd";
import { BookOpen, Download, ExternalLink } from "lucide-react";
import { useSearchParams } from "react-router";
import { formatApiError } from "@shared/api/errors";
import { downloadHelpManualPdf, helpManualsKey, listHelpManuals, type HelpManual } from "@shared/api/help";

/** The manuals for the user's role, shown in a frame. The page itself is
 * built from manuals/ with the branded template, so its look is set there,
 * not by the app's styles. */
export default function Help() {
  const [params, setParams] = useSearchParams();
  const { data, isLoading, error } = useQuery({ queryKey: helpManualsKey, queryFn: listHelpManuals });

  const manuals = data?.manuals ?? [];
  const selected: HelpManual | undefined =
    manuals.find((m) => m.slug === params.get("manual")) ?? manuals[0];

  const pdf = useMutation({
    mutationFn: (m: HelpManual) => downloadHelpManualPdf(m),
    onError: (e: unknown) => message.error(formatApiError(e, "Couldn't prepare the PDF")),
  });

  if (isLoading) return <Spin style={{ display: "block", margin: "80px auto" }} />;
  if (error) {
    return (
      <div style={{ padding: 24 }}>
        <Alert type="error" showIcon message={formatApiError(error, "Couldn't load the manuals")} />
      </div>
    );
  }
  if (!selected) {
    return (
      <div style={{ padding: 24 }}>
        <Alert type="info" showIcon message="No manuals are available for your role." />
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 64px)" }}>
      <div
        style={{
          display: "flex", flexWrap: "wrap", alignItems: "center", gap: 12,
          padding: "10px 16px", background: "#FFFFFF", borderBottom: "1px solid #E2E8F0",
        }}
      >
        <Space size={8} align="center">
          <BookOpen size={18} color="#1E40AF" />
          <Typography.Text strong>Help</Typography.Text>
        </Space>
        <Select
          aria-label="Manual"
          style={{ minWidth: 280 }}
          value={selected.slug}
          onChange={(slug) => setParams({ manual: slug }, { replace: true })}
          options={manuals.map((m) => ({ value: m.slug, label: m.title }))}
        />
        <span style={{ flex: 1 }} />
        <Tooltip title="Open this manual in its own browser tab">
          <Button icon={<ExternalLink size={14} />} href={selected.html_url} target="_blank" rel="noopener">
            Open in new tab
          </Button>
        </Tooltip>
        <Tooltip title="A PDF copy prepared for your organisation">
          <Button
            type="primary"
            icon={<Download size={14} />}
            loading={pdf.isPending}
            onClick={() => pdf.mutate(selected)}
          >
            Download PDF
          </Button>
        </Tooltip>
      </div>
      <iframe
        key={selected.slug}
        title={selected.title}
        src={selected.html_url}
        style={{ flex: 1, width: "100%", border: 0, background: "#F8FAFC" }}
      />
    </div>
  );
}
