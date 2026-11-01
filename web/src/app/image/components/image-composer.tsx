"use client";

import NextImage from "next/image";
import { toast } from "sonner";

import {
  ArrowUp,
  SlidersHorizontal,
  ImagePlus,
  LoaderCircle,
  RectangleHorizontal,
  RectangleVertical,
  Square,
  X,
} from "lucide-react";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ClipboardEvent,
  type DragEvent,
  type RefObject,
} from "react";

import { ImageLightbox } from "@/components/image-lightbox";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { ImageModel } from "@/lib/api";
import { cn } from "@/lib/utils";

type ImageComposerProps = {
  prompt: string;
  imageCount: string;
  imageRatio: string;
  imageTier: string;
  imageWidth: string;
  imageHeight: string;
  imageQuality: string;
  imageModel: ImageModel;
  imageModels: ImageModel[];
  availableQuota: string;
  activeTaskCount: number;
  referenceImages: Array<{ name: string; dataUrl: string }>;
  textareaRef: RefObject<HTMLTextAreaElement | null>;
  fileInputRef: RefObject<HTMLInputElement | null>;
  onPromptChange: (value: string) => void;
  onImageCountChange: (value: string) => void;
  onImageRatioChange: (value: string) => void;
  onImageTierChange: (value: string) => void;
  onImageWidthChange: (value: string) => void;
  onImageHeightChange: (value: string) => void;
  onImageQualityChange: (value: string) => void;
  onImageModelChange: (value: ImageModel) => void;
  onSubmit: () => void | Promise<void>;
  onPickReferenceImage: () => void;
  onReferenceImageChange: (files: File[]) => void | Promise<void>;
  onRemoveReferenceImage: (index: number) => void;
};

const imageFileNamePattern = /\.(avif|bmp|gif|heic|heif|ico|jpe?g|png|svg|tiff?|webp)$/i;

function isImageFile(file: File) {
  return file.type.startsWith("image/") || (!file.type && imageFileNamePattern.test(file.name));
}

function hasDraggedImages(dataTransfer: DataTransfer) {
  const items = Array.from(dataTransfer.items || []);
  if (items.length > 0) {
    return items.some(
      (item) => item.kind === "file" && (item.type.startsWith("image/") || !item.type),
    );
  }
  return Array.from(dataTransfer.files || []).some(isImageFile);
}

function getDraggedImageFiles(dataTransfer: DataTransfer) {
  return Array.from(dataTransfer.files || []).filter(isImageFile);
}

