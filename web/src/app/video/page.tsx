"use client";

import { useEffect, useMemo, useState } from "react";
import { LoaderCircle, Play, RefreshCw, Square, Video } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { cancelVideoTask, createVideoGenerationTask, fetchVideoTasks, getVideoContentUrl, type VideoTask } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

const runningStates = new Set(["queued", "running"]);

function statusVariant(status?: string) {
  const value = String(status || "").toLowerCase();
  if (value === "success") return "success" as const;
  if (["failed", "error", "cancelled"].includes(value)) return "danger" as const;
  if (runningStates.has(value)) return "warning" as const;
  return "outline" as const;
}

function timeLabel(value?: string) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN");
}

function durationLabel(ms?: number) {
  return typeof ms === "number" && ms >= 0 ? `${(ms / 1000).toFixed(1)} s` : "—";
}

function VideoContent() {
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("sora");
  const [size, setSize] = useState("1280x720");
  const [seconds, setSeconds] = useState(5);
  const [items, setItems] = useState<VideoTask[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [loading, setLoading] = useState(true);

  const runningIds = useMemo(() => items.filter((item) => runningStates.has(String(item.status).toLowerCase())).map((item) => item.id), [items]);

  const load = async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const data = await fetchVideoTasks();
      setItems(data.items);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载视频任务失败");
    } finally {
      if (!silent) setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);
  useEffect(() => {
    if (!runningIds.length) return;
    const timer = window.setInterval(() => void load(true), 2500);
    return () => window.clearInterval(timer);
  }, [runningIds.length]);

  const submit = async () => {
    const value = prompt.trim();
    if (!value) return;
    setSubmitting(true);
    try {
      const task = await createVideoGenerationTask({
        client_task_id: `video_${Date.now()}`,
        prompt: value,
        model,
        size,
        seconds,
      });
      setItems((current) => [task, ...current.filter((item) => item.id !== task.id)]);
      toast.success("视频任务已创建");
      setPrompt("");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "创建视频任务失败");
    } finally {
      setSubmitting(false);
    }
  };

  const cancel = async (id: string) => {
    try {
      const task = await cancelVideoTask(id);
      setItems((current) => current.map((item) => (item.id === id ? task : item)));
      toast.success("任务已取消");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "取消失败");
    }
  };

  if (loading) return <div className="flex min-h-[40vh] items-center justify-center"><LoaderCircle className="size-5 animate-spin text-stone-400" /></div>;

  return (
    <section className="mx-auto flex w-full max-w-[1400px] flex-col gap-5 px-4 py-6 md:px-8">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="space-y-1">
          <div className="text-xs font-semibold tracking-[0.18em] text-stone-500 uppercase">Video</div>
          <h1 className="text-2xl font-semibold tracking-tight text-stone-950">视频生成</h1>
          <p className="max-w-3xl text-sm leading-6 text-stone-500">统一 `/v1/videos/*` 任务接口；未配置视频后端时会明确失败，不再伪装成功。</p>
        </div>
        <Button variant="outline" className="rounded-xl bg-white/80" onClick={() => void load()}><RefreshCw className="size-4" />刷新</Button>
      </div>

      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="grid gap-4 p-5 lg:grid-cols-[1fr_180px_160px_120px_auto]">
          <Textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} placeholder="描述要生成的视频..." className="min-h-28 rounded-2xl bg-white lg:col-span-1" />
          <Input value={model} onChange={(e) => setModel(e.target.value)} placeholder="model" className="h-11 rounded-xl bg-white" />
          <Input value={size} onChange={(e) => setSize(e.target.value)} placeholder="size" className="h-11 rounded-xl bg-white" />
          <Input type="number" value={seconds} min={1} max={60} onChange={(e) => setSeconds(Math.max(1, Number(e.target.value) || 1))} className="h-11 rounded-xl bg-white" />
          <Button className="h-11 rounded-xl bg-stone-950 px-5 text-white hover:bg-stone-800" onClick={() => void submit()} disabled={submitting || !prompt.trim()}>
            {submitting ? <LoaderCircle className="size-4 animate-spin" /> : <Play className="size-4" />}生成
          </Button>
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        {items.map((item) => (
          <Card key={item.id} className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
            <CardContent className="space-y-4 p-5">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="font-mono text-xs text-stone-400">{item.id}</div>
                  <div className="mt-1 flex items-center gap-2"><Video className="size-4 text-stone-500" /><span className="font-medium">{item.model || "sora"}</span><Badge variant={statusVariant(item.status)}>{item.status}</Badge></div>
                </div>
                {runningStates.has(String(item.status).toLowerCase()) ? <Button variant="outline" className="rounded-xl text-rose-600" onClick={() => void cancel(item.id)}><Square className="size-4" />取消</Button> : null}
              </div>
              <p className="whitespace-pre-wrap rounded-xl bg-stone-50 p-3 text-sm leading-6 text-stone-600">{item.prompt || "—"}</p>
              <div className="grid grid-cols-2 gap-3 text-xs text-stone-500 md:grid-cols-4">
                <div><div className="text-stone-400">进度</div><div>{item.progress || "—"}</div></div>
                <div><div className="text-stone-400">规格</div><div>{item.size || "—"} · {item.seconds || "—"}s</div></div>
                <div><div className="text-stone-400">耗时</div><div>{durationLabel(item.duration_ms)}</div></div>
                <div><div className="text-stone-400">更新时间</div><div>{timeLabel(item.updated_at)}</div></div>
              </div>
              {item.error ? <div className="rounded-xl border border-rose-100 bg-rose-50 px-3 py-2 text-sm text-rose-700">{item.error}</div> : null}
              {item.status === "success" ? (
                <video controls className="w-full rounded-xl bg-black" src={getVideoContentUrl(item.id)} />
              ) : null}
            </CardContent>
          </Card>
        ))}
      </div>
      {items.length === 0 ? <div className="rounded-2xl border border-dashed border-stone-200 bg-white/70 py-16 text-center text-sm text-stone-500">暂无视频任务。</div> : null}
    </section>
  );
}

export default function VideoPage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin", "user"]);
  if (isCheckingAuth || !session) return <div className="flex min-h-[40vh] items-center justify-center"><LoaderCircle className="size-5 animate-spin text-stone-400" /></div>;
  return <VideoContent />;
}
