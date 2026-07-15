"use client";

import { useEffect, useMemo, useState } from "react";
import { Fingerprint, LoaderCircle, RefreshCw, ShieldCheck, Wrench } from "lucide-react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  backfillRuntimeProfiles,
  fetchRuntimeProfiles,
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
  const [profiles, setProfiles] = useState<RuntimeProfile[]>([]);
  const [audit, setAudit] = useState<RuntimeProfileAudit | null>(null);
  const [isLoading, setIsLoading] = useState(true);
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

  const loadProfiles = async (silent = false) => {
    if (!silent) setIsLoading(true);
    try {
      const data = await fetchRuntimeProfiles();
      setProfiles(data.items);
      setAudit(data.audit);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "加载画像失败");
    } finally {
      if (!silent) setIsLoading(false);
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
    void loadProfiles();
  }, []);

  return (
    <section className="mx-auto flex w-full max-w-[1600px] flex-col gap-5 px-4 py-6 md:px-8">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="space-y-1">
          <div className="text-xs font-semibold tracking-[0.18em] text-stone-500 uppercase">Runtime Profile</div>
          <h1 className="text-2xl font-semibold tracking-tight text-stone-950">画像管理</h1>
          <p className="max-w-3xl text-sm leading-6 text-stone-500">
            这里统一查看账号绑定的浏览器/TLS/OpenAI 设备画像。注册、刷新、对话、图片/API 调用都会复用同一个账号画像；代理可以变，UA/TLS/device/session 必须稳定。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            className="h-10 rounded-xl border-stone-200 bg-white/80 px-4 text-stone-700 hover:bg-white"
            onClick={() => void loadProfiles()}
            disabled={isLoading || isBackfilling}
          >
            <RefreshCw className={cn("size-4", isLoading ? "animate-spin" : "")} />
            刷新
          </Button>
          <Button
            className="h-10 rounded-xl bg-stone-950 px-4 text-white hover:bg-stone-800"
            onClick={() => void handleBackfill()}
            disabled={isLoading || isBackfilling}
          >
            {isBackfilling ? <LoaderCircle className="size-4 animate-spin" /> : <Wrench className="size-4" />}
            一键修复/回填
          </Button>
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-4">
        {[
          { label: "画像总数", value: stats.total, icon: Fingerprint, color: "text-stone-900" },
          { label: "Active", value: stats.active, icon: ShieldCheck, color: "text-emerald-600" },
          { label: "Audit OK", value: stats.auditOk, icon: ShieldCheck, color: "text-blue-600" },
          { label: "待修复", value: stats.auditFailed, icon: Wrench, color: "text-amber-600" },
        ].map((item) => {
          const Icon = item.icon;
          return (
            <Card key={item.label} className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
              <CardContent className="flex items-center justify-between p-5">
                <div>
                  <p className="text-xs text-stone-500">{item.label}</p>
                  <p className={cn("mt-1 text-2xl font-semibold", item.color)}>{item.value}</p>
                </div>
                <div className="rounded-xl bg-stone-100 p-3 text-stone-500">
                  <Icon className="size-5" />
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      <Card className="overflow-hidden rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="p-0">
          {isLoading ? (
            <div className="flex items-center justify-center gap-2 px-6 py-14 text-sm text-stone-500">
              <LoaderCircle className="size-4 animate-spin" />
              正在加载画像
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1100px] text-left">
                <thead className="border-b border-stone-100 text-[11px] text-stone-400 uppercase tracking-[0.18em]">
                  <tr>
                    <th className="w-44 px-4 py-3">Profile</th>
                    <th className="w-44 px-4 py-3">Account Key</th>
                    <th className="w-28 px-4 py-3">Template</th>
                    <th className="w-28 px-4 py-3">TLS</th>
                    <th className="w-72 px-4 py-3">User-Agent</th>
                    <th className="w-44 px-4 py-3">OpenAI Device</th>
                    <th className="w-64 px-4 py-3">Proxy Policy</th>
                    <th className="w-32 px-4 py-3">Updated</th>
                  </tr>
                </thead>
                <tbody>
                  {profiles.map((profile) => (
                    <tr key={profile.id} className="border-b border-stone-100/80 text-sm text-stone-600 hover:bg-stone-50/70">
                      <td className="px-4 py-3">
                        <div className="space-y-1">
                          <Badge variant="outline" className="rounded-md border-stone-200 text-stone-700">{shortId(profile.id)}</Badge>
                          <div className="text-xs text-stone-400">{profile.status || "active"}</div>
                        </div>
                      </td>
                      <td className="px-4 py-3 font-mono text-xs text-stone-500">{shortId(profile.account_key)}</td>
                      <td className="px-4 py-3">
                        <Badge variant="secondary" className="rounded-md bg-stone-100 text-stone-700">{profile.template || "chrome_win"}</Badge>
                      </td>
                      <td className="px-4 py-3 font-mono text-xs">{profileTls(profile)}</td>
                      <td className="px-4 py-3 text-xs leading-5 text-stone-500">{profileUa(profile)}</td>
                      <td className="px-4 py-3 text-xs leading-5 text-stone-500">
                        <div>did: {shortId(profile.openai?.["oai-device-id"])}</div>
                        <div>sid: {shortId(profile.openai?.["oai-session-id"])}</div>
                      </td>
                      <td className="px-4 py-3 text-xs leading-5 text-stone-500">
                        <div>mode: {profile.proxy_policy?.mode || "account_or_runtime"}</div>
                        <div>register: {profile.proxy_policy?.register_proxy ? "已记录" : "—"}</div>
                        <div>runtime: {profile.proxy_policy?.runtime_proxy ? "已记录" : "—"}</div>
                      </td>
                      <td className="px-4 py-3 text-xs text-stone-500">{profile.updated_at ? new Date(profile.updated_at).toLocaleString("zh-CN") : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {profiles.length === 0 ? (
                <div className="flex flex-col items-center justify-center gap-3 px-6 py-14 text-center">
                  <div className="rounded-xl bg-stone-100 p-3 text-stone-500">
                    <Fingerprint className="size-5" />
                  </div>
                  <div className="space-y-1">
                    <p className="text-sm font-medium text-stone-700">还没有画像</p>
                    <p className="text-sm text-stone-500">点击“一键修复/回填”，为已有账号补齐 runtime profile。</p>
                  </div>
                </div>
              ) : null}
            </div>
          )}
        </CardContent>
      </Card>

      {audit && audit.failed > 0 ? (
        <Card className="rounded-2xl border-amber-100 bg-amber-50/80 shadow-sm">
          <CardContent className="space-y-3 p-5">
            <div className="flex items-center gap-2 text-sm font-medium text-amber-800">
              <Wrench className="size-4" />
              有 {audit.failed} 个账号画像需要修复
            </div>
            <div className="grid gap-2 md:grid-cols-2">
              {audit.items.filter((item) => !item.ok).slice(0, 8).map((item) => (
                <div key={item.runtime_profile_id || item.account_key} className="rounded-xl border border-amber-100 bg-white/70 px-3 py-2 text-xs leading-5 text-amber-800">
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
        <LoaderCircle className="size-5 animate-spin text-stone-400" />
      </div>
    );
  }

  return <RuntimeProfilesContent />;
}
