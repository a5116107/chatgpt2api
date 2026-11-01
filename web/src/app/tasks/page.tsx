"use client";

import { useEffect, useMemo, useState, useCallback, useRef, useTransition } from "react";
import {
  CheckCircle2,
  Clock3,
  LoaderCircle,
  RefreshCw,
  Search,
  Trash2,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
import { ResourceError } from "@/components/resource-error";
import { Badge } from "@/components/ui/badge";
import { useConfirmation } from "@/components/confirm-dialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  bulkDeleteUnifiedTasks,
  fetchUnifiedTasks,
  syncUnifiedTasks,
  type UnifiedTask,
} from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";
import { cn } from "@/lib/utils";

const terminal = new Set(["success", "succeeded", "failed", "error", "cancelled", "stale"]);
function formatTime(v?: string | null) {
  if (!v) return "—";
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? v : d.toLocaleString("zh-CN");
}
function formatDuration(ms?: number) {
  return !ms || ms < 0 ? "—" : `${(ms / 1000).toFixed(1)} s`;
}
function variant(s?: string) {
  const v = String(s || "").toLowerCase();
  if (["success", "succeeded"].includes(v)) return "success" as const;
  if (["failed", "error", "cancelled"].includes(v)) return "danger" as const;
  if (["running", "queued", "pending"].includes(v)) return "warning" as const;
  if (v === "stale") return "secondary" as const;
  return "outline" as const;
}

