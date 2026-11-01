"use client";

import { CheckCircle2, RefreshCw, ShieldAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import type { ProxyPlatformState } from "@/hooks/use-proxy-platform";

const countries: Record<string, string> = {
  US: "美国",
  SG: "新加坡",
  JP: "日本",
  HK: "香港",
  TW: "台湾",
  GB: "英国",
  DE: "德国",
  FR: "法国",
  CA: "加拿大",
  BR: "巴西",
};
const healthLabels: Record<string, string> = {
  healthy: "连接正常",
  degraded: "连接不稳定",
  unhealthy: "连接异常",
  unknown: "等待检查",
  stale: "等待重新检查",
};

export function SharedProxyStatus({ state }: { state: ProxyPlatformState }) {
  const { data, loading, error, expired, ready, directReady, refresh } = state;
  const remoteReady = data?.status === "ready" && !expired && !error;
  const binding = data?.bindings;
  const capacity = data?.capacity;
  const regions = [
    ...new Set([...(data?.policy?.countries || []), ...(data?.direct_pool?.countries || [])]),
  ]
    .map((code) => countries[code] || code)
    .join("、");
  const title = ready
    ? "连接正常"
    : loading && !data
      ? "正在检查连接"
      : data?.status === "not_configured"
        ? "尚未连接代理服务"
        : expired
          ? "正在等待最新检查结果"
          : data?.status === "no_eligible_route"
            ? "当前线路暂不可用"
            : "代理连接需要检查";
  const description = ready
    ? "线路连接正常。对话、图片等功能还需要有效账号和可用额度。"
    : data?.status === "not_configured"
      ? "可在设置中连接代理服务；已有的手动代理配置仍可单独使用。"
      : data?.status === "no_eligible_route"
        ? "当前分配的线路未通过连接检查。系统会自动复查，恢复前请暂停新任务。"
        : "正在自动获取连接状态。恢复前不会把旧的连接余量显示为可用。";
  const Icon = ready ? CheckCircle2 : ShieldAlert;
  return (
    <Card className="rounded-2xl border-border bg-card shadow-sm" data-testid="shared-proxy-status">
      <CardContent className="space-y-4 p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-start gap-3">
            <div
              className={
                ready
                  ? "rounded-xl bg-success/10 p-2 text-success"
                  : "rounded-xl bg-muted p-2 text-muted-foreground"
              }
            >
              <Icon className="size-5" />
            </div>
            <div>
              <h2 className="font-semibold">代理连接</h2>
              <p role="status" className="mt-1 text-sm font-medium">
                {title}
              </p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">{description}</p>
            </div>
          </div>
          <Button
            variant="outline"
            size="sm"
            aria-label="刷新代理连接"
            disabled={loading}
            onClick={() => void refresh()}
          >
            <RefreshCw className={loading ? "size-4 animate-spin" : "size-4"} />
            <span className="hidden sm:inline">重新检查</span>
          </Button>
        </div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <div className="rounded-xl bg-muted/40 p-3">
            <p className="text-xs text-muted-foreground">还可绑定账号</p>
            <p className="mt-1 text-xl font-semibold">
              {binding?.enabled ? `${binding.remaining} 个` : binding ? "未设上限" : "—"}
            </p>
          </div>
          <div className="rounded-xl bg-muted/40 p-3">
            <p className="text-xs text-muted-foreground">可用线路</p>
            <p className="mt-1 text-xl font-semibold">
              {ready
                ? `${(remoteReady ? (capacity?.eligible_nodes ?? 0) : 0) + (directReady ? (data?.direct_pool?.assignable ?? 0) : 0)} 条`
                : "待确认"}
            </p>
          </div>
          <div className="col-span-2 rounded-xl bg-muted/40 p-3 sm:col-span-1">
            <p className="text-xs text-muted-foreground">出口地区</p>
            <p className="mt-1 text-xl font-semibold">{regions || "按代理配置"}</p>
          </div>
        </div>
        <details className="text-xs text-muted-foreground">
          <summary className="w-fit cursor-pointer py-1">连接详情与容量说明</summary>
          <div className="mt-3 space-y-3 rounded-xl border border-border p-3">
            <p>
              账号绑定：{binding?.remaining ?? "—"} / {binding?.limit ?? "—"}{" "}
              个可用；每个账号持续占用一个绑定名额，与同时连接数分别计算。
            </p>
            <p>
              平台同时连接：{remoteReady ? (capacity?.observed_remaining_connections ?? "—") : "—"}{" "}
              / {capacity?.max_concurrency ?? "—"} 个可用；增加并发不会增加账号绑定名额。
            </p>
            <p>当前项目只展示分配给它的线路，不代表共享服务的全部代理库存。</p>
            {error && <p>检查详情：{error}</p>}
            <p>
              最近检查：
              {data?.observed_at
                ? new Date(data.observed_at).toLocaleString("zh-CN")
                : "等待检查"}{" "}
              · 自动更新
            </p>
            {data?.policy && (
              <p>
                连接方式：
                {data.policy.selection_mode === "fixed" ? "保持当前线路" : "按已分配策略选择"} ·
                会话有效期 {data.policy.ttl_seconds} 秒
              </p>
            )}
            {Boolean(data?.nodes?.length) && (
              <div className="space-y-2">
                {data?.nodes.map((node) => (
                  <div
                    key={node.id}
                    className="flex flex-wrap items-center gap-2 rounded-lg bg-muted/40 p-2"
                  >
                    <Badge variant={ready && node.health === "healthy" ? "success" : "secondary"}>
                      {healthLabels[expired ? "stale" : node.health] || "等待检查"}
                    </Badge>
                    <span className="font-mono">{node.exit_ip || "出口待确认"}</span>
                    <span>
                      {node.geo_verified
                        ? `${countries[node.exit_country || ""] || node.exit_country}（出口实测）`
                        : `${countries[node.country] || node.country || "未知地区"}（待核实）`}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </details>
      </CardContent>
    </Card>
  );
}
