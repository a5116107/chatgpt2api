"use client";
import { useRef, useState } from "react";
import { LoaderCircle, MessageSquarePlus, Pencil, Search, Trash2, Check, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { getImageConversationStats, type ImageConversation } from "@/store/image-conversations";
type ImageSidebarProps = {
  conversations: ImageConversation[];
  isLoadingHistory: boolean;
  selectedConversationId: string | null;
  onCreateDraft: () => void;
  onClearHistory: () => void | Promise<void>;
  onSelectConversation: (id: string) => void;
  onDeleteConversation: (id: string) => void | Promise<void>;
  onRenameConversation: (id: string, title: string) => void | Promise<void>;
  formatConversationTime: (value: string) => string;
  hideActionButtons?: boolean;
};

export function ImageSidebar({
  conversations,
  isLoadingHistory,
  selectedConversationId,
  onCreateDraft,
  onClearHistory,
  onSelectConversation,
  onDeleteConversation,
  onRenameConversation,
  formatConversationTime,
  hideActionButtons = false,
}: ImageSidebarProps) {
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [saving, setSaving] = useState(false);
  const saveLock = useRef(false);
  const filtered = conversations.filter((conversation) =>
    (conversation.title + " " + conversation.turns.map((turn) => turn.prompt).join(" "))
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  return (
    <aside className="studio-history" aria-label="图像历史">
      <div className="flex items-center justify-between px-2 pb-3">
        <span className="text-sm font-semibold">图像创作</span>
        <span className="text-xs text-muted-foreground">{conversations.length} 个会话</span>
      </div>
      {!hideActionButtons && (
        <div className="flex gap-1">
          <Button
            variant="outline"
            className="flex-1 justify-start rounded-xl"
            onClick={onCreateDraft}
          >
            <MessageSquarePlus className="size-4" />
            新建创作
          </Button>
          <Button
            variant="ghost"
            size="icon"
            aria-label="清空图像历史"
            disabled={!conversations.length}
            onClick={() => void onClearHistory()}
          >
            <Trash2 className="size-4" />
          </Button>
        </div>
      )}
      <div className="relative my-3">
        <Search className="absolute top-3 left-3 size-4 text-muted-foreground" />
        <Input
          aria-label="搜索图像历史"
          placeholder="搜索标题或提示词"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="rounded-xl border-0 bg-background/60 pl-9 shadow-none"
        />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto space-y-1">
        {isLoadingHistory ? (
          <p role="status" className="flex items-center gap-2 p-3 text-xs text-muted-foreground">
            <LoaderCircle className="size-4 animate-spin" />
            正在读取记录…
          </p>
        ) : filtered.length === 0 ? (
          <p className="px-3 py-8 text-center text-xs leading-6 text-muted-foreground">
            {query ? "没有找到相关创作" : "用文字或参考图开始创作，历史会保存在这里。"}
          </p>
        ) : (
          filtered.map((conversation) => {
            const stats = getImageConversationStats(conversation);
            return (
              <div
                key={conversation.id}
                className={cn(
                  "studio-history-row group",
                  selectedConversationId === conversation.id && "is-selected",
                )}
              >
                {editing === conversation.id ? (
                  <form
                    className="space-y-2 p-2"
                    onSubmit={async (event) => {
                      event.preventDefault();
                      if (!title.trim() || saveLock.current) return;
                      saveLock.current = true;
                      setSaving(true);
                      try {
                        await onRenameConversation(conversation.id, title.trim());
                        setEditing(null);
                      } finally {
                        saveLock.current = false;
                        setSaving(false);
                      }
                    }}
                  >
                    <Input
                      aria-label="创作名称"
                      value={title}
                      autoFocus
                      maxLength={100}
                      disabled={saving}
                      onChange={(event) => setTitle(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Escape" && !saving) setEditing(null);
                      }}
                    />
                    <div className="flex justify-end gap-1">
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="size-7"
                        aria-label="取消重命名"
                        disabled={saving}
                        onClick={() => setEditing(null)}
                      >
                        <X className="size-3.5" />
                      </Button>
                      <Button
                        type="submit"
                        variant="ghost"
                        size="icon"
                        className="size-7"
                        aria-label="保存创作名称"
                        disabled={!title.trim() || saving}
                      >
                        <Check className="size-3.5" />
                      </Button>
                    </div>
                  </form>
                ) : (
                  <>
                    <button
                      type="button"
                      aria-current={selectedConversationId === conversation.id ? "true" : undefined}
                      className="studio-history-item pr-16"
                      onClick={() => onSelectConversation(conversation.id)}
                    >
                      <span className="block truncate text-sm">{conversation.title}</span>
                      <span className="mt-1 block truncate text-[11px] text-muted-foreground">
                        {conversation.turns.length} 轮 ·{" "}
                        {formatConversationTime(conversation.updatedAt)}
                      </span>
                      {stats.running + stats.queued > 0 && (
                        <span className="mt-1 inline-flex items-center gap-1 text-[11px] text-muted-foreground">
                          <LoaderCircle className="size-3 animate-spin" />
                          {stats.running ? "处理中 " + stats.running : "排队 " + stats.queued}
                        </span>
                      )}
                    </button>
                    <div className="studio-history-row-actions">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-7"
                        aria-label="重命名会话"
                        onClick={() => {
                          setEditing(conversation.id);
                          setTitle(conversation.title);
                        }}
                      >
                        <Pencil className="size-3.5" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="size-7"
                        aria-label="删除会话"
                        onClick={() => void onDeleteConversation(conversation.id)}
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    </div>
                  </>
                )}
              </div>
            );
          })
        )}
      </div>
      <p className="px-2 pt-4 text-[11px] text-muted-foreground">
        历史仅保存在当前浏览器，请及时下载图片
      </p>
    </aside>
  );
}
