import { useState } from "react";
import { Alert, Button, Input, Modal, Space, Tooltip, Typography, message } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Flag } from "lucide-react";
import dayjs from "dayjs";
import { formatApiError } from "@shared/api/errors";
import {
  getRecordFlag, raiseRecordFlag, recordFlagKey, withdrawRecordFlag,
} from "@shared/api/flags";

function useRecordFlag(recordId: number) {
  return useQuery({
    queryKey: recordFlagKey(recordId),
    queryFn: () => getRecordFlag(recordId),
    refetchInterval: 30_000,
  });
}

/** Above the workspace: the open flag's note, or — once a supervisor has
 * dealt with an earlier flag — what they said, so the indexer sees the
 * answer where they work. */
export function RecordFlagBanner({ recordId }: { recordId: number }) {
  const { data } = useRecordFlag(recordId);
  const [hidden, setHidden] = useState<number | null>(null);
  if (data?.open) {
    return (
      <Alert
        type="warning"
        banner
        icon={<Flag size={14} />}
        showIcon
        message={`Flagged for a supervisor: ${data.open.reason}`}
      />
    );
  }
  const resolved = data?.last_resolved;
  if (resolved && hidden !== resolved.id) {
    return (
      <Alert
        type="success"
        banner
        closable
        onClose={() => setHidden(resolved.id)}
        message={`Supervisor resolved your flag (${dayjs(resolved.closed_at).format("D MMM, HH:mm")}): ${resolved.resolution}`}
      />
    );
  }
  return null;
}

/** Footer button: flag the record with a note for the supervisor, edit the
 * note, or take the flag down. Flagging doesn't stop the indexer saving or
 * submitting the record; it holds the batch back from Complete Batch until
 * the flag is resolved or withdrawn. */
export function RecordFlagButton({ recordId }: { recordId: number }) {
  const qc = useQueryClient();
  const { data } = useRecordFlag(recordId);
  const open = data?.open ?? null;
  const [editing, setEditing] = useState(false);
  const [reason, setReason] = useState("");

  const refresh = () => {
    qc.invalidateQueries({ queryKey: recordFlagKey(recordId) });
    qc.invalidateQueries({ queryKey: ["my-tasks"] });
    qc.invalidateQueries({ queryKey: ["records"] });
  };
  const raise = useMutation({
    mutationFn: () => raiseRecordFlag(recordId, reason),
    onSuccess: () => {
      message.success(open ? "Flag updated" : "Flagged for your supervisor");
      setEditing(false);
      refresh();
    },
    onError: (e: unknown) => message.error(formatApiError(e, "Couldn't flag the record")),
  });
  const withdraw = useMutation({
    mutationFn: () => withdrawRecordFlag(recordId),
    onSuccess: () => {
      message.success("Flag removed");
      setEditing(false);
      refresh();
    },
    onError: (e: unknown) => message.error(formatApiError(e, "Couldn't remove the flag")),
  });

  return (
    <>
      <Tooltip title={open ? "Flagged — click to change or remove" : "Flag an issue for your supervisor"}>
        <Button
          icon={<Flag size={14} fill={open ? "currentColor" : "none"} />}
          style={open ? { color: "#B45309", borderColor: "#F59E0B", background: "#FFFBEB" } : undefined}
          onClick={() => {
            setReason(open?.reason ?? "");
            setEditing(true);
          }}
        >
          {open ? "Flagged" : "Flag"}
        </Button>
      </Tooltip>
      <Modal
        title={open ? "Flagged for your supervisor" : "Flag an issue for your supervisor"}
        open={editing}
        onCancel={() => setEditing(false)}
        footer={
          <Space>
            {open && (
              <Button danger loading={withdraw.isPending} onClick={() => withdraw.mutate()}>
                Remove flag
              </Button>
            )}
            <Button onClick={() => setEditing(false)}>Cancel</Button>
            <Button
              type="primary"
              disabled={!reason.trim() || reason.trim() === open?.reason}
              loading={raise.isPending}
              onClick={() => raise.mutate()}
            >
              {open ? "Update" : "Flag"}
            </Button>
          </Space>
        }
        destroyOnClose
      >
        <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
          Say what the supervisor needs to look at. The record stays in your batch, and you can keep working
          on it; the batch can't be completed until the flag is resolved or removed.
        </Typography.Paragraph>
        <Input.TextArea
          autoFocus
          rows={4}
          maxLength={2000}
          showCount
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="e.g. Two caveat numbers on the card — which one should be used?"
        />
      </Modal>
    </>
  );
}
