import { LoaderCircle } from "lucide-react";
export default function Loading() {
  return (
    <div className="state-panel" role="status" aria-live="polite">
      <LoaderCircle className="size-6 animate-spin text-primary" aria-hidden="true" />
      <strong>正在加载工作空间</strong>
      <p>页面准备就绪后会自动显示。</p>
      <div className="mt-4 grid w-full max-w-lg grid-cols-3 gap-3" aria-hidden="true">
        {[0, 1, 2].map((index) => (
          <div key={index} className="h-20 animate-pulse rounded-xl bg-muted" />
        ))}
      </div>
    </div>
  );
}
