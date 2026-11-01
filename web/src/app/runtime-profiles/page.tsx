"use client";

import { useEffect, useMemo, useState, useCallback, useRef, useTransition } from "react";
import { Fingerprint, LoaderCircle, RefreshCw, ShieldCheck, Wrench } from "lucide-react";
import { toast } from "sonner";

import { ResourceError } from "@/components/resource-error";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  backfillRuntimeProfiles,
  fetchRuntimeProfiles,
  setProxyFailover,
  type RuntimeProfile,
  type RuntimeProfileAudit,
} from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";
import { cn } from "@/lib/utils";

function profileUa(profile: RuntimeProfile) {
  return profile.headers?.user_agent_preview || profile.headers?.["user-agent"] || "—";
}

function profileTls(profile: RuntimeProfile) {
  return profile.tls?.impersonate || "chrome110";
}

function shortId(value?: string | null) {
  const text = String(value || "").trim();
  if (!text) return "—";
  if (text.length <= 16) return text;
  return `${text.slice(0, 10)}…${text.slice(-4)}`;
}

function RuntimeProfilesContent() {
  const loadRequest = useRef({ id: 0 });
  const [loadError, setLoadError] = useState("");
  const [refreshing, startRefresh] = useTransition();
  const [profiles, setProfiles] = useState<RuntimeProfile[]>([]);
  const [audit, setAudit] = useState<RuntimeProfileAudit | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [savingPolicy, setSavingPolicy] = useState("");
  const [isBackfilling, setIsBackfilling] = useState(false);

  const stats = useMemo(() => {
    const active = profiles.filter((item) => (item.status || "active") === "active").length;
    return {
      total: profiles.length,
      active,
      auditOk: audit?.ok ?? 0,
      auditFailed: audit?.failed ?? 0,
    };
  }, [audit, profiles]);

  const loadProfiles = useCallback(() => {
    const requestState = loadRequest.current;
    const requestId = ++requestState.id;
    return fetchRuntimeProfiles()
      .then((data) => {
        if (requestId !== requestState.id) return;
        setLoadError("");
        setProfiles(data.items);
        setAudit(data.audit);
      })
      .catch((error: unknown) => {
        if (requestId === requestState.id)
          setLoadError(error instanceof Error ? error.message : "加载画像失败");
      })
      .finally(() => {
        if (requestId === requestState.id) setIsLoading(false);
      });
  }, []);

  const changeFailover = async (profile: RuntimeProfile, enabled: boolean) => {
    setSavingPolicy(profile.id);
    try {
      await setProxyFailover(profile.id, enabled);
      await loadProfiles();
      toast.success(enabled ? "已开启连接故障自动换线" : "已关闭自动换线");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "保存失败");
    } finally {
      setSavingPolicy("");
    }
  };

  const handleBackfill = async () => {
    setIsBackfilling(true);
    try {
      const data = await backfillRuntimeProfiles();
      setProfiles(data.profiles);
      setAudit(data.audit);
      toast.success(`画像修复完成：修复 ${data.repaired} 个，重新绑定 ${data.rebounded} 个`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "修复画像失败");
    } finally {
      setIsBackfilling(false);
    }
  };

  useEffect(() => {
    const requestState = loadRequest.current;
    void loadProfiles();
    return () => {
      requestState.id++;
    };
  }, [loadProfiles]);

  if (loadError) return <ResourceError message={loadError} onRetry={() => loadProfiles()} />;
  return (
    <section className="flex w-full min-w-0 flex-col gap-5">
      <div className="workbench-toolbar">
        <div className="space-y-1">
          <div className="text-xs font-semibold tracking-[0.18em] text-muted-foreground uppercase">
            账号连接
          </div>
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">账号连接</h1>
          <p className="max-w-3xl text-sm leading-6 text-muted-foreground">
            管理每个账号的连接与设备信息。开启自动换线后，连接故障会切换到同地区的可用备用线路，设备身份保持稳定；没有合格备选时保留原绑定。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            className="h-10 rounded-xl border-border bg-card px-4 text-foreground/80 hover:bg-card"
            onClick={() =>
              startRefresh(async () => {
                await loadProfiles();
              })
            }
            disabled={isLoading || isBackfilling || refreshing}
          >
            <RefreshCw className={cn("size-4", isLoading ? "animate-spin" : "")} />
            刷新
          </Button>
          <Button
            className="h-10 rounded-xl bg-primary px-4 text-primary-foreground hover:bg-primary/90"
            onClick={() => void handleBackfill()}
            disabled={isLoading || isBackfilling || refreshing}
          >
            {isBackfilling ? (
              <LoaderCircle className="size-4 animate-spin" />
            ) : (
              <Wrench className="size-4" />
            )}
            一键修复/回填
          </Button>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-4">
        {[
          { label: "画像总数", value: stats.total, icon: Fingerprint, color: "text-foreground" },
          { label: "使用中", value: stats.active, icon: ShieldCheck, color: "text-success" },
          { label: "检查通过", value: stats.auditOk, icon: ShieldCheck, color: "text-info" },
          { label: "待修复", value: stats.auditFailed, icon: Wrench, color: "text-warning" },
        ].map((item) => {
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

      <Card className="overflow-hidden rounded-2xl border-border bg-card shadow-sm">
        <CardContent className="p-0">
          {isLoading ? (
            <div className="flex items-center justify-center gap-2 px-6 py-14 text-sm text-muted-foreground">
              <LoaderCircle className="size-4 animate-spin" />
              正在加载画像
            </div>
          ) : (
            <div
              className="overflow-x-auto"
              tabIndex={0}
              role="region"
              aria-label="画像明细，可左右滚动"
            >
              <table className="w-full min-w-[1100px] text-left">
                <thead className="border-b border-border text-[11px] text-muted-foreground uppercase tracking-[0.18em]">
                  <tr>
                    <th className="w-44 px-4 py-3">账号连接</th>
                    <th className="w-44 px-4 py-3">账号标识</th>
                    <th className="w-28 px-4 py-3">设备模板</th>
                    <th className="w-28 px-4 py-3">TLS</th>
                    <th className="w-72 px-4 py-3">User-Agent</th>
                    <th className="w-44 px-4 py-3">设备标识</th>
                    <th className="w-64 px-4 py-3">线路与自动恢复</th>
                    <th className="w-32 px-4 py-3">更新时间</th>
                  </tr>
                </thead>
                <tbody>
                  {profiles.map((profile) => (
                    <tr
                      key={profile.id}
                      className="border-b border-border/80 text-sm text-foreground/80 hover:bg-muted/70"
                    >
                      <td className="px-4 py-3">
                        <div className="space-y-1">
                          <Badge
                            variant="outline"
                            className="rounded-md border-border text-foreground/80"
                          >
                            {shortId(profile.id)}
                          </Badge>
                          <div className="text-xs text-muted-foreground">
                            {profile.status || "active"}
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
                        {shortId(profile.account_key)}
                      </td>
                      <td className="px-4 py-3">
                        <Badge
                          variant="secondary"
                          className="rounded-md bg-muted text-foreground/80"
                        >
                          {profile.template || "chrome_win"}
                        </Badge>
                      </td>
                      <td className="px-4 py-3 font-mono text-xs">{profileTls(profile)}</td>
                      <td className="px-4 py-3 text-xs leading-5 text-muted-foreground">
                        {profileUa(profile)}
                      </td>
                      <td className="px-4 py-3 text-xs leading-5 text-muted-foreground">
                        <div>did: {shortId(profile.openai?.["oai-device-id"])}</div>
                        <div>sid: {shortId(profile.openai?.["oai-session-id"])}</div>
                      </td>
                      <td className="px-4 py-3 text-xs leading-5 text-muted-foreground">
                        <div>{profile.proxy_policy?.runtime_proxy || "当前为直连"}</div>
                        <div>地区：{profile.proxy_policy?.country || "待确认，不跨地区切换"}</div>
                        <label className="mt-2 flex items-center gap-2 text-foreground">
                          <Checkbox
                            aria-label={`自动换线 ${profile.id}`}
                            checked={profile.proxy_policy?.failover_enabled !== false}
                            disabled={Boolean(savingPolicy)}
                            onCheckedChange={(checked) =>
                              void changeFailover(profile, checked === true)
                            }
                          />
                          {savingPolicy === profile.id ? "正在保存…" : "连接故障自动换线"}
                        </label>
                        <div className="mt-1">
                          已切换 {profile.proxy_policy?.failover_count || 0} 次
                        </div>
                        {profile.proxy_policy?.last_failover_at && (
                          <div>
                            最近切换：
                            {new Date(profile.proxy_policy.last_failover_at).toLocaleString(
                              "zh-CN",
                            )}
                          </div>
                        )}
                        <details className="mt-1">
                          <summary className="cursor-pointer">恢复规则</summary>
                          <p>
                            仅连接故障或代理认证失效触发，5
                            分钟内至多切换一次。额度及目标访问限制不会换线；提交中的创作任务不会重复执行。
                          </p>
                        </details>
                      </td>
                      <td className="px-4 py-3 text-xs text-muted-foreground">
                        {profile.updated_at
                          ? new Date(profile.updated_at).toLocaleString("zh-CN")
                          : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {profiles.length === 0 ? (
                <div className="flex flex-col items-center justify-center gap-3 px-6 py-14 text-center">
                  <div className="rounded-xl bg-muted p-3 text-muted-foreground">
                    <Fingerprint className="size-5" />
                  </div>
                  <div className="space-y-1">
                    <p className="text-sm font-medium text-foreground/80">还没有画像</p>
                    <p className="text-sm text-muted-foreground">
                      点击“一键修复/回填”，为已有账号补齐 runtime profile。
                    </p>
                  </div>
                </div>
              ) : null}
            </div>
          )}
        </CardContent>
      </Card>

      {audit && audit.failed > 0 ? (
        <Card className="rounded-2xl border-warning/25 bg-warning/80 shadow-sm">
          <CardContent className="space-y-3 p-5">
            <div className="flex items-center gap-2 text-sm font-medium text-warning">
              <Wrench className="size-4" />有 {audit.failed} 个账号画像需要修复
            </div>
            <div className="grid gap-2 md:grid-cols-2">
              {audit.items
                .filter((item) => !item.ok)
                .slice(0, 8)
                .map((item) => (
                  <div
                    key={item.runtime_profile_id || item.account_key}
                    className="rounded-xl border border-warning/25 bg-card/70 px-3 py-2 text-xs leading-5 text-warning"
                  >
                    <div className="font-mono">{shortId(item.account_key)}</div>
                    {item.missing.length > 0 ? <div>缺失：{item.missing.join(", ")}</div> : null}
                    {item.warnings.length > 0 ? <div>警告：{item.warnings.join("；")}</div> : null}
                  </div>
                ))}
            </div>
          </CardContent>
        </Card>
      ) : null}
    </section>
  );
}

export default function RuntimeProfilesPage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin"]);

  if (isCheckingAuth || !session || session.role !== "admin") {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return <RuntimeProfilesContent />;
}
