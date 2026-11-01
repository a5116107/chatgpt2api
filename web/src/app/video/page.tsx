"use client";

import { useEffect, useMemo, useState, useCallback, useRef, useTransition } from "react";
import {
  Clock3,
  Film,
  LoaderCircle,
  Play,
  RefreshCw,
  SlidersHorizontal,
  Square,
  Video,
} from "lucide-react";
import { toast } from "sonner";
import { ResourceError } from "@/components/resource-error";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  cancelVideoTask,
  createVideoGenerationTask,
  fetchVideoTasks,
  fetchVideoContent,
  type VideoTask,
} from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";
import { HttpRequestError } from "@/lib/request";

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

function VideoPreview({ taskId }: { taskId: string }) {
  const resource = useRef<{ controller: AbortController | null; url: string }>({
    controller: null,
    url: "",
  });
  const [url, setUrl] = useState("");
  const [extension, setExtension] = useState("mp4");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const current = resource.current;
    return () => {
      current.controller?.abort();
      if (current.url) URL.revokeObjectURL(current.url);
    };
  }, []);
  const load = async () => {
    const current = resource.current;
    if (current.controller) return;
    const controller = new AbortController();
    current.controller = controller;
    setLoading(true);
    setError("");
    try {
      const blob = await fetchVideoContent(taskId, controller.signal);
      controller.signal.throwIfAborted();
      if (!blob.size) throw new Error("视频文件为空，请检查该任务的结果。");
      if (blob.type && !blob.type.startsWith("video/") && blob.type !== "application/octet-stream")
        throw new Error("该任务未返回可播放的视频文件。");
      current.url = URL.createObjectURL(blob);
      setUrl(current.url);
      setExtension(blob.type.includes("webm") ? "webm" : "mp4");
    } catch (failure) {
      if (!controller.signal.aborted) {
        const status = failure instanceof HttpRequestError ? failure.status : undefined;
        setError(
          status === 401 || status === 403
            ? "该视频的访问凭据已失效或权限不足，请确认登录身份后重试。"
            : status === 404 || status === 410
              ? "视频文件不存在或已被清理，任务记录仍被保留。"
              : failure instanceof Error
                ? failure.message
                : "视频加载失败，请稍后重试。",
        );
      }
    } finally {
      if (current.controller === controller) {
        current.controller = null;
        if (!controller.signal.aborted) setLoading(false);
      }
    }
  };
  const release = () => {
    const current = resource.current;
    current.controller?.abort();
    current.controller = null;
    if (current.url) URL.revokeObjectURL(current.url);
    current.url = "";
    setUrl("");
    setLoading(false);
    setError("");
  };
  return (
    <div className="space-y-3" role="group" aria-label="视频预览">
      {url ? (
        <video
          controls
          preload="metadata"
          aria-label="任务视频预览"
          className="aspect-video w-full rounded-xl bg-black"
          src={url}
          onError={() => setError("当前浏览器未能播放此格式，可下载视频后查看。")}
        />
      ) : (
        <div className="flex aspect-video flex-col items-center justify-center gap-3 rounded-xl border border-dashed border-border bg-muted/50 p-5 text-center">
          <Video className="size-7 text-muted-foreground" aria-hidden="true" />
          <p className="text-xs text-muted-foreground">按需加载预览，不自动播放或批量下载视频。</p>
          {loading ? (
            <>
              <span role="status" className="flex items-center gap-2 text-sm">
                <LoaderCircle className="size-4 animate-spin" />
                正在加载视频…
              </span>
              <Button variant="outline" onClick={release}>
                取消加载
              </Button>
            </>
          ) : (
            <Button variant="outline" onClick={() => void load()}>
              <Play className="size-4" />
              {error ? "重试加载视频" : "加载视频预览"}
            </Button>
          )}
        </div>
      )}
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      {url && (
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" asChild>
            <a href={url} download={"video-" + taskId + "." + extension}>
              下载视频
            </a>
          </Button>
          <Button variant="ghost" onClick={release}>
            释放预览
          </Button>
        </div>
      )}
    </div>
  );
}