const qualityOptions = [
  { value: "auto", label: "自动" },
  { value: "low", label: "低" },
  { value: "medium", label: "中" },
  { value: "high", label: "高" },
];
const codexAspectOptions = [
  { ratio: "1:1", tier: "1k", width: "1024", height: "1024", label: "1:1", icon: Square },
  {
    ratio: "2:3",
    tier: "1k",
    width: "1024",
    height: "1536",
    label: "2:3",
    icon: RectangleVertical,
  },
  {
    ratio: "3:2",
    tier: "1k",
    width: "1536",
    height: "1024",
    label: "3:2",
    icon: RectangleHorizontal,
  },
  {
    ratio: "3:4",
    tier: "1k",
    width: "1024",
    height: "1365",
    label: "3:4",
    icon: RectangleVertical,
  },
  {
    ratio: "4:3",
    tier: "1k",
    width: "1365",
    height: "1024",
    label: "4:3",
    icon: RectangleHorizontal,
  },
  {
    ratio: "9:16",
    tier: "1k",
    width: "1088",
    height: "1920",
    label: "9:16",
    icon: RectangleVertical,
  },
  {
    ratio: "16:9",
    tier: "1k",
    width: "1920",
    height: "1088",
    label: "16:9",
    icon: RectangleHorizontal,
  },
  { ratio: "1:1", tier: "2k", width: "2048", height: "2048", label: "1:1(2k)", icon: Square },
  {
    ratio: "16:9",
    tier: "2k",
    width: "2560",
    height: "1440",
    label: "16:9(2k)",
    icon: RectangleHorizontal,
  },
  {
    ratio: "9:16",
    tier: "2k",
    width: "1440",
    height: "2560",
    label: "9:16(2k)",
    icon: RectangleVertical,
  },
  {
    ratio: "16:9",
    tier: "4k",
    width: "3840",
    height: "2160",
    label: "16:9(4k)",
    icon: RectangleHorizontal,
  },
  {
    ratio: "9:16",
    tier: "4k",
    width: "2160",
    height: "3840",
    label: "9:16(4k)",
    icon: RectangleVertical,
  },
  { ratio: "auto", tier: "auto", width: "1024", height: "1024", label: "auto", icon: null },
];
const webAspectOptions = [
  { ratio: "auto", tier: "auto", width: "1024", height: "1024", label: "auto", icon: null },
  { ratio: "1:1", tier: "1.5k", width: "1536", height: "1536", label: "1:1 (1.5K)", icon: Square },
  {
    ratio: "3:2",
    tier: "1.5k",
    width: "1536",
    height: "1024",
    label: "3:2 (1.5K)",
    icon: RectangleHorizontal,
  },
  {
    ratio: "2:3",
    tier: "1.5k",
    width: "1024",
    height: "1536",
    label: "2:3 (1.5K)",
    icon: RectangleVertical,
  },
  { ratio: "1:1", tier: "2k", width: "2048", height: "2048", label: "1:1 (2K)", icon: Square },
  {
    ratio: "16:9",
    tier: "2k",
    width: "2560",
    height: "1440",
    label: "16:9 (2K)",
    icon: RectangleHorizontal,
  },
  {
    ratio: "9:16",
    tier: "2k",
    width: "1440",
    height: "2560",
    label: "9:16 (2K)",
    icon: RectangleVertical,
  },
  { ratio: "1:1", tier: "4k", width: "4096", height: "4096", label: "1:1 (4K)", icon: Square },
  {
    ratio: "16:9",
    tier: "4k",
    width: "3840",
    height: "2160",
    label: "16:9 (4K)",
    icon: RectangleHorizontal,
  },
  {
    ratio: "9:16",
    tier: "4k",
    width: "2160",
    height: "3840",
    label: "9:16 (4K)",
    icon: RectangleVertical,
  },
];

