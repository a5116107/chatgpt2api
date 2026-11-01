"use client";

import NextImage from "next/image";

import { useEffect, useMemo, useState, useCallback } from "react";
import {
  ChevronLeft,
  ChevronRight,
  ImageIcon,
  LoaderCircle,
  RefreshCw,
  Search,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { DateRangeFilter } from "@/components/date-range-filter";
import { ImageLightbox } from "@/components/image-lightbox";
import { ImageThumbnail, getImageThumbnailUrl } from "@/components/image-thumbnail";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { deleteSystemLogs, fetchSystemLogs, type SystemLog } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

const LogType = {
  Call: "call",
  Account: "account",
} as const;

const typeLabels: Record<string, string> = {
  [LogType.Call]: "调用日志",
  [LogType.Account]: "账号管理日志",
};

function getDetailText(item: SystemLog, key: string) {
  const value = item.detail?.[key];
  return typeof value === "string" || typeof value === "number" ? String(value) : "-";
}

function formatDuration(item: SystemLog) {
  const value = item.detail?.duration_ms;
  return typeof value === "number" ? `${(value / 1000).toFixed(2)} s` : "-";
}

function getUrls(item: SystemLog | null) {
  const urls = item?.detail?.urls;
  return Array.isArray(urls) ? urls.filter((url): url is string => typeof url === "string") : [];
}

function getStatus(item: SystemLog) {
  const status = item.detail?.status;
  if (status === "success") return "成功";
  if (status === "failed") return "失败";
  return "-";
}

function LogsContent() {
  const [items, setItems] = useState<SystemLog[]>([]);
  const [type, setType] = useState<string>(LogType.Call);
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [detailLog, setDetailLog] = useState<SystemLog | null>(null);
  const [detailOpen, setDetailOpen] = useState(false);
  const [lightboxIndex, setLightboxIndex] = useState(0);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [page, setPage] = useState(1);
  const [isLoading, setIsLoading] = useState(true);
  const [isDeleting, setIsDeleting] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [deletingItems, setDeletingItems] = useState<SystemLog[]>([]);
  const detailUrls = getUrls(detailLog);
  const detailImages = detailUrls.map((url, index) => ({ id: `${index}`, src: url }));
  const isCallLog = type === LogType.Call;
  const pageSize = 10;
  const pageCount = Math.max(1, Math.ceil(items.length / pageSize));
  const safePage = Math.min(page, pageCount);
  const currentRows = items.slice((safePage - 1) * pageSize, safePage * pageSize);
  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds]);
  const currentPageSelected =
    currentRows.length > 0 && currentRows.every((item) => selectedSet.has(item.id));
  const allSelected = items.length > 0 && items.every((item) => selectedSet.has(item.id));

  const loadLogs = useCallback(
    () =>
      fetchSystemLogs({ type, start_date: startDate, end_date: endDate })
        .then((data) => {
          setItems(data.items);
          setSelectedIds((current) =>
            current.filter((id) => data.items.some((item) => item.id === id)),
          );
          setPage(1);
        })
        .catch((error: unknown) => {
          toast.error(error instanceof Error ? error.message : "加载日志失败");
        })
        .finally(() => {
          setIsLoading(false);
        }),
    [type, startDate, endDate],
  );
  const clearFilters = () => {
    setStartDate("");
    setEndDate("");
  };

  const openDetail = (item: SystemLog) => {
    setDetailLog(item);
    setDetailOpen(true);
  };

  const openLogImage = (item: SystemLog, index: number) => {
    setDetailLog(item);
    setLightboxIndex(index);
    setLightboxOpen(true);
  };

  const toggleIds = (ids: string[], checked: boolean) => {
    setSelectedIds((current) =>
      checked
        ? Array.from(new Set([...current, ...ids]))
        : current.filter((id) => !ids.includes(id)),
    );
  };

  const confirmDelete = async () => {
    const ids = deletingItems.map((item) => item.id);
    if (ids.length === 0) return;
    setIsDeleting(true);
    try {
      const data = await deleteSystemLogs(ids);
      toast.success(`已删除 ${data.removed} 条日志`);
      setDeletingItems([]);
      setSelectedIds((current) => current.filter((id) => !ids.includes(id)));
      if (detailLog && ids.includes(detailLog.id)) {
        setDetailOpen(false);
        setDetailLog(null);
      }
      await loadLogs();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "删除日志失败");
    } finally {
      setIsDeleting(false);
    }
  };

  useEffect(() => {
    void loadLogs();
  }, [loadLogs]);

  return (
    <section className="space-y-5">
      <div className="workbench-toolbar">
        <div className="space-y-1">
          <div className="text-xs font-semibold tracking-[0.18em] text-muted-foreground uppercase">
            Logs
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">日志管理</h1>
        </div>
        <div className="flex flex-wrap gap-2">
          <Select value={type} onValueChange={setType}>
            <SelectTrigger
              className="h-10 w-[150px] rounded-xl border-border bg-card"
              aria-label="筛选类型"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={LogType.Call}>调用日志</SelectItem>
              <SelectItem value={LogType.Account}>账号管理日志</SelectItem>
            </SelectContent>
          </Select>
          <DateRangeFilter
            startDate={startDate}
            endDate={endDate}
            onChange={(start, end) => {
              setStartDate(start);
              setEndDate(end);
            }}
          />
          <Button
            variant="outline"
            onClick={clearFilters}
            className="h-10 rounded-xl border-border bg-card px-4 text-foreground/80"
          >
            清除筛选条件
          </Button>
          <Button
            onClick={() => void loadLogs()}
            disabled={isLoading}
            className="h-10 rounded-xl bg-primary px-4 text-primary-foreground hover:bg-primary/90"
          >
            {isLoading ? (
              <LoaderCircle className="size-4 animate-spin" />
            ) : (
              <Search className="size-4" />
            )}
            查询
          </Button>
        </div>
      </div>

      <Card className="overflow-hidden rounded-2xl border-border bg-card shadow-sm">
        <CardContent className="p-0">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-4">
            <div className="flex flex-wrap items-center gap-3 text-sm text-foreground/80">
              <span>共 {items.length} 条</span>
              <label className="flex items-center gap-2">
                <Checkbox
                  checked={currentPageSelected}
                  onCheckedChange={(checked) =>
                    toggleIds(
                      currentRows.map((item) => item.id),
                      Boolean(checked),
                    )
                  }
                  aria-label="本页全选"
                />
                本页全选
              </label>
              <label className="flex items-center gap-2">
                <Checkbox
                  checked={allSelected}
                  onCheckedChange={(checked) =>
                    toggleIds(
                      items.map((item) => item.id),
                      Boolean(checked),
                    )
                  }
                  aria-label="全选结果"
                />
                全选结果
              </label>
              {selectedIds.length > 0 ? <span>已选 {selectedIds.length} 条</span> : null}
            </div>
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                className="h-8 rounded-lg px-3 text-muted-foreground"
                onClick={() => void loadLogs()}
                disabled={isLoading}
              >
                <RefreshCw className={`size-4 ${isLoading ? "animate-spin" : ""}`} />
                刷新
              </Button>
              <button
                type="button"
                className="text-sm text-muted-foreground hover:text-foreground disabled:text-muted-foreground"
                onClick={() => setSelectedIds([])}
                disabled={selectedIds.length === 0 || isDeleting}
              >
                取消选择
              </button>
              <Button
                variant="outline"
                className="h-8 rounded-lg border-destructive/25 bg-card px-3 text-destructive hover:bg-destructive/10"
                onClick={() => setDeletingItems(items.filter((item) => selectedSet.has(item.id)))}
                disabled={selectedIds.length === 0 || isDeleting}
              >
                <Trash2 className="size-4" />
                删除所选
              </Button>
            </div>
          </div>
          <div className="overflow-x-auto">
            <Table className="min-w-[900px]">
              <TableHeader>
                <TableRow>
                  <TableHead className="w-12"></TableHead>
                  <TableHead>时间</TableHead>
                  <TableHead>类型</TableHead>
                  {isCallLog ? <TableHead>令牌名称</TableHead> : null}
                  {isCallLog ? <TableHead>调用耗时</TableHead> : null}
                  {isCallLog ? <TableHead>状态</TableHead> : null}
                  {isCallLog ? <TableHead className="w-36">图片</TableHead> : null}
                  <TableHead>简述</TableHead>
                  <TableHead className="w-40">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {currentRows.map((item) => {
                  const urls = getUrls(item);
                  return (
                    <TableRow key={item.id} className="text-foreground/80">
                      <TableCell>
                        <Checkbox
                          checked={selectedSet.has(item.id)}
                          onCheckedChange={(checked) => toggleIds([item.id], Boolean(checked))}
                          aria-label="选择此项"
                        />
                      </TableCell>
                      <TableCell className="whitespace-nowrap">{item.time}</TableCell>
                      <TableCell>
                        <Badge variant="secondary" className="rounded-md">
                          {typeLabels[item.type] || item.type}
                        </Badge>
                      </TableCell>
                      {isCallLog ? <TableCell>{getDetailText(item, "key_name")}</TableCell> : null}
                      {isCallLog ? <TableCell>{formatDuration(item)}</TableCell> : null}
                      {isCallLog ? (
                        <TableCell>
                          <Badge
                            variant={item.detail?.status === "failed" ? "danger" : "success"}
                            className="rounded-md"
                          >
                            {getStatus(item)}
                          </Badge>
                        </TableCell>
                      ) : null}
                      {isCallLog ? (
                        <TableCell>
                          {urls.length ? (
                            <div className="flex items-center gap-1.5">
                              {urls.slice(0, 3).map((url, imageIndex) => (
                                <button
                                  key={`${url}-${imageIndex}`}
                                  type="button"
                                  className="relative size-9 overflow-hidden rounded-lg border border-border bg-muted"
                                  onClick={() => openLogImage(item, imageIndex)}
                                  title="预览图片"
                                  aria-label="预览图片"
                                >
                                  <ImageThumbnail
                                    src={url}
                                    thumbnailSrc={getImageThumbnailUrl(url)}
                                    className="h-full w-full"
                                  />
                                </button>
                              ))}
                              {urls.length > 3 ? (
                                <span className="text-xs text-muted-foreground">
                                  +{urls.length - 3}
                                </span>
                              ) : null}
                            </div>
                          ) : (
                            <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                              <ImageIcon className="size-3.5" />-
                            </span>
                          )}
                        </TableCell>
                      ) : null}
                      <TableCell className="max-w-[420px] truncate text-muted-foreground">
                        {item.summary || "-"}
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-1">
                          <Button
                            variant="ghost"
                            className="h-8 rounded-lg px-3 text-foreground/80"
                            onClick={() => openDetail(item)}
                          >
                            查看详情
                          </Button>
                          <Button
                            variant="ghost"
                            className="h-8 rounded-lg px-3 text-destructive hover:bg-destructive/10 hover:text-destructive"
                            onClick={() => setDeletingItems([item])}
                          >
                            删除
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
          <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-3 text-sm text-muted-foreground">
            <span>
              第 {safePage} / {pageCount} 页，共 {items.length} 条
            </span>
            <Button
              variant="outline"
              size="icon"
              className="size-9 rounded-lg border-border bg-card"
              disabled={safePage <= 1}
              onClick={() => setPage((value) => Math.max(1, value - 1))}
              aria-label="上一页"
            >
              <ChevronLeft className="size-4" />
            </Button>
            <Button
              variant="outline"
              size="icon"
              className="size-9 rounded-lg border-border bg-card"
              disabled={safePage >= pageCount}
              onClick={() => setPage((value) => Math.min(pageCount, value + 1))}
              aria-label="下一页"
            >
              <ChevronRight className="size-4" />
            </Button>
          </div>
          {!isLoading && items.length === 0 ? (
            <div className="px-6 py-14 text-center text-sm text-muted-foreground">没有找到日志</div>
          ) : null}
        </CardContent>
      </Card>
      <Dialog open={detailOpen} onOpenChange={setDetailOpen}>
        <DialogContent className="flex h-[min(88vh,860px)] w-[min(92vw,920px)] flex-col overflow-hidden rounded-2xl p-0">
          <DialogHeader className="shrink-0 border-b border-border px-6 py-5">
            <DialogTitle>日志详情</DialogTitle>
          </DialogHeader>
          <div className="flex-1 overflow-y-auto px-6 py-5">
            <div className="space-y-4">
              <div className="grid gap-3 rounded-xl border border-border bg-card p-4 text-sm text-foreground/80 md:grid-cols-2">
                {Object.entries(detailLog?.detail || {})
                  .filter(([key, value]) => key !== "urls" && typeof value !== "object")
                  .map(([key, value]) => (
                    <div key={key} className="flex items-start justify-between gap-4">
                      <span className="text-muted-foreground">{key}</span>
                      <span className="text-right font-medium break-all text-foreground/80">
                        {String(value)}
                      </span>
                    </div>
                  ))}
              </div>
              {detailUrls.length ? (
                <div className="grid gap-3 sm:grid-cols-2 md:grid-cols-3">
                  {detailUrls.map((url, index) => (
                    <button
                      key={url}
                      type="button"
                      className="aspect-square overflow-hidden rounded-xl border border-border bg-muted"
                      onClick={() => {
                        setLightboxIndex(index);
                        setLightboxOpen(true);
                      }}
                    >
                      <NextImage
                        src={url}
                        alt=""
                        className="h-full w-full object-cover"
                        unoptimized
                        width={1200}
                        height={1200}
                      />
                    </button>
                  ))}
                </div>
              ) : null}
              <pre className="max-h-[72vh] overflow-auto rounded-xl border border-border bg-muted p-4 text-xs leading-6 text-foreground/80">
                {JSON.stringify(detailLog?.detail || {}, null, 2)}
              </pre>
            </div>
          </div>
        </DialogContent>
      </Dialog>
      <ImageLightbox
        images={detailImages}
        currentIndex={lightboxIndex}
        open={lightboxOpen}
        onOpenChange={setLightboxOpen}
        onIndexChange={setLightboxIndex}
      />
      <Dialog
        open={deletingItems.length > 0}
        onOpenChange={(open) => (!open ? setDeletingItems([]) : null)}
      >
        <DialogContent showCloseButton={false} className="rounded-2xl p-6">
          <DialogHeader className="gap-2">
            <DialogTitle>{deletingItems.length === 1 ? "删除日志" : "删除所选日志"}</DialogTitle>
            <DialogDescription className="text-sm leading-6">
              确认删除 {deletingItems.length} 条日志吗？删除后无法恢复。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              className="rounded-xl"
              onClick={() => setDeletingItems([])}
              disabled={isDeleting}
            >
              取消
            </Button>
            <Button
              className="rounded-xl bg-destructive text-white hover:bg-destructive"
              onClick={() => void confirmDelete()}
              disabled={isDeleting || deletingItems.length === 0}
            >
              {isDeleting ? <LoaderCircle className="size-4 animate-spin" /> : null}
              确认删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </section>
  );
}

export default function LogsPage() {
  const { isCheckingAuth, session } = useAuthGuard(["admin"]);
  if (isCheckingAuth || !session || session.role !== "admin") {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <LoaderCircle className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  }
  return <LogsContent />;
}