function VideoContent() {
  const loadRequest = useRef({ id: 0 });
  const [loadError, setLoadError] = useState("");
  const [refreshing, startRefresh] = useTransition();
  const [prompt, setPrompt] = useState("");
  const [model, setModel] = useState("sora");
  const [size, setSize] = useState("1280x720");
  const [seconds, setSeconds] = useState(5);
  const [items, setItems] = useState<VideoTask[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [loading, setLoading] = useState(true);

  const runningIds = useMemo(
    () =>
      items
        .filter((item) => runningStates.has(String(item.status).toLowerCase()))
        .map((item) => item.id),
    [items],
  );

  const load = useCallback(() => {
    const requestState = loadRequest.current;
    const requestId = ++requestState.id;
    return fetchVideoTasks()
      .then((data) => {
        if (requestId !== requestState.id) return;
        setLoadError("");
        setItems(data.items);
      })
      .catch((error: unknown) => {
        if (requestId === requestState.id)
          setLoadError(error instanceof Error ? error.message : "加载视频任务失败");
      })
      .finally(() => {
        if (requestId === requestState.id) setLoading(false);
      });
  }, []);

  useEffect(() => {
    const requestState = loadRequest.current;
    void load();
    return () => {
      requestState.id++;
    };
  }, [load]);
  useEffect(() => {
    if (!runningIds.length) return;
    const timer = window.setInterval(() => void load(), 2500);
    return () => window.clearInterval(timer);
  }, [load, runningIds.length]);

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

  if (loading)
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-muted-foreground" />
      </div>
    );

  if (loadError) return <ResourceError message={loadError} onRetry={() => load()} />;
  return (
    <section className="flex w-full min-w-0 flex-col gap-5">
      <div className="workbench-toolbar">
        <div className="space-y-1">
          <div className="page-kicker">Video studio</div>
          <h1 className="text-3xl font-semibold tracking-tight text-foreground">视频创作</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
            把想法变成可追踪的任务。预览按需加载，失败原因、耗时与规格都保留在每张任务卡中。
          </p>
        </div>
        <div className="video-hero-actions">
          <div className="video-hero-stat">
            <span>任务</span>
            <strong>{items.length}</strong>
          </div>
          <div className="video-hero-stat">
            <span>进行中</span>
            <strong>{runningIds.length}</strong>
          </div>
          <Button
            variant="outline"
            className="h-10 rounded-xl bg-card"
            onClick={() =>
              startRefresh(async () => {
                await load();
              })
            }
            disabled={refreshing}
          >
            <RefreshCw className={refreshing ? "size-4 animate-spin" : "size-4"} />
            刷新任务
          </Button>
        </div>
      </div>

      <Card className="video-composer rounded-2xl border-border bg-card shadow-sm">
        <CardContent className="space-y-4 p-5">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2 text-sm font-medium">
              <Film className="size-4 text-primary" />
              新建视频任务
            </div>
            <span className="text-xs text-muted-foreground">{prompt.length}/2000</span>
          </div>
          <Textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value.slice(0, 2000))}
            placeholder="描述镜头、主体、动作和氛围，例如：雨后的城市街道，霓虹倒影，缓慢推进镜头…"
            aria-label="视频提示词"
            className="min-h-32 resize-y rounded-2xl bg-muted/40 px-4 py-3 text-[15px] leading-7"
          />
          <div className="flex flex-col gap-3 border-t border-border pt-4 lg:flex-row lg:items-end lg:justify-between">
            <div className="grid flex-1 gap-3 sm:grid-cols-3">
              <label className="space-y-1.5 text-xs text-muted-foreground">
                <span className="flex items-center gap-1">
                  <SlidersHorizontal className="size-3.5" />
                  模型
                </span>
                <Input
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  placeholder="model"
                  className="h-10 rounded-xl bg-card"
                />
              </label>
              <label className="space-y-1.5 text-xs text-muted-foreground">
                <span>画布规格</span>
                <Input
                  value={size}
                  onChange={(e) => setSize(e.target.value)}
                  placeholder="1280x720"
                  className="h-10 rounded-xl bg-card"
                />
              </label>
              <label className="space-y-1.5 text-xs text-muted-foreground">
                <span className="flex items-center gap-1">
                  <Clock3 className="size-3.5" />
                  时长（秒）
                </span>
                <Input
                  type="number"
                  value={seconds}
                  aria-label="视频时长（秒）"
                  min={1}
                  max={60}
                  onChange={(e) =>
                    setSeconds(Math.min(60, Math.max(1, Number(e.target.value) || 1)))
                  }
                  className="h-10 rounded-xl bg-card"
                />
              </label>
            </div>
            <Button
              className="h-10 rounded-xl bg-primary px-5 text-primary-foreground hover:bg-primary/90 lg:min-w-28"
              onClick={() => void submit()}
              disabled={submitting || !prompt.trim()}
            >
              {submitting ? (
                <LoaderCircle className="size-4 animate-spin" />
              ) : (
                <Play className="size-4" />
              )}
              生成视频
            </Button>
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        {items.map((item) => (
          <Card
            key={item.id}
            className="video-task-card rounded-2xl border-border bg-card shadow-sm"
          >
            <CardContent className="space-y-4 p-5">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="font-mono text-xs text-muted-foreground">{item.id}</div>
                  <div className="mt-1 flex items-center gap-2">
                    <Video className="size-4 text-muted-foreground" />
                    <span className="font-medium">{item.model || "sora"}</span>
                    <Badge variant={statusVariant(item.status)}>{item.status}</Badge>
                  </div>
                </div>
                {runningStates.has(String(item.status).toLowerCase()) ? (
                  <Button
                    variant="outline"
                    className="rounded-xl text-destructive"
                    onClick={() => void cancel(item.id)}
                  >
                    <Square className="size-4" />
                    取消
                  </Button>
                ) : null}
              </div>
              <p className="whitespace-pre-wrap rounded-xl bg-muted p-3 text-sm leading-6 text-foreground/80">
                {item.prompt || "—"}
              </p>
              {runningStates.has(String(item.status).toLowerCase()) ? (
                <div className="space-y-2 rounded-xl bg-muted/60 px-3 py-2">
                  <div className="flex items-center justify-between text-xs text-muted-foreground">
                    <span>正在处理</span>
                    <span>{item.progress || "排队中"}</span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-border">
                    <div className="h-full w-2/5 animate-pulse rounded-full bg-primary" />
                  </div>
                </div>
              ) : null}
              <div className="grid grid-cols-2 gap-3 text-xs text-muted-foreground md:grid-cols-4">
                <div>
                  <div className="text-muted-foreground">进度</div>
                  <div>{item.progress || "—"}</div>
                </div>
                <div>
                  <div className="text-muted-foreground">规格</div>
                  <div>
                    {item.size || "—"} · {item.seconds || "—"}s
                  </div>
                </div>
                <div>
                  <div className="text-muted-foreground">耗时</div>
                  <div>{durationLabel(item.duration_ms)}</div>
                </div>
                <div>
                  <div className="text-muted-foreground">更新时间</div>
                  <div>{timeLabel(item.updated_at)}</div>
                </div>
              </div>
              {item.error ? (
                <div className="rounded-xl border border-destructive/25 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                  {item.error}
                </div>
              ) : null}
              {item.status === "success" ? <VideoPreview taskId={item.id} /> : null}
            </CardContent>
          </Card>
        ))}
      </div>
      {items.length === 0 ? (
        <div className="state-panel">
          <div className="rounded-2xl bg-muted p-4">
            <Video className="size-7 text-muted-foreground" />
          </div>
          <strong>还没有视频任务</strong>
          <p>从上方输入一个镜头描述，生成后可在这里按需预览、下载或释放资源。</p>
        </div>
      ) : null}
    </section>
  );
}

export default function VideoPage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin", "user"]);
  if (isCheckingAuth || !session)
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  return <VideoContent />;
}
