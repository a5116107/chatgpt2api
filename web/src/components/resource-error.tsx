"use client";
import { useTransition } from "react";
import { CircleAlert, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
export function ResourceError({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => Promise<void>;
}) {
  const [pending, startTransition] = useTransition();
  return (
    <div className="state-panel" role="alert">
      <CircleAlert className="size-7 text-destructive" />
      <strong>这次加载没有完成</strong>
      <p>{message}</p>
      <Button
        disabled={pending}
        onClick={() =>
          startTransition(async () => {
            await onRetry();
          })
        }
      >
        <RefreshCw className={pending ? "size-4 animate-spin" : "size-4"} />
        {pending ? "正在重试…" : "重新加载"}
      </Button>
    </div>
  );
}
