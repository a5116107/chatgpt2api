"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  Check,
  Copy,
  Download,
  LoaderCircle,
  MessageSquarePlus,
  PanelLeft,
  Pencil,
  RefreshCw,
  Search,
  Sparkles,
  Square,
  Trash2,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { toast } from "sonner";
import { useConfirmation } from "@/components/confirm-dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import {
  createConversation,
  deleteConversation,
  exportConversation,
  fetchConversation,
  fetchConversations,
  fetchModels,
  sendChatMessage,
  updateConversation,
  type Conversation,
} from "@/lib/api";
import { creativeErrorSummary } from "@/lib/creative-error";
import { useAuthGuard } from "@/lib/use-auth-guard";
import { cn } from "@/lib/utils";

function CopyMessage({ content }: { content: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );
  return (
    <Button
      variant="ghost"
      size="icon"
      className="size-8 text-muted-foreground"
      aria-label={copied ? "已复制" : "复制消息"}
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(content);
          setCopied(true);
          if (timer.current) clearTimeout(timer.current);
          timer.current = setTimeout(() => setCopied(false), 1600);
        } catch {
          toast.error("复制失败，请手动选择文本复制");
        }
      }}
    >
      {copied ? <Check className="size-4" /> : <Copy className="size-4" />}
    </Button>
  );
}