export function ImageComposer({
  prompt,
  imageCount,
  imageRatio,
  imageTier,
  imageWidth,
  imageHeight,
  imageQuality,
  imageModel,
  imageModels,
  availableQuota,
  activeTaskCount,
  referenceImages,
  textareaRef,
  fileInputRef,
  onPromptChange,
  onImageCountChange,
  onImageRatioChange,
  onImageTierChange,
  onImageWidthChange,
  onImageHeightChange,
  onImageQualityChange,
  onImageModelChange,
  onSubmit,
  onPickReferenceImage,
  onReferenceImageChange,
  onRemoveReferenceImage,
}: ImageComposerProps) {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [lightboxOpen, setLightboxOpen] = useState(false);
  const [lightboxIndex, setLightboxIndex] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const submitLock = useRef(false);
  const lightboxImages = useMemo(
    () => referenceImages.map((image, index) => ({ id: image.name + index, src: image.dataUrl })),
    [referenceImages],
  );
  const isCodex = imageModel.toLowerCase().includes("codex");
  const aspectOptions = isCodex ? codexAspectOptions : webAspectOptions;
  const valid =
    Number.isInteger(Number(imageCount)) &&
    Number(imageCount) >= 1 &&
    Number(imageCount) <= 100 &&
    (imageRatio === "auto" ||
      (Number.isInteger(Number(imageWidth)) &&
        Number(imageWidth) > 0 &&
        Number.isInteger(Number(imageHeight)) &&
        Number(imageHeight) > 0 &&
        (isCodex || (Number(imageWidth) <= 4096 && Number(imageHeight) <= 4096))));
  useEffect(() => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = Math.min(el.scrollHeight, 160) + "px";
    }
  }, [prompt, textareaRef]);
  const submit = async () => {
    if (submitLock.current || !prompt.trim() || !valid || !imageModel) return;
    submitLock.current = true;
    setSubmitting(true);
    try {
      await onSubmit();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "提交失败，提示词已保留");
    } finally {
      submitLock.current = false;
      setSubmitting(false);
    }
  };
  const paste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(event.clipboardData.files).filter(isImageFile);
    if (files.length) {
      event.preventDefault();
      void onReferenceImageChange(files);
    }
  };
  const drag = (event: DragEvent<HTMLDivElement>) => {
    if (hasDraggedImages(event.dataTransfer)) {
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
      setDragging(true);
    }
  };
  return (
    <div className="studio-composer-wrap">
      <div className="mx-auto w-full max-w-[880px]">
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          multiple
          className="hidden"
          aria-label="选择参考图片"
          onChange={(event) => {
            void onReferenceImageChange(Array.from(event.target.files || []));
            event.target.value = "";
          }}
        />
        <ImageLightbox
          images={lightboxImages}
          currentIndex={lightboxIndex}
          open={lightboxOpen}
          onOpenChange={setLightboxOpen}
          onIndexChange={setLightboxIndex}
        />
        <div
          className={cn("studio-composer relative", dragging && "ring-2 ring-ring")}
          onDragEnter={drag}
          onDragOver={drag}
          onDragLeave={(event) => {
            if (
              !(event.relatedTarget instanceof Node) ||
              !event.currentTarget.contains(event.relatedTarget)
            )
              setDragging(false);
          }}
          onDrop={(event) => {
            const files = getDraggedImageFiles(event.dataTransfer);
            if (event.dataTransfer.files.length) event.preventDefault();
            setDragging(false);
            if (files.length) void onReferenceImageChange(files);
          }}
        >
          {referenceImages.length > 0 && (
            <div className="flex max-h-24 gap-3 overflow-x-auto px-4 pt-4 pb-2">
              {referenceImages.map((image, index) => (
                <div key={image.name + index} className="relative size-16 shrink-0">
                  <button
                    type="button"
                    aria-label={"预览参考图 " + (image.name || index + 1)}
                    className="block size-16 overflow-hidden rounded-xl border border-border"
                    onClick={() => {
                      setLightboxIndex(index);
                      setLightboxOpen(true);
                    }}
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
                  <button
                    type="button"
                    aria-label={"移除参考图 " + (image.name || index + 1)}
                    className="absolute -top-1 -right-1 grid size-6 place-items-center rounded-full border border-border bg-background"
                    onClick={() => onRemoveReferenceImage(index)}
                  >
                    <X className="size-3.5" />
                  </button>
                </div>
              ))}
            </div>
          )}
          <Textarea
            ref={textareaRef}
            aria-label="图片提示词"
            placeholder={
              referenceImages.length
                ? "描述你希望如何修改参考图…"
                : "描述你想创作的画面，也可以粘贴或拖入参考图…"
            }
            value={prompt}
            onChange={(event) => onPromptChange(event.target.value)}
            onPaste={paste}
            className="studio-textarea"
            onKeyDown={(event) => {
              if (
                event.key === "Enter" &&
                !event.shiftKey &&
                !event.nativeEvent.isComposing &&
                event.keyCode !== 229
              ) {
                event.preventDefault();
                void submit();
              }
            }}
          />
          <div className="flex items-center justify-between gap-2 px-3 pb-3">
            <div className="flex min-w-0 items-center gap-1 sm:gap-2">
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label="添加参考图"
                title="添加参考图"
                onClick={onPickReferenceImage}
                className="shrink-0 rounded-full"
              >
                <ImagePlus className="size-5" />
              </Button>
              <Button
                type="button"
                variant="ghost"
                className="min-w-0 rounded-full px-3 text-xs"
                aria-label="生成设置"
                onClick={() => setSettingsOpen(true)}
              >
                <SlidersHorizontal className="size-4 shrink-0" />
                <span className="truncate">
                  {imageRatio === "auto" ? "自动尺寸" : imageWidth + " × " + imageHeight} ·{" "}
                  {imageCount || 1} 张
                </span>
              </Button>
              <span className="hidden truncate text-[11px] text-muted-foreground xl:inline">
                {imageModel}
              </span>
            </div>
            <Button
              type="button"
              size="icon"
              className="shrink-0 rounded-full"
              aria-label={submitting ? "正在提交图片任务" : "生成图片"}
              disabled={submitting || !prompt.trim() || !valid || !imageModel}
              onClick={() => void submit()}
            >
              {submitting ? (
                <LoaderCircle className="size-5 animate-spin" />
              ) : (
                <ArrowUp className="size-5" />
              )}
            </Button>
          </div>
          {dragging && (
            <div className="pointer-events-none absolute inset-0 grid place-items-center rounded-3xl border-2 border-dashed border-primary bg-background/95 text-sm font-medium">
              松开以添加参考图
            </div>
          )}
        </div>
        <div className="studio-composer-hint flex flex-wrap items-center justify-center gap-x-3 gap-y-1">
          <span>{referenceImages.length ? "参考图编辑" : "文字生图"} · Enter 发送</span>
          <span>可用额度 {availableQuota}</span>
          {activeTaskCount > 0 && <span role="status">{activeTaskCount} 个任务处理中</span>}
          <span className="hidden sm:inline">历史仅保存在当前浏览器</span>
        </div>
        <Dialog open={settingsOpen} onOpenChange={setSettingsOpen}>
          <DialogContent className="max-h-[85dvh] overflow-y-auto sm:max-w-lg">
            <DialogHeader>
              <DialogTitle>生成设置</DialogTitle>
              <DialogDescription>选择模型、画面比例和质量。下一次生成时生效。</DialogDescription>
            </DialogHeader>
            <div className="grid grid-cols-2 gap-4">
              <div className="col-span-2 space-y-2">
                <Label htmlFor="image-model">图像模型</Label>
                <Select value={imageModel} onValueChange={onImageModelChange}>
                  <SelectTrigger id="image-model" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {[...new Set([imageModel, ...imageModels])].filter(Boolean).map((model) => (
                      <SelectItem key={model} value={model}>
                        {model}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="image-quality">质量</Label>
                <Select value={imageQuality} onValueChange={onImageQualityChange}>
                  <SelectTrigger id="image-quality" className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {qualityOptions.map((option) => (
                      <SelectItem key={option.value} value={option.value}>
                        {option.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="image-count">生成数量</Label>
                <Input
                  id="image-count"
                  type="number"
                  min={1}
                  max={100}
                  step={1}
                  value={imageCount}
                  onChange={(event) => onImageCountChange(event.target.value)}
                />
              </div>
            </div>
            <fieldset className="space-y-3">
              <legend className="text-sm font-medium">比例与分辨率</legend>
              <div className="grid grid-cols-3 gap-2 sm:grid-cols-4">
                {aspectOptions.map((option) => {
                  const Icon = option.icon;
                  const selected =
                    imageRatio === option.ratio &&
                    imageTier === option.tier &&
                    imageWidth === option.width &&
                    imageHeight === option.height;
                  return (
                    <button
                      type="button"
                      key={option.label}
                      aria-pressed={selected}
                      className={cn(
                        "flex min-h-16 flex-col items-center justify-center gap-1 rounded-xl border px-2 py-3 text-xs transition hover:bg-muted",
                        selected ? "border-primary bg-muted font-semibold" : "border-border",
                      )}
                      onClick={() => {
                        onImageRatioChange(option.ratio);
                        onImageTierChange(option.tier);
                        onImageWidthChange(option.width);
                        onImageHeightChange(option.height);
                      }}
                    >
                      {Icon && <Icon className="size-4" />}
                      <span>{option.label === "auto" ? "自动" : option.label}</span>
                    </button>
                  );
                })}
              </div>
            </fieldset>
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="image-width">宽度（px）</Label>
                <Input
                  id="image-width"
                  type="number"
                  min={1}
                  max={isCodex ? undefined : 4096}
                  disabled={imageRatio === "auto"}
                  value={imageWidth}
                  onChange={(event) => onImageWidthChange(event.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="image-height">高度（px）</Label>
                <Input
                  id="image-height"
                  type="number"
                  min={1}
                  max={isCodex ? undefined : 4096}
                  disabled={imageRatio === "auto"}
                  value={imageHeight}
                  onChange={(event) => onImageHeightChange(event.target.value)}
                />
              </div>
            </div>
            {!valid && (
              <p className="text-sm text-destructive" role="alert">
                请输入有效尺寸和 1–100 之间的整数数量。
              </p>
            )}
            <DialogFooter>
              <Button type="button" onClick={() => setSettingsOpen(false)}>
                完成
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </div>
  );
}