function TasksContent() {
  const { confirm, confirmationDialog } = useConfirmation();
  const loadRequest = useRef({ id: 0, controller: null as AbortController | null });
  const [loadError, setLoadError] = useState("");
  const [refreshing, startRefresh] = useTransition();
  const [items, setItems] = useState<UnifiedTask[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [type, setType] = useState("all");
  const [status, setStatus] = useState("all");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const load = useCallback(() => {
    const requestState = loadRequest.current;
    requestState.controller?.abort();
    const controller = new AbortController();
    requestState.controller = controller;
    const requestId = ++requestState.id;
    return fetchUnifiedTasks(
      {
        type: type === "all" ? "" : type,
        status: status === "all" ? "" : status,
        limit: 500,
      },
      controller.signal,
    )
      .then((data) => {
        if (requestId !== requestState.id) return;
        setLoadError("");
        setItems(data.items);
        setSelected((current) => current.filter((id) => data.items.some((item) => item.id === id)));
      })
      .catch((error: unknown) => {
        if (requestId === requestState.id && !controller.signal.aborted)
          setLoadError(error instanceof Error ? error.message : "加载任务失败");
      })
      .finally(() => {
        if (requestId === requestState.id) setLoading(false);
      });
  }, [type, status]);

  const sync = async () => {
    setSyncing(true);
    try {
      const r = await syncUnifiedTasks();
      toast.success(`任务中心同步：图片 ${r.upserted}，总计 ${r.total}`);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "同步任务失败");
    } finally {
      setSyncing(false);
    }
  };
  const remove = async (ids: string[], terminalOnly = true) => {
    if (
      !(await confirm({
        title: "清理这些任务记录？",
        description: "删除的任务记录将不会出现在列表中，请确认已保留需要的结果。",
      }))
    )
      return;
    setDeleting(true);
    try {
      const r = await bulkDeleteUnifiedTasks(ids, terminalOnly);
      toast.success(`已清理 ${r.removed} 个任务，保留 ${r.kept} 个`);
      setSelected([]);
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "清理任务失败");
    } finally {
      setDeleting(false);
    }
  };
  useEffect(() => {
    const requestState = loadRequest.current;
    void load();
    return () => {
      requestState.id++;
      requestState.controller?.abort();
    };
  }, [load]);
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return !q
      ? items
      : items.filter((x) =>
          [x.id, x.type, x.status, x.progress, x.error, x.source].some((v) =>
            String(v || "")
              .toLowerCase()
              .includes(q),
          ),
        );
  }, [items, query]);
  const selectedSet = useMemo(() => new Set(selected), [selected]);
  const stats = useMemo(
    () => ({
      total: items.length,
      running: items.filter((x) =>
        ["running", "queued", "pending"].includes(String(x.status).toLowerCase()),
      ).length,
      success: items.filter((x) =>
        ["success", "succeeded"].includes(String(x.status).toLowerCase()),
      ).length,
      failed: items.filter((x) => ["failed", "error"].includes(String(x.status).toLowerCase()))
        .length,
      stale: items.filter((x) => String(x.status).toLowerCase() === "stale").length,
    }),
    [items],
  );
  const toggle = (ids: string[], checked: boolean) =>
    setSelected((cur) =>
      checked ? Array.from(new Set([...cur, ...ids])) : cur.filter((id) => !ids.includes(id)),
    );
  if (loading)
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  if (loadError) return <ResourceError message={loadError} onRetry={() => load()} />;
  return (
    <section className="flex w-full min-w-0 flex-col gap-5">
      {confirmationDialog}
      <div className="workbench-toolbar">
        <div className="space-y-1">
          <div className="text-xs font-semibold tracking-[0.18em] text-muted-foreground uppercase">
            Task Center
          </div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">统一任务中心</h1>
          <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
            注册、图片、对话、视频统一任务模型；支持手动删除所选与批量清理终态任务。
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="outline"
            className="h-10 rounded-xl border-border bg-card px-4"
            onClick={() =>
              startRefresh(async () => {
                await load();
              })
            }
            disabled={refreshing}
          >
            <RefreshCw className={cn("size-4", refreshing ? "animate-spin" : "")} />
            刷新
          </Button>
          <Button
            className="h-10 rounded-xl bg-primary px-4 text-primary-foreground hover:bg-primary/90"
            onClick={() => void sync()}
            disabled={syncing}
          >
            {syncing ? (
              <LoaderCircle className="size-4 animate-spin" />
            ) : (
              <RefreshCw className="size-4" />
            )}
            同步任务源
          </Button>
        </div>
      </div>
      <div className="grid gap-3 md:grid-cols-5">
        {[
          { label: "总任务", value: stats.total, icon: Search, color: "text-foreground" },
          { label: "运行中", value: stats.running, icon: Clock3, color: "text-warning" },
          { label: "成功", value: stats.success, icon: CheckCircle2, color: "text-success" },
          { label: "失败", value: stats.failed, icon: XCircle, color: "text-destructive" },
          { label: "Stale", value: stats.stale, icon: Trash2, color: "text-muted-foreground" },
        ].map((x) => {
          const Icon = x.icon;
          return (
            <Card key={x.label} className="rounded-2xl border-border bg-card shadow-sm">
              <CardContent className="flex items-center justify-between p-5">
                <div>
                  <p className="text-xs text-muted-foreground">{x.label}</p>
                  <p className={cn("mt-1 text-2xl font-semibold", x.color)}>{x.value}</p>
                </div>
                <div className="rounded-xl bg-muted p-3 text-muted-foreground">
                  <Icon className="size-5" />
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
      <Card className="overflow-hidden rounded-2xl border-border bg-card shadow-sm">
        <CardContent className="p-0">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-4">
            <div className="flex flex-wrap items-center gap-2">
              <Select value={type} onValueChange={setType}>
                <SelectTrigger
                  className="h-9 w-36 rounded-xl border-border bg-card"
                  aria-label="筛选类型"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部类型</SelectItem>
                  <SelectItem value="register">register</SelectItem>
                  <SelectItem value="image">image</SelectItem>
                  <SelectItem value="chat">chat</SelectItem>
                  <SelectItem value="video">video</SelectItem>
                </SelectContent>
              </Select>
              <Select value={status} onValueChange={setStatus}>
                <SelectTrigger
                  className="h-9 w-36 rounded-xl border-border bg-card"
                  aria-label="筛选状态"
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">全部状态</SelectItem>
                  <SelectItem value="running">running</SelectItem>
                  <SelectItem value="queued">queued</SelectItem>
                  <SelectItem value="success">success</SelectItem>
                  <SelectItem value="succeeded">succeeded</SelectItem>
                  <SelectItem value="failed">failed</SelectItem>
                  <SelectItem value="stale">stale</SelectItem>
                </SelectContent>
              </Select>
              <Input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="搜索任务 ID / 错误 / 来源"
                className="h-9 w-72 rounded-xl border-border bg-card"
              />
            </div>
            <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              <label className="flex items-center gap-2">
                <Checkbox
                  checked={filtered.length > 0 && filtered.every((x) => selectedSet.has(x.id))}
                  onCheckedChange={(checked) =>
                    toggle(
                      filtered.map((x) => x.id),
                      Boolean(checked),
                    )
                  }
                  aria-label="全选当前结果"
                />
                全选当前结果
              </label>
              <span>已选 {selected.length}</span>
              <Button
                variant="outline"
                className="h-8 rounded-lg border-border bg-card px-3 text-foreground/80"
                onClick={() => void remove([], true)}
                disabled={deleting}
              >
                清理全部终态
              </Button>
              <Button
                variant="outline"
                className="h-8 rounded-lg border-destructive/25 bg-card px-3 text-destructive hover:bg-destructive/10"
                onClick={() => void remove(selected, false)}
                disabled={selected.length === 0 || deleting}
              >
                删除所选
              </Button>
            </div>
          </div>
          <div
            className="overflow-x-auto"
            tabIndex={0}
            role="region"
            aria-label="任务明细，可左右滚动"
          >
            <table className="w-full min-w-[1120px] text-left text-sm">
              <thead className="text-[11px] uppercase tracking-[0.16em] text-muted-foreground">
                <tr>
                  <th className="w-12 px-4 py-3"></th>
                  <th className="px-4 py-3">任务</th>
                  <th className="px-4 py-3">状态</th>
                  <th className="px-4 py-3">进度</th>
                  <th className="px-4 py-3">成功/失败/总数</th>
                  <th className="px-4 py-3">耗时</th>
                  <th className="px-4 py-3">时间</th>
                  <th className="px-4 py-3">错误</th>
                  <th className="px-4 py-3">操作</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((x) => (
                  <tr key={x.id} className="border-t border-border text-foreground/80">
                    <td className="px-4 py-3">
                      <Checkbox
                        checked={selectedSet.has(x.id)}
                        onCheckedChange={(checked) => toggle([x.id], Boolean(checked))}
                        aria-label="选择此项"
                      />
                    </td>
                    <td className="px-4 py-3">
                      <div className="font-mono text-xs">{x.id}</div>
                      <div className="text-xs text-muted-foreground">
                        {x.type} · {x.source || "—"}
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <Badge variant={variant(x.status)} className="rounded-md">
                        {x.status || "unknown"}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 text-xs">{x.progress || "—"}</td>
                    <td className="px-4 py-3 text-xs">
                      <span className="text-success">{x.success ?? "—"}</span> /{" "}
                      <span className="text-destructive">{x.fail ?? "—"}</span> / {x.total ?? "—"}
                    </td>
                    <td className="px-4 py-3 text-xs">{formatDuration(x.duration_ms)}</td>
                    <td className="px-4 py-3 text-xs">
                      <div>{formatTime(x.created_at)}</div>
                      <div className="text-muted-foreground">{formatTime(x.updated_at)}</div>
                    </td>
                    <td className="max-w-[280px] truncate px-4 py-3 text-xs" title={x.error || ""}>
                      {x.error || "—"}
                    </td>
                    <td className="px-4 py-3">
                      <Button
                        variant="ghost"
                        className="h-8 rounded-lg px-3 text-destructive hover:bg-destructive/10"
                        onClick={() =>
                          void remove([x.id], !terminal.has(String(x.status).toLowerCase()))
                        }
                        disabled={deleting}
                      >
                        删除
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {filtered.length === 0 ? (
              <div className="px-6 py-14 text-center text-sm text-muted-foreground">
                没有匹配任务
              </div>
            ) : null}
          </div>
        </CardContent>
      </Card>
    </section>
  );
}

export default function TasksPage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin"]);
  if (isCheckingAuth || !session || session.role !== "admin")
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  return <TasksContent />;
}
