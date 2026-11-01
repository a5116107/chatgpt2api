"use client";

import NextImage from "next/image";
import { useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  Clock3,
  Download,
  EyeOff,
  ImageIcon,
  LoaderCircle,
  RotateCcw,
  SlidersHorizontal,
  Sparkles,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { creativeErrorDetail, creativeErrorSummary } from "@/lib/creative-error";
import { cn } from "@/lib/utils";
import type {
  ImageConversation,
  ImageTurnStatus,
  StoredImage,
  StoredReferenceImage,
} from "@/store/image-conversations";

export type ImageLightboxItem = {
  id: string;
  src: string;
  sizeLabel?: string;
  dimensions?: string;
};

type ImageResultsProps = {
  selectedConversation: ImageConversation | null;
  onOpenLightbox: (images: ImageLightboxItem[], index: number) => void;
  onContinueEdit: (conversationId: string, image: StoredImage | StoredReferenceImage) => void;
  onDeletePrompt: (conversationId: string, turnId: string) => void;
  onDeleteResults: (conversationId: string, turnId: string) => void;
  onReuseTurnConfig: (conversationId: string, turnId: string) => void | Promise<void>;
  onRegenerateTurn: (conversationId: string, turnId: string) => void | Promise<void>;
  onRetryImage: (conversationId: string, turnId: string, imageId: string) => void | Promise<void>;
  onTimeoutRetryContinue: (taskId: string) => void | Promise<void>;
  onDismissErrors: (conversationId: string, turnId: string) => void | Promise<void>;
  formatConversationTime: (value: string) => string;
};

function imageBlob(base64: string) {
  const binary = atob(base64);
  return new Blob([Uint8Array.from(binary, (char) => char.charCodeAt(0))], { type: "image/png" });
}

function useImageSources(conversation: ImageConversation | null) {
  const cache = useRef(new Map<string, string>());
  const [sources, setSources] = useState<Record<string, string>>({});
  useEffect(() => {
    const needed = new Set<string>();
    for (const turn of conversation?.turns || []) {
      if (turn.resultsDeleted) continue;
      for (const image of turn.images)
        if (image.status === "success" && image.b64_json) needed.add(image.b64_json);
    }
    for (const [key, url] of cache.current)
      if (!needed.has(key)) {
        URL.revokeObjectURL(url);
        cache.current.delete(key);
      }
    for (const key of needed)
      if (!cache.current.has(key)) {
        try {
          cache.current.set(key, URL.createObjectURL(imageBlob(key)));
        } catch {
          /* Corrupt historical image renders a recoverable error. */
        }
      }
    setSources(Object.fromEntries(cache.current));
  }, [conversation]);
  useEffect(() => {
    const owned = cache.current;
    return () => {
      for (const url of owned.values()) URL.revokeObjectURL(url);
      owned.clear();
    };
  }, []);
  return sources;
}

async function downloadStoredImage(image: StoredImage, index: number, original = false) {
  try {
    let blob: Blob;
    if (image.b64_json && !original) blob = imageBlob(image.b64_json);
    else {
      const source = original ? image.originalUrl || image.url : image.url;
      if (!source) throw new Error("没有可下载的图片资源");
      const url = new URL(source, window.location.origin);
      if (!["http:", "https:", "blob:", "data:"].includes(url.protocol))
        throw new Error("图片地址格式无效");
      const response = await fetch(url, { signal: AbortSignal.timeout(60000) });
      if (!response.ok) throw new Error("图片下载失败（" + response.status + "）");
      blob = await response.blob();
      if (!blob.type.startsWith("image/")) throw new Error("服务器返回的不是图片文件");
    }
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const extension = blob.type.includes("jpeg")
      ? "jpg"
      : blob.type.includes("webp")
        ? "webp"
        : "png";
    a.download = "image-" + (index + 1) + (original ? "-original" : "") + "." + extension;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) {
    toast.error(e instanceof Error ? creativeErrorSummary(e.message) : "下载失败，请重试");
  }
}

function ErrorDetails({ error }: { error: string }) {
  return (
    <div className="creative-error">
      <p>{creativeErrorSummary(error)}</p>
      <details>
        <summary>查看错误详情</summary>
        <pre>{creativeErrorDetail(error)}</pre>
      </details>
    </div>
  );
}

export function ImageResults({
  selectedConversation,
  onOpenLightbox,
  onContinueEdit,
  onDeletePrompt,
  onDeleteResults,
  onReuseTurnConfig,
  onRegenerateTurn,
  onRetryImage,
  onTimeoutRetryContinue,
  onDismissErrors,
  formatConversationTime,
}: ImageResultsProps) {
  const sources = useImageSources(selectedConversation);
  const [now, setNow] = useState(0);
  const hasLoading = selectedConversation?.turns.some(
    (turn) => !turn.resultsDeleted && turn.images.some((image) => image.status === "loading"),
  );
  useEffect(() => {
    if (!hasLoading) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [hasLoading]);
  const src = (image: StoredImage) =>
    image.b64_json ? sources[image.b64_json] || "" : image.url || "";

  if (!selectedConversation || selectedConversation.turns.length === 0)
    return (
      <div className="studio-empty">
        <span className="studio-empty-mark">
          <ImageIcon className="size-7" strokeWidth={1.4} />
        </span>
        <h2>让想象，有画面。</h2>
        <p>用文字描述你的想法，或添加参考图继续创作。</p>
        <div className="image-empty-examples">
          <span>产品视觉</span>
          <span>插画设计</span>
          <span>场景探索</span>
          <span>图片编辑</span>
        </div>
      </div>
    );

  return (
    <div className="image-thread">
      {selectedConversation.turns.map((turn, turnIndex) => {
        const successful = turn.images
          .filter((image) => image.status === "success" && src(image))
          .map((image) => ({
            id: image.id,
            src: src(image),
            dimensions:
              image.width && image.height ? image.width + " × " + image.height : undefined,
          }));
        const busy = turn.status === "generating" || turn.status === "queued";
        return (
          <article
            key={turn.id}
            className="image-turn"
            aria-label={"第 " + (turnIndex + 1) + " 轮生成"}
          >
            {!turn.promptDeleted && (
              <div className="ml-auto max-w-[92%] sm:max-w-[80%]">
                <div className="chat-user-bubble">
                  <p className="whitespace-pre-wrap break-words text-sm leading-7">{turn.prompt}</p>
                  {turn.referenceImages.length > 0 && (
                    <div className="mt-3 flex gap-2 overflow-x-auto">
                      {turn.referenceImages.map((image, index) => (
                        <button
                          key={index}
                          type="button"
                          className="size-16 shrink-0 overflow-hidden rounded-xl"
                          aria-label={"查看参考图 " + (index + 1)}
                          onClick={() =>
                            onOpenLightbox(
                              turn.referenceImages.map((ref, i) => ({
                                id: turn.id + "-ref-" + i,
                                src: ref.dataUrl,
                              })),
                              index,
                            )
                          }
                        >
                          <NextImage
                            src={image.dataUrl}
                            alt={image.name || "参考图"}
                            unoptimized
                            width={64}
                            height={64}
                            className="h-full w-full object-cover"
                          />
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                <div className="mt-2 flex flex-wrap items-center justify-end gap-1 text-xs text-muted-foreground">
                  <span className="mr-2">{formatConversationTime(turn.createdAt)}</span>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-8 px-2 text-xs"
                    onClick={() => void onReuseTurnConfig(selectedConversation.id, turn.id)}
                  >
                    <SlidersHorizontal className="size-3.5" />
                    复用参数
                  </Button>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-8"
                    aria-label="删除提示词"
                    onClick={() => onDeletePrompt(selectedConversation.id, turn.id)}
                  >
                    <Trash2 className="size-3.5" />
                  </Button>
                </div>
              </div>
            )}
            {!turn.resultsDeleted && (
              <div className="mt-6">
                <div className="mb-4 flex flex-wrap items-center gap-x-3 gap-y-2 text-xs text-muted-foreground">
                  <span className="inline-flex items-center gap-2 font-semibold text-foreground">
                    <Sparkles className="size-4" />
                    图像创作
                  </span>
                  <span>{getTurnStatusLabel(turn.status)}</span>
                  <span>
                    {turn.count} 张 · {turn.size || "自动尺寸"} ·{" "}
                    {turn.quality === "auto" ? "自动质量" : turn.quality}
                  </span>
                  <span className="max-w-[220px] truncate" title={turn.model}>
                    {turn.model}
                  </span>
                </div>
                <div
                  className={cn(
                    "image-result-grid",
                    turn.images.length === 1 && "image-result-single",
                  )}
                >
                  {turn.images.map((image, index) => {
                    if (image.status === "success") {
                      const imageSrc = src(image);
                      return (
                        <div key={image.id} className="image-result-card">
                          {imageSrc ? (
                            <ResultImage
                              src={imageSrc}
                              onOpen={() =>
                                onOpenLightbox(
                                  successful,
                                  successful.findIndex((item) => item.id === image.id),
                                )
                              }
                              label={"查看生成图片 " + (index + 1)}
                            />
                          ) : (
                            <div className="grid min-h-48 place-items-center rounded-2xl bg-muted text-sm text-muted-foreground">
                              {image.b64_json && !sources[image.b64_json]
                                ? "正在读取图片，或历史资源已损坏"
                                : "图片资源缺失"}
                            </div>
                          )}
                          <div className="flex flex-wrap items-center justify-between gap-2 pt-2">
                            <span className="text-[11px] text-muted-foreground">
                              {image.width && image.height
                                ? image.width + " × " + image.height
                                : "图片 " + (index + 1)}
                              {image.durationMs != null
                                ? " · " + (image.durationMs / 1000).toFixed(1) + "s"
                                : ""}
                            </span>
                            <div className="flex items-center gap-1">
                              <Button
                                variant="ghost"
                                size="sm"
                                className="h-8 px-2 text-xs"
                                onClick={() => onContinueEdit(selectedConversation.id, image)}
                              >
                                <Sparkles className="size-3.5" />
                                编辑
                              </Button>
                              <Button
                                variant="ghost"
                                size="icon"
                                className="size-8"
                                aria-label="下载图片"
                                onClick={() => void downloadStoredImage(image, index)}
                              >
                                <Download className="size-4" />
                              </Button>
                              {image.outputTransform === "lanczos_upscale" && image.originalUrl && (
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  className="h-8 px-2 text-xs"
                                  aria-label="下载原图"
                                  onClick={() => void downloadStoredImage(image, index, true)}
                                >
                                  原图
                                </Button>
                              )}
                            </div>
                          </div>
                        </div>
                      );
                    }
                    if (image.status === "error")
                      return (
                        <div key={image.id} className="image-failure-card">
                          <div className="mb-3 flex items-center gap-2 text-sm font-medium">
                            <AlertCircle className="size-4 text-destructive" />
                            图片 {index + 1} 未完成
                          </div>
                          <ErrorDetails error={image.error || "生成失败，请重试"} />
                          {image.accountHash ? (
                            <p className="text-xs text-muted-foreground">
                              本次使用账号：{image.accountHash}
                            </p>
                          ) : null}
                          <div className="mt-4 flex flex-wrap gap-2">
                            {image.taskId && /超时|timeout/i.test(image.error || "") && (
                              <Button
                                variant="outline"
                                size="sm"
                                onClick={() => void onTimeoutRetryContinue(image.taskId!)}
                              >
                                继续等待
                              </Button>
                            )}
                            <Button
                              variant="outline"
                              size="sm"
                              disabled={!turn.prompt.trim() || busy}
                              onClick={() =>
                                void onRetryImage(selectedConversation.id, turn.id, image.id)
                              }
                            >
                              <RotateCcw className="size-3.5" />
                              重新生成这一张
                            </Button>
                          </div>
                        </div>
                      );
                    const queued = image.taskStatus === "queued" || turn.status === "queued";
                    const elapsed =
                      image.elapsedSecs != null
                        ? Math.max(
                            0,
                            image.elapsedSecs +
                              (image.elapsedUpdatedAt && now
                                ? (now - image.elapsedUpdatedAt) / 1000
                                : 0),
                          )
                        : null;
                    return (
                      <div key={image.id} className="image-loading-card" role="status">
                        <div className="mb-3 grid size-12 place-items-center rounded-2xl bg-background">
                          {queued ? (
                            <Clock3 className="size-5" />
                          ) : (
                            <LoaderCircle className="size-5 animate-spin" />
                          )}
                        </div>
                        <p className="text-sm font-medium">
                          {queued ? "已加入队列" : getProgressLabel(image.progress)}
                        </p>
                        <p className="mt-2 text-xs text-muted-foreground">
                          图片 {index + 1} / {turn.images.length}
                          {elapsed !== null && !queued ? " · " + elapsed.toFixed(0) + "s" : ""}
                        </p>
                      </div>
                    );
                  })}
                </div>
                {turn.error && !turn.images.some((image) => image.status === "error") && (
                  <div className="mt-3">
                    <ErrorDetails error={turn.error} />
                  </div>
                )}
                <div className="mt-3 flex flex-wrap items-center gap-1 text-muted-foreground">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-8 px-2 text-xs"
                    disabled={busy || !turn.prompt.trim()}
                    onClick={() => void onRegenerateTurn(selectedConversation.id, turn.id)}
                  >
                    <RotateCcw className="size-3.5" />
                    全部重新生成
                  </Button>
                  {turn.images.some((image) => image.status === "error") && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-8 px-2 text-xs"
                      onClick={() => void onDismissErrors(selectedConversation.id, turn.id)}
                    >
                      <EyeOff className="size-3.5" />
                      隐藏失败项
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-8"
                    aria-label="删除生成结果"
                    onClick={() => onDeleteResults(selectedConversation.id, turn.id)}
                  >
                    <Trash2 className="size-3.5" />
                  </Button>
                </div>
              </div>
            )}
          </article>
        );
      })}
    </div>
  );
}

function ResultImage({ src, label, onOpen }: { src: string; label: string; onOpen: () => void }) {
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  if (failed)
    return (
      <div className="grid min-h-52 place-items-center gap-2 rounded-2xl bg-muted p-6 text-sm text-muted-foreground">
        <p>图片加载失败，历史记录已保留。</p>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            setAttempt((value) => value + 1);
            setFailed(false);
          }}
        >
          重新加载图片
        </Button>
      </div>
    );
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={label}
      className="group block w-full overflow-hidden rounded-2xl bg-muted focus-visible:ring-2 focus-visible:ring-ring"
    >
      <NextImage
        key={attempt}
        src={src}
        alt={label}
        unoptimized
        width={1000}
        height={1000}
        onError={() => setFailed(true)}
        className="max-h-[540px] w-full object-contain transition duration-200 group-hover:brightness-95"
      />
    </button>
  );
}

function getTurnStatusLabel(status: ImageTurnStatus) {
  return { queued: "排队中", generating: "处理中", success: "已完成", error: "部分或全部失败" }[
    status
  ];
}
const PROGRESS_LABELS: Record<string, string> = {
  getting_account: "正在准备账号",
  uploading: "正在上传参考图",
  bootstrapping: "正在连接服务",
  getting_token: "正在验证会话",
  preparing_conversation: "正在准备创作",
  starting_generation: "正在启动生成",
  generating: "正在生成画面",
  receiving_image: "正在接收图片",
};
function getProgressLabel(progress?: string) {
  return progress ? PROGRESS_LABELS[progress] || "正在生成画面" : "正在生成画面";
}
