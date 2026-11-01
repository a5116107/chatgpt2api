"use client";

import { useId } from "react";

import {
  AlertTriangle,
  LoaderCircle,
  Plus,
  Play,
  RotateCcw,
  Save,
  Square,
  Trash2,
  UserPlus,
} from "lucide-react";

import {
  Badge,
  Button,
  Checkbox,
  Input,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Textarea,
} from "@/components/ui";

import { useSettingsStore, registerHasChanges } from "../../settings/store";
import { useConfirmation } from "@/components/confirm-dialog";
import type { ProxyPlatformState } from "@/hooks/use-proxy-platform";
import { mailProviderDefaults, mailProviderOptions } from "./mail-provider-catalog";
import { OutlookExternalProviderFields } from "./outlook-external-provider-fields";
import { RandomDomainProviderFields } from "./random-domain-provider-fields";

function RegisterProxyFields({ disabled }: { disabled: boolean }) {
  const fieldId = useId();
  const config = useSettingsStore((state) => state.registerConfig);
  const setProxy = useSettingsStore((state) => state.setRegisterProxy);
  const setSessionTtl = useSettingsStore((state) => state.setRegisterProxySessionTtl);
  const setTtlUnit = useSettingsStore((state) => state.setRegisterProxyTtlUnit);
  const setRegion = useSettingsStore((state) => state.setRegisterProxyRegion);

  if (!config) return null;
  return (
    <>
      <div className="space-y-2">
        <label className="text-sm text-foreground/80" htmlFor={fieldId + "-1"}>
          注册代理
        </label>
        <Input
          value={config.proxy}
          onChange={(event) => setProxy(event.target.value)}
          placeholder="http://127.0.0.1:7890"
          className="h-10 rounded-xl border-border bg-card"
          disabled={disabled}
          id={fieldId + "-1"}
        />
      </div>
      <div className="space-y-2">
        <label className="text-sm text-foreground/80" htmlFor={fieldId + "-2"}>
          代理会话租期（秒）
        </label>
        <Input
          value={String(config.proxy_session_ttl_seconds || 900)}
          onChange={(event) => setSessionTtl(event.target.value)}
          min={60}
          max={3600}
          type="number"
          className="h-10 rounded-xl border-border bg-card"
          disabled={disabled}
          id={fieldId + "-2"}
        />
      </div>
      <div className="space-y-2">
        <label className="text-sm text-foreground/80" htmlFor={fieldId + "-3"}>
          代理 TTL 单位
        </label>
        <Select
          value={config.proxy_ttl_unit || "seconds"}
          onValueChange={(value) => setTtlUnit(value as "seconds" | "minutes")}
          disabled={disabled}
        >
          <SelectTrigger className="h-10 rounded-xl border-border bg-card" id={fieldId + "-3"}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="seconds">秒（旧动态代理）</SelectItem>
            <SelectItem value="minutes">分钟（socks.acom.work）</SelectItem>
          </SelectContent>
        </Select>
      </div>
      <div className="space-y-2">
        <label className="text-sm text-foreground/80" htmlFor={fieldId + "-4"}>
          代理区域
        </label>
        <Input
          value={config.proxy_region || ""}
          onChange={(event) => setRegion(event.target.value)}
          placeholder="SG；留空沿用代理配置"
          className="h-10 rounded-xl border-border bg-card"
          disabled={disabled}
          id={fieldId + "-4"}
        />
      </div>
    </>
  );
}

