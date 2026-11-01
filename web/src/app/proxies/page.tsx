"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { LoaderCircle, RefreshCw, Upload, Play, CheckCircle2 } from "lucide-react";
import { toast } from "sonner";
import { SharedProxyStatus } from "@/components/shared-proxy-status";
import { ResourceError } from "@/components/resource-error";
import { useConfirmation } from "@/components/confirm-dialog";
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
import { Card, CardContent } from "@/components/ui/card";
import { useProxyPlatform } from "@/hooks/use-proxy-platform";
import {
  activateProxies,
  deleteProxies,
  fetchProxies,
  importProxies,
  reportProxyEvent,
  testProxyPool,
  type ProxyNode,
} from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

function ProxiesContent() {
  const platform = useProxyPlatform();
  const { confirm, confirmationDialog } = useConfirmation();
  const sequence = useRef({ id: 0 });
  const [items, setItems] = useState<ProxyNode[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [text, setText] = useState("");
  const [provider, setProvider] = useState("手动导入");
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [page, setPage] = useState(0);
  const [notice, setNotice] = useState("");
  const [invalid, setInvalid] = useState<Array<{ line: number; reason: string }>>([]);
  const lineCount = useMemo(() => text.split(/\r?\n/).filter((line) => line.trim()).length, [text]);
  const load = useCallback(async () => {
    const id = ++sequence.current.id;
    try {
      const result = await fetchProxies();
      if (sequence.current.id !== id) return;
      setItems(result.items);
      const existingIds = new Set(result.items.map((item) => item.id));
      setSelected((current) => current.filter((value) => existingIds.has(value)));
      setError("");
    } catch (failure) {
      if (sequence.current.id === id)
        setError(failure instanceof Error ? failure.message : "加载代理失败");
    } finally {
      if (sequence.current.id === id) setLoading(false);
    }
  }, []);
  useEffect(() => {
    const request = sequence.current;
    const timer = setTimeout(() => void load(), 0);
    const refresh = () => {
      if (!document.hidden) void load();
    };
    const interval = setInterval(refresh, 15000);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      clearTimeout(timer);
      clearInterval(interval);
      document.removeEventListener("visibilitychange", refresh);
      request.id++;
    };
  }, [load]);
  const visible = useMemo(
    () =>
      items.filter((item) => {
        if (item.protected) return false;
        const matches =
          filter === "all" ||
          (filter === "ready"
            ? item.assignment_ready
            : filter === "enabled"
              ? item.assignment_enabled
              : filter === "untested"
                ? !item.probe
                : item.probe && !item.assignment_ready);
        return (
          matches &&
          (!query ||
            [item.proxy, item.country, item.provider, item.probe?.exit_ip].some((value) =>
              String(value || "")
                .toLowerCase()
                .includes(query.toLowerCase()),
            ))
        );
      }),
    [items, filter, query],
  );
  const pageCount = Math.max(1, Math.ceil(visible.length / 50));
  const currentPage = Math.min(page, pageCount - 1);
  const pageItems = visible.slice(currentPage * 50, (currentPage + 1) * 50);
  const itemById = new Map(items.map((item) => [item.id, item]));
  const selectedIds = new Set(selected);
  const runTests = async (ids: string[]) => {
    let passed = 0,
      failed = 0,
      skipped = 0;
    for (let start = 0; start < ids.length; start += 30) {
      setNotice(
        `正在测试 ${start + 1}–${Math.min(start + 30, ids.length)} / ${ids.length} 条代理…`,
      );
      const result = await testProxyPool({
        ids: ids.slice(start, start + 30),
        limit: 30,
        include_dead: true,
      });
      passed += result.ok;
      failed += result.failed;
      skipped += result.skipped;
      await load();
    }
    const message = `测试完成：${passed} 条可用，${failed} 条未通过，${skipped} 条跳过。连接和目标访问均通过后才能可分配 / 故障备用。`;
    setNotice(message);
    toast.success(message);
  };
  const importBatch = async (test: boolean) => {
    if (!text.trim()) return toast.error("请先粘贴代理地址，每行一条");
    if (lineCount > 2000) return toast.error("每批最多添加 2000 条代理，请分批导入");
    setBusy("import");
    try {
      const result = await importProxies(text, provider.trim() || "手动导入");
      setText("");
      setInvalid(result.invalid);
      setSelected(result.ids);
      setNotice(
        `新增 ${result.imported} 条，跳过重复 ${result.duplicates} 条，格式错误 ${result.invalid.length} 条。`,
      );
      await load();
      if (test && result.ids.length) await runTests(result.ids);
    } catch (failure) {
      toast.error(failure instanceof Error ? failure.message : "导入失败");
    } finally {
      setBusy("");
    }
  };
  const testSelected = async () => {
    const ids = selected.length
      ? selected
      : visible.filter((item) => !item.protected).map((item) => item.id);
    if (!ids.length) return toast.error("请先导入或选择代理");
    setBusy("test");
    try {
      await runTests(ids);
    } catch (failure) {
      toast.error(failure instanceof Error ? failure.message : "测试失败");
    } finally {
      setBusy("");
    }
  };
  const activate = async (enabled: boolean) => {
    const ids = selected.filter((id) =>
      Boolean(
        itemById.get(id) &&
        !itemById.get(id)?.protected &&
        (!enabled || itemById.get(id)?.assignment_ready),
      ),
    );
    if (!ids.length) return toast.error("请先选择通过测试的代理");
    if (
      !(await confirm({
        title: enabled ? "使用所选代理？" : "停止分配所选代理？",
        confirmLabel: enabled ? "确认启用" : "确认停用",
        destructive: false,
        description: enabled
          ? `将启用 ${ids.length} 条代理，每条可绑定 1 个账号，也可作为故障恢复备用线路。不会自动启动注册。`
          : "只停止分配新账号，已有账号保留原绑定。",
      }))
    )
      return;
    setBusy("activate");
    try {
      let updated = 0;
      for (let start = 0; start < ids.length; start += 2000) {
        const result = await activateProxies(ids.slice(start, start + 2000), enabled);
        updated += result.updated;
        setNotice(`已处理 ${updated} / ${ids.length} 条代理…`);
      }
      setNotice(`已${enabled ? "启用" : "停用"} ${updated} 条代理。自动恢复仅在连接故障时触发。`);
      await load();
      await platform.refresh();
    } catch (failure) {
      toast.error(failure instanceof Error ? failure.message : "操作失败");
    } finally {
      setBusy("");
    }
  };
  const remove = async () => {
    if (
      !(await confirm({
        title: "删除所选记录？",
        description: "仅删除未启用且未绑定账号的代理；当前连接与账号绑定不受影响。",
      }))
    )
      return;
    setBusy("delete");
    try {
      await deleteProxies({ ids: selected });
      await load();
    } catch (failure) {
      toast.error(failure instanceof Error ? failure.message : "删除失败");
    } finally {
      setBusy("");
    }
  };
  const manual = async (item: ProxyNode, code: string, status?: number) => {
    setBusy("feedback");
    try {
      await reportProxyEvent(item.id, code, status);
      await load();
    } catch (failure) {
      toast.error(failure instanceof Error ? failure.message : "记录失败");
    } finally {
      setBusy("");
    }
  };
  if (loading)
    return (
      <div className="flex min-h-64 items-center justify-center">
        <LoaderCircle className="size-5 animate-spin" />
      </div>
    );
  if (error) return <ResourceError message={error} onRetry={load} />;
  return (
    <section className="flex min-w-0 flex-col gap-5">
      {confirmationDialog}
      <div className="workbench-toolbar">
        <div>
          <h1 className="text-2xl font-semibold">代理池</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            批量添加、筛选和使用。账号线路失效时自动寻找同地区的合格备用代理。
          </p>
        </div>
      </div>
      <SharedProxyStatus state={platform} />
      <Card>
        <CardContent className="space-y-4 p-5">
          <div>
            <h2 className="font-semibold">批量添加代理</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              每批最多 2000 条，每行一个 HTTP、HTTPS 或 SOCKS5
              地址。重复地址自动跳过，凭据仅保存在服务端。
            </p>
          </div>
          <label className="sr-only" htmlFor="proxy-import-text">
            批量代理地址
          </label>
          <Textarea
            id="proxy-import-text"
            value={text}
            onChange={(event) => setText(event.target.value)}
            autoComplete="off"
            spellCheck={false}
            maxLength={2000000}
            disabled={Boolean(busy)}
            placeholder={"http://USER:PASSWORD@HOST:PORT\nhttp://USER:PASSWORD@HOST:PORT"}
            className="min-h-32 font-mono text-xs"
          />
          <p
            className={
              lineCount > 2000 ? "text-sm text-destructive" : "text-xs text-muted-foreground"
            }
            role={lineCount > 2000 ? "alert" : undefined}
          >
            已填写 {lineCount} / 2000 条
            {lineCount > 2000 ? "，请分批导入" : "；测试将自动分段进行，并显示进度。"}
          </p>
          <div className="flex flex-wrap items-end gap-3">
            <div className="min-w-0 flex-1 space-y-1">
              <label className="text-xs text-muted-foreground" htmlFor="proxy-provider">
                来源名称
              </label>
              <Input
                id="proxy-provider"
                value={provider}
                onChange={(event) => setProvider(event.target.value)}
                disabled={Boolean(busy)}
              />
            </div>
            <Button
              onClick={() => void importBatch(true)}
              disabled={Boolean(busy) || !text.trim() || lineCount > 2000}
            >
              <Upload className="size-4" />
              导入并测试
            </Button>
            <Button
              variant="outline"
              onClick={() => void importBatch(false)}
              disabled={Boolean(busy) || !text.trim() || lineCount > 2000}
            >
              仅导入
            </Button>
          </div>
          {notice && (
            <p role="status" className="rounded-xl bg-muted/50 p-3 text-sm">
              {busy && <LoaderCircle className="mr-2 inline size-4 animate-spin" />}
              {notice}
            </p>
          )}
          {invalid.length > 0 && (
            <details>
              <summary className="cursor-pointer text-sm">查看 {invalid.length} 条格式问题</summary>
              {invalid.map((entry) => (
                <p key={entry.line} className="mt-2 text-sm text-muted-foreground">
                  第 {entry.line} 行：{entry.reason}
                </p>
              ))}
            </details>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardContent className="space-y-4 p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="font-semibold">
                已添加的代理 · {items.filter((item) => !item.protected).length} 条
              </h2>
              <p className="mt-1 text-xs text-muted-foreground">
                可使用 {items.filter((item) => item.assignment_ready).length} 条 · 已启用{" "}
                {items.filter((item) => item.assignment_enabled).length}{" "}
                条。测试不会创建账号，也不代表模型额度可用。
              </p>
            </div>
            <Button
              variant="ghost"
              onClick={() => void load()}
              aria-label="刷新代理列表"
              disabled={Boolean(busy)}
            >
              <RefreshCw className="size-4" />
              刷新
            </Button>
          </div>
          <div className="flex flex-wrap gap-2">
            <Select
              value={filter}
              onValueChange={(value) => {
                setFilter(value);
                setPage(0);
              }}
            >
              <SelectTrigger className="w-36" aria-label="筛选代理">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部代理</SelectItem>
                <SelectItem value="ready">测试通过</SelectItem>
                <SelectItem value="enabled">已启用</SelectItem>
                <SelectItem value="untested">尚未测试</SelectItem>
                <SelectItem value="failed">未通过 / 已过期</SelectItem>
              </SelectContent>
            </Select>
            <Input
              className="min-w-0 flex-1 sm:max-w-72"
              placeholder="搜索地址、地区或来源"
              aria-label="搜索代理"
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setPage(0);
              }}
            />
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="mr-1 text-xs text-muted-foreground">已选 {selected.length} 条</span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void testSelected()}
              disabled={Boolean(busy)}
            >
              <Play className="size-4" />
              {selected.length ? "测试所选" : "测试列表"}
            </Button>
            <Button
              size="sm"
              onClick={() => void activate(true)}
              disabled={Boolean(busy) || !selected.some((id) => itemById.get(id)?.assignment_ready)}
            >
              <CheckCircle2 className="size-4" />
              使用已通过代理
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void activate(false)}
              disabled={Boolean(busy) || !selected.length}
            >
              停止分配
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void remove()}
              disabled={Boolean(busy) || !selected.length}
            >
              删除所选
            </Button>
          </div>
          <div
            className="overflow-x-auto"
            role="region"
            aria-label="代理列表，可左右滚动"
            tabIndex={0}
          >
            <table className="w-full min-w-[850px] text-left text-sm">
              <thead>
                <tr className="border-b border-border text-xs text-muted-foreground">
                  <th className="p-3">
                    <Checkbox
                      aria-label="选择列表中的代理"
                      checked={
                        visible.filter((item) => !item.protected).length > 0 &&
                        visible
                          .filter((item) => !item.protected)
                          .every((item) => selectedIds.has(item.id))
                      }
                      onCheckedChange={(checked) =>
                        setSelected(
                          checked
                            ? visible.filter((item) => !item.protected).map((item) => item.id)
                            : [],
                        )
                      }
                    />
                  </th>
                  <th className="p-3">代理地址</th>
                  <th className="p-3">连接</th>
                  <th className="p-3">目标访问</th>
                  <th className="p-3">出口 / 地区</th>
                  <th className="p-3">响应时间</th>
                  <th className="p-3">使用状态</th>
                  <th className="p-3">详情</th>
                </tr>
              </thead>
              <tbody>
                {pageItems.map((item) => (
                  <tr key={item.id} className="border-b border-border/60">
                    <td className="p-3">
                      <Checkbox
                        aria-label={`选择代理 ${item.id}`}
                        disabled={Boolean(item.protected) || Boolean(busy)}
                        checked={selectedIds.has(item.id)}
                        onCheckedChange={(checked) =>
                          setSelected((current) =>
                            checked
                              ? [...current.filter((id) => id !== item.id), item.id]
                              : current.filter((id) => id !== item.id),
                          )
                        }
                      />
                    </td>
                    <td className="max-w-72 p-3">
                      <p className="truncate font-mono text-xs" title={item.proxy}>
                        {item.proxy}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {item.protected ? "共享平台代理入口" : item.provider || "手动导入"}
                      </p>
                    </td>
                    <td className="p-3">
                      <Badge variant={item.probe?.network_ok ? "success" : "secondary"}>
                        {!item.probe ? "待测试" : item.probe.network_ok ? "连接正常" : "未通过"}
                      </Badge>
                    </td>
                    <td className="p-3">
                      {!item.probe
                        ? "待测试"
                        : item.probe.target_ok
                          ? "通过"
                          : item.probe.target_status
                            ? `返回 ${item.probe.target_status}`
                            : "连接未完成"}
                    </td>
                    <td className="p-3 text-xs">
                      <p className="font-mono">{item.probe?.exit_ip || "待确认"}</p>
                      <p className="text-muted-foreground">{item.probe?.country || "地区待确认"}</p>
                    </td>
                    <td className="p-3">
                      {item.probe?.latency_ms != null ? `${item.probe.latency_ms} ms` : "—"}
                    </td>
                    <td className="p-3">
                      {item.assignment_enabled
                        ? item.assignment_ready
                          ? "可分配 / 故障备用"
                          : "暂停分配，待复测"
                        : item.protected
                          ? "平台管理"
                          : item.assignment_ready
                            ? "可使用"
                            : "未启用"}
                    </td>
                    <td className="p-3">
                      <details>
                        <summary className="cursor-pointer text-xs">连接记录与维护</summary>
                        <div className="mt-2 space-y-2 text-xs text-muted-foreground">
                          <p>
                            {item.probe?.observed_at
                              ? `检查时间：${new Date(item.probe.observed_at).toLocaleString("zh-CN")}`
                              : "尚无记录"}
                          </p>
                          <p>
                            检查结果有效 30
                            分钟；已启用代理会自动复查。账号仅在代理连接故障时自动切换，不因账号额度或目标访问限制换线。
                          </p>
                          <details>
                            <summary className="cursor-pointer">手动记录结果</summary>
                            <p className="my-2">人工反馈不代替真实测试。</p>
                            <div className="flex gap-1">
                              <Button
                                size="sm"
                                variant="ghost"
                                disabled={Boolean(busy)}
                                onClick={() => void manual(item, "success")}
                              >
                                成功
                              </Button>
                              <Button
                                size="sm"
                                variant="ghost"
                                disabled={Boolean(busy)}
                                onClick={() => void manual(item, "proxy_or_challenge_blocked", 403)}
                              >
                                403
                              </Button>
                              <Button
                                size="sm"
                                variant="ghost"
                                disabled={Boolean(busy)}
                                onClick={() => void manual(item, "account_rate_limited", 429)}
                              >
                                429
                              </Button>
                            </div>
                          </details>
                        </div>
                      </details>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!visible.length && (
              <p className="p-8 text-center text-sm text-muted-foreground">
                {items.some((item) => !item.protected)
                  ? "没有符合筛选条件的代理"
                  : "还没有代理，在上方粘贴地址即可批量添加。"}
              </p>
            )}
          </div>
          {visible.length > 50 && (
            <nav
              aria-label="代理列表分页"
              className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground"
            >
              <span>共 {visible.length} 条 · 每页 50 条 · 已选项跨页保留</span>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  disabled={currentPage === 0}
                  onClick={() => setPage(Math.max(0, currentPage - 1))}
                >
                  上一页
                </Button>
                <span>
                  第 {currentPage + 1} / {pageCount} 页
                </span>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={currentPage >= pageCount - 1}
                  onClick={() => setPage(currentPage + 1)}
                >
                  下一页
                </Button>
              </div>
            </nav>
          )}
        </CardContent>
      </Card>
    </section>
  );
}

export default function ProxiesPage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin"]);
  if (isCheckingAuth || !session || session.role !== "admin")
    return (
      <div className="flex min-h-64 items-center justify-center">
        <LoaderCircle className="size-5 animate-spin" />
      </div>
    );
  return <ProxiesContent />;
}
