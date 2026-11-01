"use client";

import { useEffect, useMemo, useState, useCallback, useRef, useTransition } from "react";
import {
  Activity,
  Bot,
  CheckCircle2,
  LoaderCircle,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck,
  Wrench,
} from "lucide-react";
import { toast } from "sonner";
import { ResourceError } from "@/components/resource-error";
import { Badge } from "@/components/ui/badge";
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
  fetchAccountCapabilities,
  fetchRiskEvents,
  fetchRiskSummary,
  probeAccountCapabilities,
  updateAccountCapability,
  type AccountCapability,
  type RiskEvent,
  type RiskSummary,
} from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";
import { cn } from "@/lib/utils";

const caps: Array<keyof AccountCapability> = [
  "chat",
  "responses",
  "raw_conversation",
  "search",
  "image",
  "image_edit",
  "image_variation",
  "file",
  "audio_tts",
  "audio_stt",
  "audio_translation",
  "video",
];
const capLabels: Record<string, string> = {
  chat: "Chat",
  responses: "Resp",
  raw_conversation: "Raw",
  search: "Search",
  image: "Image",
  image_edit: "Edit",
  image_variation: "Var",
  file: "File",
  audio_tts: "TTS",
  audio_stt: "STT",
  audio_translation: "Trans",
  video: "Video",
};
function shortId(v?: string | null) {
  const s = String(v || "").trim();
  return !s ? "—" : s.length > 18 ? `${s.slice(0, 10)}…${s.slice(-6)}` : s;
}
function formatTime(v?: string | null) {
  if (!v) return "—";
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? v : d.toLocaleString("zh-CN");
}
function variant(v?: string) {
  const s = String(v || "");
  if (s.includes("token") || s.includes("quota")) return "danger" as const;
  if (s.includes("timeout") || s.includes("rate") || s.includes("proxy")) return "warning" as const;
  if (s.includes("success") || s.includes("ok")) return "success" as const;
  return "secondary" as const;
}

