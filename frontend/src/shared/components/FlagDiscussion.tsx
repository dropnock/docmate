import { useState } from "react";
import { Button, Empty, Input, List, Space, Tag, Typography, message } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs from "dayjs";
import { formatApiError } from "@shared/api/errors";
import { addFlagComment, flagCommentsKey, listFlagComments, type RecordFlagListItem } from "@shared/api/flags";

interface Props {
  flag: RecordFlagListItem;
  /** Who's looking — labels the other side's messages. */
  side: "digitizing" | "customer";
}

/** The discussion on a flag shared with the customer: the digitizing and
 * customer supervisors post messages; each one notifies the other side.
 * Read-only once the flag is closed. */
export default function FlagDiscussion({ flag, side }: Props) {
  const qc = useQueryClient();
  const [body, setBody] = useState("");
  const open = flag.status === "open";

  const { data: comments = [], isLoading } = useQuery({
    queryKey: flagCommentsKey(flag.id),
    queryFn: () => listFlagComments(flag.id),
    refetchInterval: 30_000,
  });

  const post = useMutation({
    mutationFn: () => addFlagComment(flag.id, body),
    onSuccess: () => {
      setBody("");
      qc.invalidateQueries({ queryKey: flagCommentsKey(flag.id) });
      qc.invalidateQueries({ queryKey: ["project-flags", flag.project_id] });
    },
    onError: (e: unknown) => message.error(formatApiError(e, "Couldn't post the message")),
  });

  return (
    <div>
      <Typography.Title level={5} style={{ marginTop: 0 }}>Discussion with the {side === "digitizing" ? "customer" : "digitization team"}</Typography.Title>
      <List
        size="small"
        loading={isLoading}
        dataSource={comments}
        locale={{ emptyText: <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No messages yet" /> }}
        renderItem={(c) => (
          <List.Item style={{ display: "block" }}>
            <Space size={6} wrap>
              <Typography.Text strong>{c.author_name}</Typography.Text>
              <Tag color={c.author_side === "customer" ? "gold" : "blue"}>
                {c.author_side === "customer" ? "Customer" : "Digitization"}
              </Tag>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {dayjs(c.created_at).format("D MMM YYYY, HH:mm")}
              </Typography.Text>
            </Space>
            <div style={{ whiteSpace: "pre-wrap", marginTop: 4 }}>{c.body}</div>
          </List.Item>
        )}
      />
      {open ? (
        <Space direction="vertical" style={{ width: "100%", marginTop: 8 }}>
          <Input.TextArea
            rows={3}
            maxLength={2000}
            showCount
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder={side === "customer" ? "How should the team process this record?" : "Reply to the customer"}
          />
          <Button type="primary" disabled={!body.trim()} loading={post.isPending} onClick={() => post.mutate()}>
            Post
          </Button>
        </Space>
      ) : (
        <Typography.Text type="secondary">This flag is closed, so the discussion is read-only.</Typography.Text>
      )}
    </div>
  );
}