export function RegisterCard({ platform }: { platform: ProxyPlatformState }) {
  const { confirm, confirmationDialog } = useConfirmation();
  const savedConfig = useSettingsStore((state) => state.savedRegisterConfig);
  const fieldId = useId();
  const config = useSettingsStore((state) => state.registerConfig);
  const isLoading = useSettingsStore((state) => state.isLoadingRegister);
  const isSaving = useSettingsStore((state) => state.isSavingRegister);
  const setTotal = useSettingsStore((state) => state.setRegisterTotal);
  const setThreads = useSettingsStore((state) => state.setRegisterThreads);
  const setMaxAttempts = useSettingsStore((state) => state.setRegisterMaxAttempts);
  const setMode = useSettingsStore((state) => state.setRegisterMode);
  const setTargetQuota = useSettingsStore((state) => state.setRegisterTargetQuota);
  const setTargetAvailable = useSettingsStore((state) => state.setRegisterTargetAvailable);
  const setCheckInterval = useSettingsStore((state) => state.setRegisterCheckInterval);
  const setMailField = useSettingsStore((state) => state.setRegisterMailField);
  const addProvider = useSettingsStore((state) => state.addRegisterProvider);
  const updateProvider = useSettingsStore((state) => state.updateRegisterProvider);
  const deleteProvider = useSettingsStore((state) => state.deleteRegisterProvider);
  const save = useSettingsStore((state) => state.saveRegister);
  const toggle = useSettingsStore((state) => state.toggleRegister);
  const reset = useSettingsStore((state) => state.resetRegister);
  const resetOutlookPool = useSettingsStore((state) => state.resetOutlookPool);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center rounded-xl border border-border bg-card p-10">
        <LoaderCircle className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!config) return null;

  const stats = config.stats || {
    success: 0,
    fail: 0,
    done: 0,
    running: 0,
    threads: config.threads,
  };
  const providers = config.mail.providers || [];
  const logs = config.logs || [];
  const currentAccounts = Number(stats.current_available || 0);
  const required =
    config.mode === "available"
      ? Math.max(0, config.target_available - currentAccounts)
      : config.mode === "quota" && Number(stats.current_quota || 0) >= config.target_quota
        ? 0
        : config.mode === "quota"
          ? config.threads
          : config.total;
  const bindings = platform.data?.bindings;
  const shortfall =
    bindings?.enabled && bindings.strict ? Math.max(0, required - bindings.remaining) : 0;
  const mailReady = providers.some((provider) => provider.enable !== false);
  const checking = platform.loading && !platform.data;
  const networkBlocked =
    !platform.ready &&
    (platform.data?.status !== "not_configured" || Boolean(platform.data?.direct_pool?.enabled));
  const targetReached = required === 0;
  const dirty = registerHasChanges(config, savedConfig);
  const blocked = checking || networkBlocked || shortfall > 0 || !mailReady || targetReached;
  const heading = config.enabled
    ? "任务正在执行"
    : checking
      ? "正在检查准备情况"
      : shortfall > 0
        ? "目标超出当前容量"
        : networkBlocked
          ? "请先恢复代理连接"
          : !mailReady
            ? "请先配置邮箱来源"
            : targetReached
              ? "目标已满足"
              : "准备就绪";
  const message =
    shortfall > 0
      ? `本次需要新增 ${required} 个账号，还可绑定 ${bindings?.remaining} 个，差 ${shortfall} 个名额。`
      : networkBlocked
        ? "当前线路暂不可用或检查结果已过期。系统会自动复查，恢复后再开始。"
        : !mailReady
          ? "展开左侧邮箱来源，添加并启用一个可用来源。"
          : targetReached
            ? "当前账号或额度已经达到目标，无需重复启动。"
            : config.mode === "quota"
              ? "将按目标额度执行，并遵守现有账号绑定容量。"
              : `本次计划新增 ${required} 个账号。连接、邮箱和账号名额将在启动时再次检查。`;
  const adjustTarget = () => {
    const remaining = bindings?.remaining || 0;
    if (!remaining) return;
    if (config.mode === "available") setTargetAvailable(String(currentAccounts + remaining));
    else if (config.mode === "total") setTotal(String(remaining));
    setThreads(String(Math.min(config.threads, remaining)));
  };
  const handleToggleRegister = async () => {
    if (!config.enabled) {
      if (blocked) return;
      if (
        !(await confirm({
          title: "开始注册任务？",
          confirmLabel: "保存并开始",
          destructive: false,
          description: `将保存当前配置并开始执行，同时最多执行 ${config.threads} 个任务。`,
        }))
      )
        return;
    }
    await toggle();
  };
  const updateProviderType = (index: number, type: string) => {
    updateProvider(index, {
      type,
      enable: true,
      ...mailProviderDefaults(type),
    });
  };

  return (
    <div className="grid min-h-[560px] items-start gap-0 overflow-hidden rounded-2xl border border-border bg-card/70 xl:grid-cols-[1fr_1.1fr]">
      {confirmationDialog}
      <section className="space-y-4 overflow-y-auto border-b border-border p-4 xl:border-r xl:border-b-0">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex size-9 items-center justify-center rounded-md bg-muted">
              <UserPlus className="size-5 text-foreground/80" />
            </div>
            <div>
              <h2 className="text-lg font-semibold tracking-tight">注册配置</h2>
            </div>
          </div>
          <Button
            className="h-9 rounded-xl bg-primary px-4 text-primary-foreground hover:bg-primary/90"
            onClick={() => void save()}
            disabled={isSaving || config.enabled}
          >
            {isSaving ? (
              <LoaderCircle className="size-4 animate-spin" />
            ) : (
              <Save className="size-4" />
            )}
            保存配置
          </Button>
        </div>

        <p className="text-sm text-muted-foreground">
          选择目标和同时执行数，其他参数可在高级设置中调整。
        </p>
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2 sm:col-span-2">
            <label className="text-sm" htmlFor={fieldId + "-5"}>
              任务目标
            </label>
            <Select
              value={config.mode || "total"}
              onValueChange={(value) => setMode(value as "total" | "quota" | "available")}
              disabled={config.enabled}
            >
              <SelectTrigger id={fieldId + "-5"}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="total">新增指定数量的账号</SelectItem>
                <SelectItem value="available">补充到目标账号数</SelectItem>
                <SelectItem value="quota">补充到目标额度</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <label className="text-sm" htmlFor={fieldId + "-target"}>
              {config.mode === "available"
                ? "目标账号数"
                : config.mode === "quota"
                  ? "目标额度"
                  : "新增账号数"}
            </label>
            <Input
              id={fieldId + "-target"}
              type="number"
              min={1}
              value={String(
                config.mode === "available"
                  ? config.target_available
                  : config.mode === "quota"
                    ? config.target_quota
                    : config.total,
              )}
              onChange={(event) =>
                (config.mode === "available"
                  ? setTargetAvailable
                  : config.mode === "quota"
                    ? setTargetQuota
                    : setTotal)(event.target.value)
              }
              disabled={config.enabled}
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm" htmlFor={fieldId + "-7"}>
              同时执行数
            </label>
            <Input
              id={fieldId + "-7"}
              type="number"
              min={1}
              value={String(config.threads)}
              onChange={(event) => setThreads(event.target.value)}
              disabled={config.enabled}
            />
            <p className="text-xs text-muted-foreground">
              只影响同时执行的任务数，不改变账号容量。
            </p>
          </div>
        </div>
        <details className="rounded-xl border border-border p-3">
          <summary className="cursor-pointer text-sm font-medium">高级设置</summary>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <RegisterProxyFields disabled={config.enabled} />
            <div className="space-y-2">
              <label className="text-sm" htmlFor={fieldId + "-8"}>
                单账号最多尝试
              </label>
              <Input
                id={fieldId + "-8"}
                type="number"
                min={1}
                max={20}
                value={String(config.max_attempts || 6)}
                onChange={(event) => setMaxAttempts(event.target.value)}
                disabled={config.enabled}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm" htmlFor={fieldId + "-11"}>
                目标检查间隔（秒）
              </label>
              <Input
                id={fieldId + "-11"}
                type="number"
                min={1}
                value={String(config.check_interval)}
                onChange={(event) => setCheckInterval(event.target.value)}
                disabled={config.enabled || config.mode === "total"}
              />
            </div>
          </div>
        </details>

        <details
          id="registration-mail"
          className="space-y-3 border-t border-border pt-3"
          open={providers.length === 0 ? true : undefined}
        >
          <summary className="cursor-pointer text-sm font-medium">
            邮箱来源 · 已启用 {providers.filter((provider) => provider.enable !== false).length} 个
          </summary>
          <div className="flex items-center justify-between gap-3">
            <div>
              <h3 className="text-sm font-semibold text-foreground">邮箱配置</h3>
              <p className="mt-1 text-xs text-muted-foreground">在这里管理收取验证码的邮箱服务。</p>
            </div>
            <Button
              type="button"
              variant="outline"
              className="h-9 rounded-xl border-border bg-card px-3 text-foreground/80"
              onClick={addProvider}
              disabled={config.enabled}
            >
              <Plus className="size-4" />
              添加
            </Button>
          </div>

          <div className="grid gap-4 md:grid-cols-3">
            <div className="space-y-2">
              <label className="text-sm text-foreground/80" htmlFor={fieldId + "-12"}>
                请求超时
              </label>
              <Input
                value={String(config.mail.request_timeout || "")}
                onChange={(event) => setMailField("request_timeout", event.target.value)}
                className="h-10 rounded-xl border-border bg-card"
                disabled={config.enabled}
                id={fieldId + "-12"}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm text-foreground/80" htmlFor={fieldId + "-13"}>
                等待验证码超时
              </label>
              <Input
                value={String(config.mail.wait_timeout || "")}
                onChange={(event) => setMailField("wait_timeout", event.target.value)}
                className="h-10 rounded-xl border-border bg-card"
                disabled={config.enabled}
                id={fieldId + "-13"}
              />
            </div>
            <div className="space-y-2">
              <label className="text-sm text-foreground/80" htmlFor={fieldId + "-14"}>
                轮询间隔
              </label>
              <Input
                value={String(config.mail.wait_interval || "")}
                onChange={(event) => setMailField("wait_interval", event.target.value)}
                className="h-10 rounded-xl border-border bg-card"
                disabled={config.enabled}
                id={fieldId + "-14"}
              />
            </div>
          </div>

          <div className="space-y-3">
            {providers.map((provider, index) => {
              const type = String(provider.type || "mailfree");
              const domains = Array.isArray(provider.domain)
                ? provider.domain.map(String).join("\n")
                : "";
              const domainIndices = Array.isArray(provider.domain_indices)
                ? provider.domain_indices.map(String).join(",")
                : String(provider.domain_index || "");
              const subdomains = Array.isArray(provider.subdomain)
                ? provider.subdomain.map(String).join("\n")
                : "";
              return (
                <div
                  key={index}
                  className="space-y-3 border-t border-border pt-3 first:border-t-0 first:pt-0"
                >
                  <div className="flex items-center justify-between gap-3">
                    <label className="flex items-center gap-3 text-sm text-foreground/80">
                      <Checkbox
                        checked={Boolean(provider.enable)}
                        onCheckedChange={(checked) =>
                          updateProvider(index, { enable: Boolean(checked) })
                        }
                        disabled={config.enabled}
                        aria-label="启用"
                      />
                      启用
                    </label>
                    <button
                      type="button"
                      className="rounded-lg p-2 text-muted-foreground transition hover:bg-destructive/10 hover:text-destructive disabled:opacity-50"
                      onClick={() => deleteProvider(index)}
                      disabled={config.enabled || providers.length <= 1}
                      title="删除 provider"
                      aria-label="删除 provider"
                    >
                      <Trash2 className="size-4" />
                    </button>
                  </div>

                  <div className="grid gap-4 md:grid-cols-2">
                    <div className="space-y-2">
                      <label
                        className="text-sm text-foreground/80"
                        htmlFor={fieldId + "-15" + "-" + index}
                      >
                        类型
                      </label>
                      <Select
                        value={type}
                        onValueChange={(value) => updateProviderType(index, value)}
                        disabled={config.enabled}
                      >
                        <SelectTrigger
                          className="h-10 rounded-xl border-border bg-card"
                          id={fieldId + "-15" + "-" + index}
                        >
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {mailProviderOptions.map((option) => (
                            <SelectItem key={option.value} value={option.value}>
                              {option.label}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    {type === "cloudmail_gen" ||
                    type === "cloudflare_temp_email" ||
                    type === "mailfree" ||
                    type === "moemail" ||
                    type === "inbucket" ||
                    type === "yyds_mail" ||
                    type === "ddg_mail" ? (
                      <>
                        <div className="space-y-2">
                          <label
                            className="text-sm text-foreground/80"
                            htmlFor={fieldId + "-16" + "-" + index}
                          >
                            {type === "cloudmail_gen" ? "CloudMail URL" : "API Base"}
                          </label>
                          <Input
                            value={String(provider.api_base || "")}
                            onChange={(event) =>
                              updateProvider(index, { api_base: event.target.value })
                            }
                            className="h-10 rounded-xl border-border bg-card"
                            disabled={config.enabled}
                            id={fieldId + "-16" + "-" + index}
                          />
                        </div>
                        {type === "cloudmail_gen" ? (
                          <>
                            <div className="space-y-2">
                              <label
                                className="text-sm text-foreground/80"
                                htmlFor={fieldId + "-17" + "-" + index}
                              >
                                管理员邮箱
                              </label>
                              <Input
                                value={String(provider.admin_email || "")}
                                onChange={(event) =>
                                  updateProvider(index, { admin_email: event.target.value })
                                }
                                className="h-10 rounded-xl border-border bg-card"
                                disabled={config.enabled}
                                id={fieldId + "-17" + "-" + index}
                              />
                            </div>
                            <div className="space-y-2">
                              <label
                                className="text-sm text-foreground/80"
                                htmlFor={fieldId + "-18" + "-" + index}
                              >
                                管理员密码
                              </label>
                              <Input
                                value={String(provider.admin_password || "")}
                                onChange={(event) =>
                                  updateProvider(index, { admin_password: event.target.value })
                                }
                                className="h-10 rounded-xl border-border bg-card"
                                disabled={config.enabled}
                                id={fieldId + "-18" + "-" + index}
                              />
                            </div>
                          </>
                        ) : null}
                        {type === "cloudflare_temp_email" ||
                        type === "mailfree" ||
                        type === "ddg_mail" ? (
                          <div className="space-y-2">
                            <label
                              className="text-sm text-foreground/80"
                              htmlFor={fieldId + "-19" + "-" + index}
                            >
                              {type === "mailfree" ? "Admin Token / Password" : "Admin Password"}
                            </label>
                            <Input
                              value={String(provider.admin_password || "")}
                              onChange={(event) =>
                                updateProvider(index, { admin_password: event.target.value })
                              }
                              placeholder={
                                type === "mailfree"
                                  ? "留空则使用容器环境变量 MAIL_ADMIN_TOKEN / MAILFREE_ADMIN_TOKEN"
                                  : ""
                              }
                              className="h-10 rounded-xl border-border bg-card"
                              disabled={config.enabled}
                              id={fieldId + "-19" + "-" + index}
                            />
                          </div>
                        ) : null}
                      </>
                    ) : null}
                    {type === "ddg_mail" ? (
                      <>
                        <div className="space-y-2">
                          <label
                            className="text-sm text-foreground/80"
                            htmlFor={fieldId + "-20" + "-" + index}
                          >
                            DDG Token <span className="text-destructive">*</span>
                          </label>
                          <Input
                            value={String(provider.ddg_token || "")}
                            onChange={(event) =>
                              updateProvider(index, { ddg_token: event.target.value })
                            }
                            className="h-10 rounded-xl border-border bg-card"
                            disabled={config.enabled}
                            placeholder="DuckDuckGo Email Protection 的 Bearer Token"
                            id={fieldId + "-20" + "-" + index}
                          />
                        </div>
                        <div className="space-y-2">
                          <label
                            className="text-sm text-foreground/80"
                            htmlFor={fieldId + "-21" + "-" + index}
                          >
                            CF Inbox JWT <span className="text-destructive">*</span>
                          </label>
                          <Input
                            value={String(provider.cf_inbox_jwt || "")}
                            onChange={(event) =>
                              updateProvider(index, { cf_inbox_jwt: event.target.value })
                            }
                            className="h-10 rounded-xl border-border bg-card"
                            disabled={config.enabled}
                            placeholder="CF 临时邮箱后端的固定收件箱 JWT（DDG 转发目标）"
                            id={fieldId + "-21" + "-" + index}
                          />
                        </div>
                        <div className="rounded-lg border border-warning/25 bg-warning/10 p-3 text-xs text-warning">
                          <p className="font-medium mb-1">使用说明</p>
                          <ol className="list-decimal list-inside space-y-0.5">
                            <li>
                              先在{" "}
                              <a
                                href="https://duckduckgo.com/email/"
                                target="_blank"
                                className="underline"
                              >
                                DuckDuckGo Email Protection
                              </a>{" "}
                              登录并设置转发目标为 CF 收件箱地址
                            </li>
                            <li>
                              DDG Token 从浏览器 DevTools → Network → quack.duckduckgo.com
                              请求中获取{" "}
                              <code className="bg-warning/10 px-1 rounded">
                                Authorization: Bearer
                              </code>
                            </li>
                            <li>CF Inbox JWT 从 CF 临时邮箱后端创建固定收件箱后获取</li>
                            <li>
                              所有 @duck.com 别名收到的邮件会转发到同一个 CF 收件箱，系统按 To:
                              头自动匹配
                            </li>
                          </ol>
                        </div>
                      </>
                    ) : null}
                    {type === "inbucket" ? (
                      <label className="flex items-center gap-3 pt-8 text-sm text-foreground/80">
                        <Checkbox
                          checked={Boolean(provider.random_subdomain ?? true)}
                          onCheckedChange={(checked) =>
                            updateProvider(index, { random_subdomain: Boolean(checked) })
                          }
                          disabled={config.enabled}
                          aria-label="启用随机子域名"
                        />
                        启用随机子域名
                      </label>
                    ) : null}
                    {type === "mailfree" ? (
                      <div className="space-y-2">
                        <label
                          className="text-sm text-foreground/80"
                          htmlFor={fieldId + "-22" + "-" + index}
                        >
                          Domain Index / Indices
                        </label>
                        <Input
                          value={domainIndices}
                          onChange={(event) =>
                            updateProvider(index, {
                              domain_indices: event.target.value
                                .split(/[\n,]/)
                                .map((item) => Number(item.trim()))
                                .filter((item) => Number.isInteger(item) && item >= 0),
                            })
                          }
                          placeholder="4 或 482,1383,1443,..."
                          className="h-10 rounded-xl border-border bg-card"
                          disabled={config.enabled}
                          id={fieldId + "-22" + "-" + index}
                        />
                      </div>
                    ) : null}
                    {type === "tempmail_lol" ||
                    type === "moemail" ||
                    type === "duckmail" ||
                    type === "gptmail" ||
                    type === "yyds_mail" ? (
                      <div className="space-y-2">
                        <label
                          className="text-sm text-foreground/80"
                          htmlFor={fieldId + "-23" + "-" + index}
                        >
                          API Key
                        </label>
                        <Input
                          value={String(provider.api_key || "")}
                          onChange={(event) =>
                            updateProvider(index, { api_key: event.target.value })
                          }
                          className="h-10 rounded-xl border-border bg-card"
                          disabled={config.enabled}
                          id={fieldId + "-23" + "-" + index}
                        />
                      </div>
                    ) : null}
                    <RandomDomainProviderFields
                      providerName={type}
                      provider={provider}
                      disabled={config.enabled}
                      onChange={(patch) => updateProvider(index, patch)}
                    />
                    {type === "outlook_external" ? (
                      <OutlookExternalProviderFields
                        provider={provider}
                        disabled={config.enabled}
                        onChange={(patch) => updateProvider(index, patch)}
                      />
                    ) : null}
                    {type === "duckmail" || type === "gptmail" ? (
                      <div className="space-y-2">
                        <label
                          className="text-sm text-foreground/80"
                          htmlFor={fieldId + "-24" + "-" + index}
                        >
                          Default Domain
                        </label>
                        <Input
                          value={String(provider.default_domain || "")}
                          onChange={(event) =>
                            updateProvider(index, { default_domain: event.target.value })
                          }
                          placeholder={type === "duckmail" ? "duckmail.sbs" : ""}
                          className="h-10 rounded-xl border-border bg-card"
                          disabled={config.enabled}
                          id={fieldId + "-24" + "-" + index}
                        />
                      </div>
                    ) : null}
                    {type === "yyds_mail" ? (
                      <>
                        <div className="space-y-2">
                          <label
                            className="text-sm text-foreground/80"
                            htmlFor={fieldId + "-25" + "-" + index}
                          >
                            Subdomain
                          </label>
                          <Input
                            value={String(provider.subdomain || "")}
                            onChange={(event) =>
                              updateProvider(index, { subdomain: event.target.value })
                            }
                            className="h-10 rounded-xl border-border bg-card"
                            disabled={config.enabled}
                            id={fieldId + "-25" + "-" + index}
                          />
                        </div>
                        <label className="flex items-center gap-3 pt-8 text-sm text-foreground/80">
                          <Checkbox
                            checked={Boolean(provider.wildcard)}
                            onCheckedChange={(checked) =>
                              updateProvider(index, { wildcard: Boolean(checked) })
                            }
                            disabled={config.enabled}
                            aria-label="Wildcard"
                          />
                          Wildcard
                        </label>
                      </>
                    ) : null}
                    {type === "outlook_token" ? (
                      <>
                        <div className="space-y-2">
                          <label
                            className="text-sm text-foreground/80"
                            htmlFor={fieldId + "-26" + "-" + index}
                          >
                            读取方式
                          </label>
                          <Select
                            value={String(provider.mode || "graph")}
                            onValueChange={(value) => updateProvider(index, { mode: value })}
                            disabled={config.enabled}
                          >
                            <SelectTrigger
                              className="h-10 rounded-xl border-border bg-card"
                              id={fieldId + "-26" + "-" + index}
                            >
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value="graph">Graph API</SelectItem>
                              <SelectItem value="imap">IMAP (XOAUTH2)</SelectItem>
                              <SelectItem value="auto">自动 (Graph→IMAP)</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                        {String(provider.mode || "graph") !== "graph" ? (
                          <div className="space-y-2">
                            <label
                              className="text-sm text-foreground/80"
                              htmlFor={fieldId + "-27" + "-" + index}
                            >
                              IMAP Host
                            </label>
                            <Input
                              value={String(provider.imap_host || "outlook.office365.com")}
                              onChange={(event) =>
                                updateProvider(index, { imap_host: event.target.value })
                              }
                              className="h-10 rounded-xl border-border bg-card"
                              disabled={config.enabled}
                              id={fieldId + "-27" + "-" + index}
                            />
                          </div>
                        ) : null}
                      </>
                    ) : null}
                  </div>

                  {type === "outlook_token"
                    ? (() => {
                        const stats = (provider.mailboxes_stats || {}) as Record<string, number>;
                        const savedCount = Number(provider.mailboxes_count || 0);
                        const preview = Array.isArray(provider.mailboxes_preview)
                          ? (provider.mailboxes_preview as string[])
                          : [];
                        const pendingCount = String(provider.mailboxes || "")
                          .split(/\r?\n/)
                          .filter(
                            (line) => line.includes("----") && line.split("----").length >= 4,
                          ).length;
                        return (
                          <div className="space-y-2">
                            <label
                              className="flex items-center justify-between text-sm text-foreground/80"
                              htmlFor={fieldId + "-28" + "-" + index}
                            >
                              <span>
                                邮箱池导入 <span className="text-destructive">*</span>
                              </span>
                              <span className="text-xs text-muted-foreground">
                                已保存 {savedCount} 个
                                {pendingCount ? ` · 待导入 ${pendingCount} 个` : ""}
                              </span>
                            </label>
                            <Textarea
                              value={String(provider.mailboxes || "")}
                              onChange={(event) =>
                                updateProvider(index, { mailboxes: event.target.value })
                              }
                              placeholder={
                                "每行一个邮箱，格式：\n邮箱----密码----client_id----refresh_token\n（出于安全，已保存的密码/refresh_token 不会回显；此处仅用于新增或覆盖）"
                              }
                              className="min-h-32 rounded-xl border-border bg-card font-mono text-xs"
                              disabled={config.enabled}
                              id={fieldId + "-28" + "-" + index}
                            />
                            <div className="flex flex-wrap items-center gap-1.5 text-xs">
                              <span className="rounded-md bg-muted px-2 py-1 text-foreground/80">
                                未使用 {stats.unused ?? 0}
                              </span>
                              <span className="rounded-md bg-info/10 px-2 py-1 text-info">
                                占用中 {stats.in_use ?? 0}
                              </span>
                              <span className="rounded-md bg-success/10 px-2 py-1 text-success">
                                已用 {stats.used ?? 0}
                              </span>
                              <span className="rounded-md bg-warning/10 px-2 py-1 text-warning">
                                token失效 {stats.token_invalid ?? 0}
                              </span>
                              <span className="rounded-md bg-destructive/10 px-2 py-1 text-destructive">
                                失败 {stats.failed ?? 0}
                              </span>
                            </div>
                            {preview.length ? (
                              <p className="text-xs text-muted-foreground">
                                已保存邮箱（脱敏）：{preview.slice(0, 8).join("、")}
                                {preview.length > 8 ? ` 等 ${preview.length} 个` : ""}
                              </p>
                            ) : null}
                            <div className="flex flex-wrap items-center gap-2">
                              <Button
                                type="button"
                                variant="outline"
                                className="h-8 rounded-lg border-border bg-card px-3 text-xs text-foreground/80"
                                onClick={() => void resetOutlookPool("failed")}
                                disabled={config.enabled}
                              >
                                清除失败/占用状态
                              </Button>
                              <Button
                                type="button"
                                variant="outline"
                                className="h-8 rounded-lg border-warning/25 bg-card px-3 text-xs text-warning hover:bg-warning/10"
                                onClick={() => {
                                  if (
                                    window.confirm(
                                      "确定要从 Outlook 邮箱池中删除所有未使用邮箱吗？此操作会移除这些已保存凭据。",
                                    )
                                  )
                                    void resetOutlookPool("unused");
                                }}
                                disabled={config.enabled}
                              >
                                清空未使用
                              </Button>
                              <Button
                                type="button"
                                variant="outline"
                                className="h-8 rounded-lg border-destructive/25 bg-card px-3 text-xs text-destructive hover:bg-destructive/10"
                                onClick={() => {
                                  if (
                                    window.confirm(
                                      "确定要重置整个 Outlook 邮箱池状态吗？所有邮箱会被标记为可重新使用。",
                                    )
                                  )
                                    void resetOutlookPool("all");
                                }}
                                disabled={config.enabled}
                              >
                                重置全部状态
                              </Button>
                            </div>
                            <p className="text-xs text-muted-foreground">
                              每个邮箱仅成功注册一次（状态记录在
                              data/outlook_token_used.json）。失败的邮箱会被标记原因，可用上方按钮释放后重试。
                            </p>
                          </div>
                        );
                      })()
                    : null}

                  {type === "cloudmail_gen" ||
                  type === "tempmail_lol" ||
                  type === "cloudflare_temp_email" ||
                  type === "moemail" ||
                  type === "inbucket" ||
                  type === "yyds_mail" ||
                  type === "ddg_mail" ? (
                    <div className="space-y-2">
                      <label
                        className="text-sm text-foreground/80"
                        htmlFor={fieldId + "-29" + "-" + index}
                      >
                        {type === "cloudmail_gen"
                          ? "邮箱域名"
                          : type === "inbucket"
                            ? "基础域名列表"
                            : "Domain"}
                      </label>
                      <Textarea
                        value={domains}
                        onChange={(event) =>
                          updateProvider(index, {
                            domain: event.target.value.split(/[\n,]/).map((item) => item.trim()),
                          })
                        }
                        placeholder={
                          type === "cloudmail_gen"
                            ? "每行一个域名，留空则使用服务默认域名"
                            : type === "inbucket"
                              ? "每行一个基础域名，系统会自动生成随机子域名"
                              : type === "moemail"
                                ? "每行一个域名"
                                : "每行一个域名，留空则使用服务默认域名"
                        }
                        className="min-h-20 rounded-xl border-border bg-card font-mono text-xs"
                        disabled={config.enabled}
                        id={fieldId + "-29" + "-" + index}
                      />
                    </div>
                  ) : null}
                  {type === "cloudmail_gen" ? (
                    <div className="space-y-2">
                      <label
                        className="text-sm text-foreground/80"
                        htmlFor={fieldId + "-30" + "-" + index}
                      >
                        子域名（支持多个）
                      </label>
                      <Textarea
                        value={subdomains}
                        onChange={(event) =>
                          updateProvider(index, {
                            subdomain: event.target.value.split(/[\n,]/).map((item) => item.trim()),
                          })
                        }
                        placeholder="每行一个子域名前缀，留空则直接使用主域名"
                        className="min-h-20 rounded-xl border-border bg-card font-mono text-xs"
                        disabled={config.enabled}
                        id={fieldId + "-30" + "-" + index}
                      />
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        </details>
      </section>

      <section className="flex min-h-0 flex-col p-4">
        <div className="space-y-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold tracking-tight">运行结果</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                先确认准备情况，开始后在这里查看进度和结果。
              </p>
            </div>
            <Badge variant={config.enabled ? "success" : "secondary"} className="rounded-md">
              {config.enabled ? "运行中" : "已停止"}
            </Badge>
          </div>
          <div
            role="status"
            className="space-y-2 rounded-xl border border-border bg-muted/40 p-4"
            data-testid="registration-readiness"
          >
            <p className="font-semibold">{heading}</p>
            <p className="text-sm leading-6 text-muted-foreground">{message}</p>
            {shortfall > 0 &&
              bindings &&
              bindings.remaining > 0 &&
              config.mode !== "quota" &&
              !config.enabled && (
                <Button variant="outline" onClick={adjustTarget}>
                  将目标调整为{" "}
                  {config.mode === "available"
                    ? currentAccounts + bindings.remaining
                    : bindings.remaining}{" "}
                  个
                </Button>
              )}
            {shortfall > 0 && (
              <p className="text-xs text-muted-foreground">
                要保留当前目标，需要先增加账号绑定容量；减少同时执行数不会增加名额。
              </p>
            )}
            {dirty && (
              <p className="text-xs text-warning">配置尚未保存。开始任务时会先保存当前配置。</p>
            )}
          </div>
          {(stats.last_error || stats.finish_reason || stats.start_blocked_reason) && (
            <details className="rounded-xl border border-border p-3 text-sm">
              <summary className="cursor-pointer">上次任务说明</summary>
              <p className="mt-2 text-muted-foreground">
                {stats.last_error ||
                  {
                    target_reached: "目标已达成",
                    stopped_by_user: "已手动停止",
                    interrupted_by_restart: "任务被应用重启中断",
                  }[stats.finish_reason || ""] ||
                  "上次任务已结束，请查看日志。"}
              </p>
              {stats.start_blocked_reason && (
                <p className="mt-2 text-xs text-muted-foreground">
                  上次任务未启动，历史结果保留；上方准备情况按当前配置重新计算。
                </p>
              )}
            </details>
          )}
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {[
              ["成功 / 成功率", `${stats.success} / ${stats.success_rate || 0}%`],
              ["失败", stats.fail],
              ["完成", stats.done],
              ["运行 / 线程", `${stats.running} / ${stats.threads}`],
              ["运行时间", `${stats.elapsed_seconds || 0}s`],
              ["平均注册单个", `${stats.avg_seconds || 0}s`],
              ["当前正常号池额度", stats.current_quota || 0],
              ["当前号池正常账号", stats.current_available || 0],
            ].map(([label, value]) => (
              <div key={label} className="border border-border bg-card/70 px-3 py-2">
                <div className="text-xs text-muted-foreground">{label}</div>
                <div className="mt-1 text-base font-semibold text-foreground">{value}</div>
              </div>
            ))}
          </div>
          <div className="grid grid-cols-3 gap-2">
            <Button
              className="h-10 rounded-xl bg-primary px-3 text-primary-foreground hover:bg-primary/90"
              onClick={handleToggleRegister}
              disabled={isSaving || (!config.enabled && blocked)}
            >
              {isSaving ? (
                <LoaderCircle className="size-4 animate-spin" />
              ) : config.enabled ? (
                <Square className="size-4" />
              ) : (
                <Play className="size-4" />
              )}
              {config.enabled ? "停止任务" : dirty ? "保存并开始" : "开始任务"}
            </Button>
            <Button
              variant="outline"
              className="h-10 rounded-xl border-border bg-card px-3 text-foreground/80"
              onClick={() => void reset()}
              disabled={isSaving || config.enabled}
            >
              <RotateCcw className="size-4" />
              重置
            </Button>
            <Button
              variant="outline"
              className="h-10 rounded-xl border-border bg-card px-3 text-foreground/80"
              onClick={() => void save()}
              disabled={isSaving || config.enabled}
            >
              <Save className="size-4" />
              保存
            </Button>
          </div>
          <div className="flex items-center gap-2 border border-warning/25 bg-warning/10 px-3 py-2 text-xs text-warning">
            <AlertTriangle className="size-4 shrink-0" />
            开始前会再次确认并保存配置。仅调整目标或刷新连接不会自动启动任务。
          </div>
        </div>

        <div className="mt-4 flex min-h-0 flex-1 flex-col space-y-3 overflow-hidden border-t border-border pt-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="text-sm font-semibold text-foreground">任务日志</h3>
              <p className="mt-1 text-xs text-warning">失败时会在这里记录具体步骤和原因。</p>
            </div>
            <Badge variant="secondary" className="rounded-md">
              {logs.length}
            </Badge>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto border border-border bg-card/70 p-3 font-mono text-xs leading-6">
            {logs.length === 0 ? (
              <div className="text-muted-foreground">任务开始后显示进度。</div>
            ) : (
              logs
                .slice()
                .reverse()
                .map((item, index) => (
                  <div
                    key={`${item.time}-${index}`}
                    className={
                      item.level === "red"
                        ? "text-destructive"
                        : item.level === "green"
                          ? "text-success"
                          : item.level === "yellow"
                            ? "text-warning"
                            : "text-foreground/80"
                    }
                  >
                    <span className="text-muted-foreground">
                      {new Date(item.time).toLocaleTimeString()}
                    </span>
                    <span className="pl-2">{item.text}</span>
                  </div>
                ))
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
