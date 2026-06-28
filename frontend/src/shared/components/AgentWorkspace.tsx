import { Alert, Badge, Button, Spin, Typography } from "antd";
import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import api from "@shared/api/client";
import type { DocumentType, Task } from "@shared/types";
import OpenSeadragonViewer from "./ImageViewer/OpenSeadragonViewer";
import SchemaForm from "./SchemaForm";
import SplitWorkspace from "./SplitWorkspace";

interface Props {
  task: Task;
  onComplete?: () => void;
}

export default function AgentWorkspace({ task, onComplete }: Props) {
  const qc = useQueryClient();
  const [localStatus, setLocalStatus] = useState(task.status);

  // Fetch image URL — enabled once task is in_progress (either from prop or after start)
  const { data: viewData, isLoading: viewLoading } = useQuery({
    queryKey: ["record-view-url", task.record_id],
    queryFn: () =>
      api.get<{ view_url: string }>(`/records/${task.record_id}/view-url`).then((r) => r.data),
    enabled: localStatus === "in_progress",
  });

  // Fetch record for locked_by info and existing data
  const { data: record } = useQuery({
    queryKey: ["record", task.record_id],
    queryFn: () => api.get(`/records/${task.record_id}`).then((r) => r.data),
    refetchInterval: 10_000,
  });

  // Fetch document type schema
  const { data: batchData } = useQuery({
    queryKey: ["batch", task.batch_id],
    queryFn: () => api.get(`/batches/${task.batch_id}`).then((r) => r.data),
  });

  const { data: docType } = useQuery<DocumentType>({
    queryKey: ["doctype", batchData?.document_type_id],
    queryFn: () =>
      api.get(`/document-types/${batchData.document_type_id}`).then((r) => r.data),
    enabled: !!batchData?.document_type_id,
  });

  // Start task (acquires lock) — update localStatus so the workspace transitions immediately
  const startMutation = useMutation({
    mutationFn: () => api.post(`/tasks/${task.id}/start`),
    onSuccess: () => {
      setLocalStatus("in_progress");
      qc.invalidateQueries({ queryKey: ["record", task.record_id] });
    },
  });

  // Complete task
  const completeMutation = useMutation({
    mutationFn: (indexed_data: Record<string, unknown>) =>
      api.post(`/tasks/${task.id}/complete`, { indexed_data }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["record", task.record_id] });
      onComplete?.();
    },
  });

  const isLockedByOther = record?.locked_by && record.locked_by !== task.assigned_to;
  const isMyTask = localStatus === "in_progress";

  if (localStatus === "pending") {
    return (
      <div style={{ padding: 24 }}>
        <Typography.Title level={4}>Record #{task.record_id}</Typography.Title>
        <Button type="primary" onClick={() => startMutation.mutate()} loading={startMutation.isPending}>
          Start Indexing
        </Button>
      </div>
    );
  }

  return (
    <div style={{ height: "100vh", display: "flex", flexDirection: "column" }}>
      {/* Lock status banner */}
      {isLockedByOther && (
        <Alert
          type="error"
          message={`This record is locked by another user (ID: ${record.locked_by}). Please contact your supervisor.`}
          banner
        />
      )}
      {isMyTask && !isLockedByOther && (
        <Alert type="info" message="Record locked by you — complete or abandon to release." banner />
      )}
      {record?.current_version > 1 && (
        <Alert
          type="warning"
          message={`Rework — this is version ${record.current_version}. Previous data is pre-filled.`}
          banner
        />
      )}

      <div style={{ flex: 1, overflow: "hidden" }}>
        <SplitWorkspace
          left={
            viewLoading ? (
              <Spin />
            ) : viewData?.view_url ? (
              <OpenSeadragonViewer imageUrl={viewData.view_url} />
            ) : (
              <div style={{ padding: 24, color: "#ccc" }}>No image available</div>
            )
          }
          right={
            <div style={{ padding: 16 }}>
              <Typography.Title level={5}>
                Data Entry — Record #{task.record_id}{" "}
                <Badge count={`v${record?.current_version ?? 1}`} style={{ background: "#108ee9" }} />
              </Typography.Title>
              {docType ? (
                <SchemaForm
                  schema={docType.json_schema as any}
                  initialValues={record?.indexed_data ?? undefined}
                  onSubmit={(values) => completeMutation.mutate(values)}
                  loading={completeMutation.isPending}
                  submitLabel="Submit & Complete"
                />
              ) : (
                <Spin />
              )}
            </div>
          }
        />
      </div>
    </div>
  );
}
