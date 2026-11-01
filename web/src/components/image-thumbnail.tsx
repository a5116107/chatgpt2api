"use client";

import Image from "next/image";
import { useState } from "react";
import { ImageOff } from "lucide-react";
import { cn } from "@/lib/utils";

type ImageThumbnailProps = {
  src: string;
  thumbnailSrc?: string;
  alt?: string;
  className?: string;
  imageClassName?: string;
};
export function getImageThumbnailUrl(src: string) {
  const marker = "/images/";
  const index = src.indexOf(marker);
  return index < 0
    ? src
    : src.slice(0, index) + "/image-thumbnails/" + src.slice(index + marker.length);
}
export function ImageThumbnail({
  src,
  thumbnailSrc,
  alt = "",
  className,
  imageClassName,
}: ImageThumbnailProps) {
  const initialSrc = thumbnailSrc || getImageThumbnailUrl(src);
  const [failed, setFailed] = useState({ source: "", thumbnail: false, original: false });
  const state = failed.source === src ? failed : { source: src, thumbnail: false, original: false };
  const currentSrc = state.thumbnail ? src : initialSrc;
  return (
    <span className={cn("block overflow-hidden bg-muted", className)}>
      {state.original ? (
        <span
          role="img"
          aria-label={alt || "图片加载失败"}
          className="flex h-full min-h-20 w-full items-center justify-center text-muted-foreground"
        >
          <ImageOff className="size-6" />
        </span>
      ) : (
        <Image
          unoptimized
          width={640}
          height={640}
          src={currentSrc}
          alt={alt}
          className={cn("h-full w-full object-cover", imageClassName)}
          loading="lazy"
          onError={() => setFailed({ source: src, thumbnail: true, original: currentSrc === src })}
        />
      )}
    </span>
  );
}