function ConversationsContent() {
  const { confirm, confirmationDialog } = useConfirmation();
  const [items, setItems] = useState<Conversation[]>([]);
  const [active, setActive] = useState<Conversation | null>(null);
  const [activeId, setActiveId] = useState("");
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("auto");
  const [models, setModels] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [listError, setListError] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [query, setQuery] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyHidden, setHistoryHidden] = useState(false);
  const [rename, setRename] = useState<string | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [away, setAway] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const sendingRef = useRef(false);
  const requestVersion = useRef(0);
  const mounted = useRef(true);
  const viewport = useRef<HTMLDivElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const stickToBottom = useRef(true);
  const draftPrompts = useRef(new Map<string, string>());

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      controller.current?.abort();
    };
  }, []);
  const loadList = useCallback(
    () =>
      fetchConversations({ limit: 200 })
        .then((data) => {
          if (mounted.current) {
            setItems(data.items);
            setListError("");
          }
        })
        .catch((e: unknown) => {
          if (mounted.current) setListError(e instanceof Error ? e.message : "读取对话失败");
        })
        .finally(() => {
          if (mounted.current) setLoading(false);
        }),
    [],
  );
  useEffect(() => {
    void loadList();
    void fetchModels()
      .then((data) => {
        if (mounted.current)
          setModels(
            data.data
              .map((item) => item.id)
              .filter((id) => !/image|video|sora|audio|whisper|tts|embedding/i.test(id)),
          );
      })
      .catch(() => {});
  }, [loadList]);
  useEffect(() => {
    const el = textarea.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = Math.min(el.scrollHeight, 180) + "px";
    }
  }, [prompt]);
  const messages = useMemo(() => active?.messages || [], [active]);
  useEffect(() => {
    if (stickToBottom.current && viewport.current)
      viewport.current.scrollTop = viewport.current.scrollHeight;
  }, [messages]);

  const selectConversation = async (id: string) => {
    if (sendingRef.current) return;
    draftPrompts.current.set(activeId, prompt);
    const version = ++requestVersion.current;
    setActiveId(id);
    setActive(null);
    setError("");
    setNotice("");
    setDetailError("");
    setHistoryOpen(false);
    setPrompt(draftPrompts.current.get(id) || "");
    stickToBottom.current = true;
    setAway(false);
    if (!id) {
      setDetailLoading(false);
      textarea.current?.focus();
      return;
    }
    setDetailLoading(true);
    try {
      const item = await fetchConversation(id);
      if (version === requestVersion.current && mounted.current) {
        setActive(item);
        setModel(item.model || "auto");
      }
    } catch (e) {
      if (version === requestVersion.current && mounted.current)
        setDetailError(e instanceof Error ? e.message : "读取对话失败");
    } finally {
      if (version === requestVersion.current && mounted.current) setDetailLoading(false);
    }
  };

  const send = async () => {
    const content = prompt.trim();
    if (!content || sendingRef.current || detailLoading || (activeId && !active)) return;
    sendingRef.current = true;
    setSending(true);
    setError("");
    setNotice("");
    const abort = new AbortController();
    controller.current = abort;
    const selectedModel = model.trim() || "auto";
    let conversation = active;
    let assistantId = "";
    let reply = "";
    let optimistic: Conversation | null = null;
    try {
      if (!conversation)
        conversation = await createConversation({
          title: content.slice(0, 40),
          model: selectedModel,
          source: "playground",
        });
      if (!mounted.current || abort.signal.aborted) return;
      setActiveId(conversation.id);
      const userId = crypto.randomUUID();
      assistantId = crypto.randomUUID();
      const context = (conversation.messages || [])
        .filter((m) => m.metadata?.status !== "failed")
        .map((m) => ({ role: m.role, content: m.content }));
      optimistic = {
        ...conversation,
        model: selectedModel,
        messages: [
          ...(conversation.messages || []),
          { id: userId, role: "user", content },
          { id: assistantId, role: "assistant", content: "" },
        ],
      };
      setActive(optimistic);
      setPrompt("");
      draftPrompts.current.delete(activeId);
      stickToBottom.current = true;
      await sendChatMessage(
        {
          conversation_id: conversation.id,
          model: selectedModel,
          messages: [...context, { role: "user", content }],
        },
        {
          signal: abort.signal,
          onDelta: (delta) => {
            reply += delta;
            if (mounted.current)
              setActive(
                (current) =>
                  current && {
                    ...current,
                    messages: current.messages?.map((m) =>
                      m.id === assistantId ? { ...m, content: reply } : m,
                    ),
                  },
              );
          },
        },
      );
      if (mounted.current) {
        const fresh = await fetchConversation(conversation.id);
        if (mounted.current) {
          setActive(fresh);
          setItems((current) => [fresh, ...current.filter((item) => item.id !== fresh.id)]);
        }
      }
    } catch (e) {
      if (!mounted.current) return;
      if (abort.signal.aborted)
        setNotice("已停止接收回复。服务端任务可能仍在结束处理中，已接收内容保留在当前页面。");
      else {
        setError(e instanceof Error ? e.message : "发送失败");
        setPrompt((current) => current || content);
      }
    } finally {
      if (mounted.current) {
        if (optimistic && conversation) {
          const local = {
            ...optimistic,
            messages: optimistic.messages
              ?.filter((m) => m.id !== assistantId || reply)
              .map((m) => (m.id === assistantId ? { ...m, content: reply } : m)),
          };
          setActive((current) =>
            current?.id === local.id && current.messages?.some((m) => m.id === assistantId)
              ? local
              : current,
          );
          void loadList();
        }
        sendingRef.current = false;
        setSending(false);
        controller.current = null;
      }
    }
  };

  const remove = async () => {
    if (
      !active ||
      sendingRef.current ||
      !(await confirm({
        title: "删除这段对话？",
        description: "对话和消息记录将被删除，请先导出需要保留的内容。",
      }))
    )
      return;
    try {
      await deleteConversation(active.id);
      setItems((current) => current.filter((item) => item.id !== active.id));
      draftPrompts.current.delete(active.id);
      setPrompt("");
      setActive(null);
      setActiveId("");
      setError("");
      setNotice("");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "删除失败");
    }
  };
  const download = async () => {
    if (!active || exporting) return;
    setExporting(true);
    try {
      const blob = await exportConversation(active.id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = (active.title || "conversation").replace(/[\\/:*?"<>|]/g, "_") + ".md";
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "导出失败");
    } finally {
      setExporting(false);
    }
  };
  const history = (
    <div className="studio-history">
      <div className="flex items-center justify-between gap-2 px-2 pb-3">
        <span className="text-sm font-semibold">对话</span>
        <Button
          variant="ghost"
          size="icon"
          aria-label="刷新会话列表"
          disabled={loading || sending}
          onClick={() => void loadList()}
        >
          <RefreshCw className={cn("size-4", loading && "animate-spin")} />
        </Button>
      </div>
      <Button
        variant="outline"
        className="w-full justify-start rounded-xl"
        disabled={sending}
        onClick={() => void selectConversation("")}
      >
        <MessageSquarePlus className="size-4" />
        新建对话
      </Button>
      <div className="relative my-3">
        <Search className="absolute top-3 left-3 size-4 text-muted-foreground" />
        <Input
          aria-label="搜索对话"
          placeholder="搜索对话"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="rounded-xl border-0 bg-background/60 pl-9 shadow-none"
        />
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto space-y-1" aria-label="对话历史">
        {loading && (
          <p className="p-3 text-sm text-muted-foreground" role="status">
            正在读取对话…
          </p>
        )}
        {listError && (
          <div role="alert" className="p-3 text-sm text-destructive">
            {creativeErrorSummary(listError)}
            <Button variant="ghost" onClick={() => void loadList()}>
              重试
            </Button>
          </div>
        )}
        {!loading &&
          !listError &&
          items
            .filter((item) => (item.title || item.id).toLowerCase().includes(query.toLowerCase()))
            .map((item) => (
              <button
                type="button"
                key={item.id}
                disabled={sending}
                aria-current={activeId === item.id ? "true" : undefined}
                className={cn("studio-history-item", activeId === item.id && "is-selected")}
                onClick={() => void selectConversation(item.id)}
              >
                <span className="block truncate text-sm">{item.title || "未命名对话"}</span>
                <span className="mt-1 block text-[11px] text-muted-foreground">
                  {item.message_count ?? 0} 条消息 ·{" "}
                  {item.updated_at ? new Date(item.updated_at).toLocaleDateString("zh-CN") : "刚刚"}
                </span>
              </button>
            ))}
        {!loading &&
          !listError &&
          !items.some((item) =>
            (item.title || item.id).toLowerCase().includes(query.toLowerCase()),
          ) && (
            <p className="px-3 py-8 text-center text-xs leading-6 text-muted-foreground">
              {query ? "没有找到相关对话" : "从一个问题开始，对话会保存在这里。"}
            </p>
          )}
      </div>
      <p className="px-2 pt-4 text-[11px] text-muted-foreground">对话历史保存在服务端</p>
    </div>
  );

  return (
    <section
      className={cn("studio-layout", historyHidden && "studio-history-hidden")}
      aria-label="对话工作区"
    >
      {confirmationDialog}
      <aside className="studio-desktop-history">{history}</aside>
      <Sheet open={historyOpen} onOpenChange={setHistoryOpen}>
        <SheetContent side="left" className="w-[290px] gap-0 p-0">
          <SheetHeader className="sr-only">
            <SheetTitle>对话历史</SheetTitle>
            <SheetDescription>搜索、切换或新建对话</SheetDescription>
          </SheetHeader>
          <div className="min-h-0 flex-1 pt-10">{history}</div>
        </SheetContent>
      </Sheet>
      <div className="studio-main">
        <header className="studio-toolbar">
          <Button
            variant="ghost"
            size="icon"
            className="hidden lg:inline-flex"
            aria-label={historyHidden ? "展开对话历史" : "收起对话历史"}
            onClick={() => setHistoryHidden((value) => !value)}
          >
            <PanelLeft className="size-5" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="lg:hidden"
            aria-label="打开对话历史"
            onClick={() => setHistoryOpen(true)}
          >
            <PanelLeft className="size-5" />
          </Button>
          <h1 className="min-w-0 flex-1 truncate text-base font-semibold">
            {active?.title || "新对话"}
          </h1>
          {active && (
            <>
              <Button
                variant="ghost"
                size="icon"
                aria-label="重命名对话"
                disabled={sending}
                onClick={() => setRename(active.title)}
              >
                <Pencil className="size-4" />
              </Button>
              <Button
                variant="ghost"
                size="icon"
                aria-label="导出对话"
                disabled={sending || exporting}
                onClick={() => void download()}
              >
                {exporting ? (
                  <LoaderCircle className="size-4 animate-spin" />
                ) : (
                  <Download className="size-4" />
                )}
              </Button>
              <Button
                variant="ghost"
                size="icon"
                aria-label="删除对话"
                disabled={sending}
                onClick={() => void remove()}
              >
                <Trash2 className="size-4" />
              </Button>
            </>
          )}
        </header>
        <div className="relative min-h-0 flex-1">
          <div
            ref={viewport}
            className="studio-scroll"
            aria-label="对话消息"
            aria-busy={detailLoading}
            onScroll={(e) => {
              const el = e.currentTarget;
              stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 100;
              setAway(!stickToBottom.current);
            }}
          >
            {detailLoading ? (
              <div className="studio-empty" role="status">
                <LoaderCircle className="size-6 animate-spin" />
                <p>正在打开对话…</p>
              </div>
            ) : detailError ? (
              <div className="studio-empty" role="alert">
                <p>{creativeErrorSummary(detailError)}</p>
                <Button onClick={() => void selectConversation(activeId)}>重新加载</Button>
              </div>
            ) : messages.length === 0 ? (
              <div className="studio-empty">
                <span className="studio-empty-mark">
                  <Sparkles className="size-7" strokeWidth={1.4} />
                </span>
                <h2>今天有什么想法？</h2>
                <p>提一个问题，或一起把想法变成方案。</p>
                <div className="studio-suggestions">
                  {[
                    "帮我把一个想法整理成行动计划",
                    "解释一个复杂概念，并举个例子",
                    "帮我润色一段文字",
                  ].map((text) => (
                    <button
                      key={text}
                      type="button"
                      onClick={() => {
                        setPrompt(text);
                        textarea.current?.focus();
                      }}
                    >
                      {text}
                      <ArrowUp className="size-3.5" />
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <div className="studio-thread">
                {messages.map((message) => (
                  <article
                    key={message.id}
                    className={cn("chat-message", message.role === "user" && "chat-message-user")}
                    aria-label={message.role === "user" ? "你的消息" : "助手消息"}
                  >
                    {message.role === "user" ? (
                      <div className="chat-user-bubble">{message.content}</div>
                    ) : message.metadata?.status === "failed" ? (
                      <div className="creative-error">
                        <p>{creativeErrorSummary(message.content)}</p>
                      </div>
                    ) : (
                      <>
                        <div className="mb-3 flex items-center gap-2 text-xs font-semibold text-muted-foreground">
                          <Sparkles className="size-4" />
                          {message.role === "system" ? "系统" : "ChatGPT2API"}
                        </div>
                        {message.content ? (
                          <div className="chat-markdown">
                            <ReactMarkdown
                              remarkPlugins={[remarkGfm]}
                              components={{
                                a: (props) => (
                                  <a {...props} target="_blank" rel="noopener noreferrer" />
                                ),
                              }}
                            >
                              {message.content}
                            </ReactMarkdown>
                          </div>
                        ) : (
                          <div
                            role="status"
                            className="flex items-center gap-2 text-sm text-muted-foreground"
                          >
                            <LoaderCircle className="size-4 animate-spin" />
                            正在思考…
                          </div>
                        )}
                      </>
                    )}
                    {message.content && message.metadata?.status !== "failed" && (
                      <div className="chat-message-actions">
                        <CopyMessage content={message.content} />
                      </div>
                    )}
                  </article>
                ))}
              </div>
            )}
          </div>
          {away && (
            <Button
              variant="outline"
              size="icon"
              className="studio-jump"
              aria-label="滚动到最新消息"
              onClick={() => {
                stickToBottom.current = true;
                setAway(false);
                viewport.current?.scrollTo({
                  top: viewport.current.scrollHeight,
                  behavior: "smooth",
                });
              }}
            >
              <ArrowDown className="size-4" />
            </Button>
          )}
        </div>
        <div className="studio-composer-wrap">
          <div className="mx-auto w-full max-w-[800px]">
            {error && (
              <div className="creative-error mb-3" role="alert">
                <p>{creativeErrorSummary(error)}</p>
                <details>
                  <summary>查看错误详情</summary>
                  <pre>{error.slice(0, 8000)}</pre>
                </details>
                <button
                  type="button"
                  className="mt-2 text-xs underline"
                  onClick={() => {
                    setError("");
                    textarea.current?.focus();
                  }}
                >
                  修改后重试
                </button>
              </div>
            )}
            {notice && (
              <p className="mb-3 text-xs leading-5 text-muted-foreground" role="status">
                {notice}
              </p>
            )}
            <div className="studio-composer">
              <Textarea
                ref={textarea}
                aria-label="对话内容"
                placeholder="向 ChatGPT2API 提问"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                onKeyDown={(e) => {
                  if (
                    e.key === "Enter" &&
                    !e.shiftKey &&
                    !e.nativeEvent.isComposing &&
                    e.keyCode !== 229
                  ) {
                    e.preventDefault();
                    void send();
                  }
                }}
                className="studio-textarea"
              />
              <div className="flex items-center justify-between gap-3 px-3 pb-3">
                <div className="flex min-w-0 items-center gap-2">
                  <label htmlFor="conversation-model" className="text-xs text-muted-foreground">
                    模型
                  </label>
                  <Input
                    id="conversation-model"
                    list="conversation-models"
                    aria-label="对话模型"
                    value={model}
                    disabled={sending}
                    onChange={(e) => setModel(e.target.value)}
                    className="h-8 w-[min(200px,40vw)] rounded-lg border-0 bg-transparent px-2 text-xs shadow-none"
                  />
                  <datalist id="conversation-models">
                    <option value="auto">自动选择</option>
                    {models.map((id) => (
                      <option key={id} value={id} />
                    ))}
                  </datalist>
                </div>
                {sending ? (
                  <Button
                    size="icon"
                    className="rounded-full"
                    aria-label="停止接收回复"
                    onClick={() => controller.current?.abort()}
                  >
                    <Square className="size-4 fill-current" />
                  </Button>
                ) : (
                  <Button
                    size="icon"
                    className="rounded-full"
                    aria-label="发送消息"
                    disabled={!prompt.trim() || detailLoading || Boolean(activeId && !active)}
                    onClick={() => void send()}
                  >
                    <ArrowUp className="size-5" />
                  </Button>
                )}
              </div>
            </div>
            <p className="studio-composer-hint">Enter 发送 · Shift + Enter 换行 · 回复请注意核实</p>
          </div>
        </div>
      </div>
      <Dialog
        open={rename !== null}
        onOpenChange={(open) => {
          if (!open && !renaming) setRename(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>重命名对话</DialogTitle>
            <DialogDescription>为这段对话起一个便于查找的名称。</DialogDescription>
          </DialogHeader>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              if (!active || !rename?.trim() || renaming) return;
              setRenaming(true);
              try {
                const fresh = await updateConversation(active.id, { title: rename.trim() });
                setActive(fresh);
                setItems((current) => current.map((item) => (item.id === fresh.id ? fresh : item)));
                setRename(null);
              } catch (err) {
                toast.error(err instanceof Error ? err.message : "重命名失败");
              } finally {
                setRenaming(false);
              }
            }}
          >
            <Input
              aria-label="对话名称"
              value={rename || ""}
              onChange={(e) => setRename(e.target.value)}
              autoFocus
              maxLength={100}
            />
            <DialogFooter className="mt-4">
              <Button
                type="button"
                variant="outline"
                disabled={renaming}
                onClick={() => setRename(null)}
              >
                取消
              </Button>
              <Button type="submit" disabled={!rename?.trim() || renaming}>
                保存
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
    </section>
  );
}

export default function ConversationsPage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin", "user"]);
  if (isCheckingAuth || !session)
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  return <ConversationsContent />;
}
