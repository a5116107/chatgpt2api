"use client";

import { useEffect, useMemo, useState } from "react";
import { Download, LoaderCircle, MessageSquarePlus, RefreshCw, Send, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  createConversation,
  deleteConversation,
  fetchConversation,
  fetchConversations,
  getConversationExportUrl,
  sendChatMessage,
  type Conversation,
} from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";
import { cn } from "@/lib/utils";

function timeLabel(value?: string) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN");
}

function assistantText(response: Awaited<ReturnType<typeof sendChatMessage>>) {
  return response.choices?.[0]?.message?.content || "";
}

function ConversationsContent() {
  const [items, setItems] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState("");
  const [active, setActive] = useState<Conversation | null>(null);
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("gpt-5-5-thinking");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);

  const loadList = async (selectFirst = false) => {
    setLoading(true);
    try {
      const data = await fetchConversations({ limit: 200 });
      setItems(data.items);
      if (selectFirst && data.items[0]) setActiveId(data.items[0].id);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载对话失败");
    } finally {
      setLoading(false);
    }
  };

  const loadActive = async (id: string) => {
    if (!id) {
      setActive(null);
      return;
    }
    try {
      setActive(await fetchConversation(id));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载会话详情失败");
    }
  };

  useEffect(() => { void loadList(true); }, []);
  useEffect(() => { void loadActive(activeId); }, [activeId]);

  const messages = useMemo(() => active?.messages || [], [active]);

  const createNew = async () => {
    try {
      const item = await createConversation({ title: "新对话", model, source: "playground" });
      setItems((current) => [item, ...current]);
      setActiveId(item.id);
      setActive(item);
      toast.success("已创建新对话");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "创建对话失败");
    }
  };

  const send = async () => {
    const content = prompt.trim();
    if (!content) return;
    setSending(true);
    try {
      let conversation = active;
      if (!conversation) {
        conversation = await createConversation({ title: content.slice(0, 40), model, source: "playground" });
        setActiveId(conversation.id);
      }
      const response = await sendChatMessage({
        conversation_id: conversation.id,
        model,
        messages: [...(conversation.messages || []).map((m) => ({ role: m.role, content: m.content })), { role: "user", content }],
      });
      const fresh = await fetchConversation(conversation.id);
      setActive(fresh);
      setItems((current) => [fresh, ...current.filter((item) => item.id !== fresh.id)]);
      setPrompt("");
      const latest = fresh.messages?.slice().reverse().find((m) => m.role === "assistant")?.content || "";
      toast.success(latest || assistantText(response) || "回复已完成");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "发送失败");
    } finally {
      setSending(false);
    }
  };

  const remove = async (id: string) => {
    try {
      await deleteConversation(id);
      setItems((current) => current.filter((item) => item.id !== id));
      if (activeId === id) {
        setActiveId("");
        setActive(null);
      }
      toast.success("对话已删除");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除失败");
    }
  };

  if (loading) return <div className="flex min-h-[40vh] items-center justify-center"><LoaderCircle className="size-5 animate-spin text-stone-400" /></div>;

  return (
    <section className="mx-auto grid w-full max-w-[1600px] gap-5 px-4 py-6 md:px-8 lg:grid-cols-[360px_1fr]">
      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="flex h-[calc(100vh-120px)] flex-col p-0">
          <div className="flex items-center justify-between border-b border-stone-100 px-5 py-4">
            <div>
              <div className="text-xs font-semibold tracking-[0.18em] text-stone-500 uppercase">Conversations</div>
              <h1 className="text-xl font-semibold">对话会话</h1>
            </div>
            <div className="flex gap-2">
              <Button variant="outline" size="icon" className="rounded-xl" onClick={() => void loadList()}><RefreshCw className="size-4" /></Button>
              <Button size="icon" className="rounded-xl bg-stone-950 text-white" onClick={() => void createNew()}><MessageSquarePlus className="size-4" /></Button>
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-auto p-3">
            {items.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setActiveId(item.id)}
                className={cn("mb-2 w-full rounded-2xl border p-3 text-left transition", activeId === item.id ? "border-stone-950 bg-stone-950 text-white" : "border-stone-100 bg-white hover:border-stone-200")}
              >
                <div className="line-clamp-1 text-sm font-medium">{item.title || item.id}</div>
                <div className={cn("mt-1 flex items-center justify-between text-xs", activeId === item.id ? "text-stone-200" : "text-stone-400")}>
                  <span>{item.message_count ?? 0} messages</span>
                  <span>{timeLabel(item.updated_at)}</span>
                </div>
              </button>
            ))}
            {items.length === 0 ? <div className="py-12 text-center text-sm text-stone-500">暂无对话，点击右上角创建。</div> : null}
          </div>
        </CardContent>
      </Card>

      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="flex h-[calc(100vh-120px)] flex-col p-0">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-stone-100 px-5 py-4">
            <div>
              <div className="flex items-center gap-2"><h2 className="text-xl font-semibold">{active?.title || "Playground 对话"}</h2><Badge variant="outline">{active?.status || "ready"}</Badge></div>
              <div className="text-xs text-stone-400">{active?.id || "新消息会自动创建会话"} · {active?.model || model}</div>
            </div>
            <div className="flex gap-2">
              <Input value={model} onChange={(e) => setModel(e.target.value)} className="h-9 w-48 rounded-xl" />
              {active ? <Button variant="outline" className="rounded-xl" asChild><a href={getConversationExportUrl(active.id, "markdown")} target="_blank" rel="noreferrer"><Download className="size-4" />导出</a></Button> : null}
              {active ? <Button variant="outline" className="rounded-xl text-rose-600" onClick={() => void remove(active.id)}><Trash2 className="size-4" />删除</Button> : null}
            </div>
          </div>
          <div className="min-h-0 flex-1 space-y-3 overflow-auto bg-stone-50/60 p-5">
            {messages.map((message) => (
              <div key={message.id} className={cn("max-w-[86%] rounded-2xl px-4 py-3 text-sm leading-6 shadow-sm", message.role === "user" ? "ml-auto bg-stone-950 text-white" : "bg-white text-stone-700")}>
                <div className="mb-1 text-[11px] uppercase opacity-60">{message.role} · {timeLabel(message.created_at)}</div>
                <div className="whitespace-pre-wrap">{message.content}</div>
              </div>
            ))}
            {messages.length === 0 ? <div className="py-16 text-center text-sm text-stone-500">输入内容后将通过 `/v1/chat/completions` 写入会话。</div> : null}
          </div>
          <div className="border-t border-stone-100 p-4">
            <div className="flex gap-3">
              <Textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="输入对话内容..." className="min-h-24 flex-1 rounded-2xl bg-white" />
              <Button className="h-auto rounded-2xl bg-stone-950 px-5 text-white hover:bg-stone-800" onClick={() => void send()} disabled={sending || !prompt.trim()}>
                {sending ? <LoaderCircle className="size-5 animate-spin" /> : <Send className="size-5" />}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </section>
  );
}

export default function ConversationsPage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin", "user"]);
  if (isCheckingAuth || !session) return <div className="flex min-h-[40vh] items-center justify-center"><LoaderCircle className="size-5 animate-spin text-stone-400" /></div>;
  return <ConversationsContent />;
}
