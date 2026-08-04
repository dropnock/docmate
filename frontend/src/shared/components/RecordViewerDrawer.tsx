import { Descriptions, Drawer, Spin, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import api from "@shared/api/client";
import { useRecordImage } from "@shared/hooks/useRecordImage";
import type { DocRecord } from "@shared/types";
import OpenSeadragonViewer from "./ImageViewer/OpenSeadragonViewer";

interface Props {
  recordId: number | null;
  onClose: () => void;
}

/** Read-only record viewer reused wherever a supervisor/agent needs to look
 * at a record's image and current data outside the indexing/QA workspace
 * itself — same image-fetch path AgentWorkspace.tsx uses (useRecordImage,
 * which proxies through the backend) and never the presigned-URL endpoint. */
export default function RecordViewerDrawer({ recordId, onClose }: Props) {
  const {
    data: viewData,
    isLoading: viewLoading,
    page,
    setPage,
    pageCount,
  } = useRecordImage(recordId ?? undefined, recordId != null);

  const { data: record, isLoading: recordLoading } = useQuery<DocRecord>({
    queryKey: ["record", recordId],
    queryFn: () => api.get(`/records/${recordId}`).then((r) => r.data),
    enabled: recordId != null,
  });

  return (
    <Drawer
      title={record?.original_filename ?? `Record #${recordId}`}
      open={recordId != null}
      onClose={onClose}
      width={860}
    >
      <div style={{ height: 420, border: "1px solid #E2E8F0", marginBottom: 16 }}>
        {viewLoading ? (
          <Spin style={{ margin: 40 }} />
        ) : viewData?.objectUrl ? (
          viewData.contentType === "application/pdf" ? (
            <iframe
              src={viewData.objectUrl}
              style={{ width: "100%", height: "100%", border: "none" }}
              title={record?.original_filename ?? `Record ${recordId}`}
            />
          ) : (
            <OpenSeadragonViewer
              imageUrl={viewData.objectUrl}
              page={page}
              pageCount={pageCount}
              onPageChange={setPage}
            />
          )
        ) : (
          <div style={{ padding: 24, color: "#64748B" }}>No file attached to this record.</div>
        )}
      </div>

      {recordLoading ? (
        <Spin />
      ) : (
        <>
          <Descriptions size="small" column={2} bordered style={{ marginBottom: 16 }}>
            <Descriptions.Item label="Status">
              <Tag>{record?.status.replace(/_/g, " ")}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="Version">{record?.current_version}</Descriptions.Item>
            <Descriptions.Item label="Indexed By">{record?.indexed_by_name ?? "—"}</Descriptions.Item>
            <Descriptions.Item label="QA'd By">{record?.qa_by_name ?? "—"}</Descriptions.Item>
          </Descriptions>

          <Typography.Text strong style={{ display: "block", marginBottom: 4 }}>
            Indexed Data
          </Typography.Text>
          <pre style={{ fontSize: 12, background: "#F8FAFC", padding: 12, borderRadius: 4 }}>
            {JSON.stringify(record?.indexed_data ?? {}, null, 2)}
          </pre>
        </>
      )}
    </Drawer>
  );
}
