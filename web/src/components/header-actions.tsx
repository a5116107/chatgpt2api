"use client";

import { ThemeToggle } from "@/components/theme-toggle";
import { VersionReleaseDialog } from "@/components/version-release-dialog";
import Image from "next/image";
import { cn } from "@/lib/utils";

export function HeaderActions({
  className,
  showGithubText = true,
}: {
  className?: string;
  showGithubText?: boolean;
}) {
  return (
    <div className={cn("flex items-center gap-2 sm:gap-3", className)}>
      <ThemeToggle />
      <a
        href="https://github.com/basketikun/chatgpt2api"
        target="_blank"
        rel="noreferrer"
        className="inline-flex h-8 items-center justify-center gap-1.5 text-sm text-muted-foreground transition hover:text-foreground  "
        aria-label="GitHub repository"
      >
        <Image src="/github.svg" alt="" width={16} height={16} className="size-4 dark:invert" />
        {showGithubText ? <span className="hidden sm:inline">GitHub</span> : null}
      </a>
      <VersionReleaseDialog />
    </div>
  );
}