function RiskContent() {
  const loadRequest = useRef({ id: 0 });
  const [loadError, setLoadError] = useState("");
  const [refreshing, startRefresh] = useTransition();
  const [summary, setSummary] = useState<RiskSummary | null>(null);
  const [events, setEvents] = useState<RiskEvent[]>([]);
  const [capabilities, setCapabilities] = useState<AccountCapability[]>([]);
  const [code, setCode] = useState("all");
  const [scope, setScope] = useState("all");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [probing, setProbing] = useState(false);
  const [updating, setUpdating] = useState("");

  const load = useCallback(() => {
    const requestState = loadRequest.current;
    const requestId = ++requestState.id;
    return Promise.all([
      fetchRiskSummary(),
      fetchRiskEvents({
        code: code === "all" ? "" : code,
        scope: scope === "all" ? "" : scope,
        limit: 300,
      }),
      fetchAccountCapabilities(),
    ])
      .then((data) => {
        if (requestId !== requestState.id) return;
        setLoadError("");
        setSummary(data[0]);
        setEvents(data[1].items);
        setCapabilities(data[2].items);
      })
      .catch((error: unknown) => {
        if (requestId === requestState.id)
          setLoadError(error instanceof Error ? error.message : "加载风控数据失败");
      })
      .finally(() => {
        if (requestId === requestState.id) setLoading(false);
      });
  }, [code, scope]);

  const probe = async () => {
    setProbing(true);
    try {
      const r = await probeAccountCapabilities();
      toast.success(
        `能力矩阵同步：总计 ${r.total}，新增 ${r.created}，更新 ${r.updated}，移除 ${r.removed}`,
      );
      await load();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "同步能力矩阵失败");
    } finally {
      setProbing(false);
    }
  };

  const toggle = async (
    item: AccountCapability,
    key: keyof AccountCapability,
    checked: boolean,
  ) => {
    const id = `${item.account_key}:${String(key)}`;
    setUpdating(id);
    try {
      const r = await updateAccountCapability(item.account_key, {
        [key]: checked,
        source: "manual",
      });
      setCapabilities((list) => list.map((x) => (x.account_key === item.account_key ? r.item : x)));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "更新能力失败");
    } finally {
      setUpdating("");
    }
  };

  useEffect(() => {
    const requestState = loadRequest.current;
    void load();
    return () => {
      requestState.id++;
    };
  }, [load]);
  const filteredCaps = useMemo(() => {
    const q = query.trim().toLowerCase();
    return !q
      ? capabilities
      : capabilities.filter((x) =>
          [x.account_key, x.runtime_profile_id, x.plan, x.source, x.risk_status].some((v) =>
            String(v || "")
              .toLowerCase()
              .includes(q),
          ),
        );
  }, [capabilities, query]);

  const cards = [
    { label: "账号总数", value: summary?.accounts.total ?? 0, icon: Bot, color: "text-foreground" },
    {
      label: "Chat 可用",
      value: summary?.accounts.capabilities?.chat ?? 0,
      icon: CheckCircle2,
      color: "text-success",
    },
    {
      label: "Image 可用",
      value: summary?.accounts.capabilities?.image ?? 0,
      icon: Activity,
      color: "text-info",
    },
    {
      label: "代理健康",
      value: `${summary?.proxies.healthy ?? 0}/${summary?.proxies.total ?? 0}`,
      icon: ShieldCheck,
      color: "text-success",
    },
    {
      label: "画像待修",
      value: summary?.profiles.failed ?? 0,
      icon: Wrench,
      color: "text-warning",
    },
    {
      label: "风险事件",
      value: summary?.risk_events.total ?? 0,
      icon: ShieldAlert,
      color: "text-destructive",
    },
  ];
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
          <div className="text-xs font-semibold tracking-[0.18em] text-muted-foreground uppercase">
            Risk Control
          </div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">风控中心</h1>
          <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
            统一查看账号能力、风险事件、画像审计和代理质量；账号问题与代理问题分流记录。
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
            onClick={() => void probe()}
            disabled={probing}
          >
            {probing ? (
              <LoaderCircle className="size-4 animate-spin" />
            ) : (
              <ShieldCheck className="size-4" />
            )}
            同步能力矩阵
          </Button>
        </div>
      </div>
      <div className="grid gap-3 md:grid-cols-3 xl:grid-cols-6">
        {cards.map((item) => {
          const Icon = item.icon;
          return (
            <Card key={item.label} className="rounded-2xl border-border bg-card shadow-sm">
              <CardContent className="flex items-center justify-between p-5">
                <div>
                  <p className="text-xs text-muted-foreground">{item.label}</p>
                  <p className={cn("mt-1 text-2xl font-semibold", item.color)}>{item.value}</p>
                </div>
                <div className="rounded-xl bg-muted p-3 text-muted-foreground">
                  <Icon className="size-5" />
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
      <div className="grid gap-5 xl:grid-cols-[1fr_1.25fr]">
        <Card className="overflow-hidden rounded-2xl border-border bg-card shadow-sm">
          <CardContent className="p-0">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-4">
              <div>
                <h2 className="font-semibold">最近风险事件</h2>
                <p className="text-xs text-muted-foreground">最多 300 条</p>
              </div>
              <div className="flex gap-2">
                <Select value={code} onValueChange={setCode}>
                  <SelectTrigger
                    className="h-9 w-44 rounded-xl border-border bg-card"
                    aria-label="事件范围"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部 code</SelectItem>
                    <SelectItem value="token_invalid">token_invalid</SelectItem>
                    <SelectItem value="network_timeout">network_timeout</SelectItem>
                    <SelectItem value="proxy_or_challenge_blocked">proxy_blocked</SelectItem>
                    <SelectItem value="account_rate_limited">rate_limited</SelectItem>
                    <SelectItem value="policy_rejected">policy</SelectItem>
                  </SelectContent>
                </Select>
                <Select value={scope} onValueChange={setScope}>
                  <SelectTrigger
                    className="h-9 w-32 rounded-xl border-border bg-card"
                    aria-label="事件范围"
                  >
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">全部 scope</SelectItem>
                    <SelectItem value="account">account</SelectItem>
                    <SelectItem value="proxy">proxy</SelectItem>
                    <SelectItem value="request">request</SelectItem>
                    <SelectItem value="upstream">upstream</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </div>
            <div
              className="max-h-[580px] overflow-auto"
              tabIndex={0}
              role="region"
              aria-label="风险明细，可滚动查看"
            >
              <table className="w-full min-w-[760px] text-left text-sm">
                <thead className="sticky top-0 bg-card text-[11px] uppercase tracking-[0.16em] text-muted-foreground">
                  <tr>
                    <th className="px-4 py-3">时间</th>
                    <th className="px-4 py-3">Code</th>
                    <th className="px-4 py-3">Scope</th>
                    <th className="px-4 py-3">账号/画像</th>
                    <th className="px-4 py-3">消息</th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((x) => (
                    <tr key={x.id} className="border-t border-border text-foreground/80">
                      <td className="whitespace-nowrap px-4 py-3 text-xs">{formatTime(x.time)}</td>
                      <td className="px-4 py-3">
                        <Badge variant={variant(x.code)} className="rounded-md">
                          {x.code}
                        </Badge>
                      </td>
                      <td className="px-4 py-3 text-xs">{x.scope || "—"}</td>
                      <td className="px-4 py-3 font-mono text-xs">
                        <div>{shortId(x.account_key)}</div>
                        <div className="text-muted-foreground">{shortId(x.runtime_profile_id)}</div>
                      </td>
                      <td className="max-w-[300px] truncate px-4 py-3 text-xs" title={x.message}>
                        {x.message || "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {events.length === 0 ? (
                <div className="px-6 py-12 text-center text-sm text-muted-foreground">
                  暂无风险事件
                </div>
              ) : null}
            </div>
          </CardContent>
        </Card>
        <Card className="overflow-hidden rounded-2xl border-border bg-card shadow-sm">
          <CardContent className="p-0">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-4">
              <div>
                <h2 className="font-semibold">账号能力矩阵</h2>
                <p className="text-xs text-muted-foreground">调度层按这里筛选能力</p>
              </div>
              <div className="relative">
                <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="搜索账号/画像/状态"
                  className="h-9 w-64 rounded-xl border-border bg-card pl-9"
                />
              </div>
            </div>
            <div
              className="max-h-[580px] overflow-auto"
              tabIndex={0}
              role="region"
              aria-label="风险明细，可滚动查看"
            >
              <table className="w-full min-w-[1120px] text-left text-sm">
                <thead className="sticky top-0 bg-card text-[11px] uppercase tracking-[0.16em] text-muted-foreground">
                  <tr>
                    <th className="px-4 py-3">账号</th>
                    <th className="px-4 py-3">Plan</th>
                    <th className="px-4 py-3">风险</th>
                    {caps.map((k) => (
                      <th key={String(k)} className="px-3 py-3 text-center">
                        {capLabels[String(k)]}
                      </th>
                    ))}
                    <th className="px-4 py-3">更新</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredCaps.map((x) => (
                    <tr key={x.account_key} className="border-t border-border text-foreground/80">
                      <td className="px-4 py-3 font-mono text-xs">
                        <div>{shortId(x.account_key)}</div>
                        <div className="text-muted-foreground">{shortId(x.runtime_profile_id)}</div>
                      </td>
                      <td className="px-4 py-3">
                        <Badge variant="secondary" className="rounded-md">
                          {x.plan || "unknown"}
                        </Badge>
                      </td>
                      <td className="px-4 py-3 text-xs">
                        {x.risk_status ? (
                          <Badge variant={variant(x.risk_status)} className="rounded-md">
                            {x.risk_status}
                          </Badge>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </td>
                      {caps.map((k) => {
                        const id = `${x.account_key}:${String(k)}`;
                        return (
                          <td key={String(k)} className="px-3 py-3 text-center">
                            <Checkbox
                              checked={Boolean(x[k])}
                              disabled={updating === id}
                              onCheckedChange={(checked) => void toggle(x, k, Boolean(checked))}
                              aria-label="选择此项"
                            />
                          </td>
                        );
                      })}
                      <td className="whitespace-nowrap px-4 py-3 text-xs text-muted-foreground">
                        {formatTime(x.updated_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {filteredCaps.length === 0 ? (
                <div className="px-6 py-12 text-center text-sm text-muted-foreground">
                  没有匹配账号
                </div>
              ) : null}
            </div>
          </CardContent>
        </Card>
      </div>
    </section>
  );
}

export default function RiskPage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin"]);
  if (isCheckingAuth || !session || session.role !== "admin")
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  return <RiskContent />;
}
