export type ProgressState = { done: boolean; error?: string | null };

function pause(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    signal.throwIfAborted();
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

export async function pollProgress<T extends ProgressState>(
  read: (signal: AbortSignal) => Promise<T>,
  onUpdate: (value: T) => void,
  signal: AbortSignal,
  { intervalMs = 500, timeoutMs = 30 * 60 * 1000 } = {},
): Promise<T> {
  const deadline = Date.now() + timeoutMs;
  while (true) {
    signal.throwIfAborted();
    const value = await read(signal).catch((error: unknown) => {
      signal.throwIfAborted();
      throw error;
    });
    signal.throwIfAborted();
    if (value.error) throw new Error(value.error);
    onUpdate(value);
    if (value.done) return value;
    if (Date.now() >= deadline)
      throw new Error("进度等待已超时，后台任务未被取消，请刷新查看最新结果。");
    await pause(intervalMs, signal);
  }
}
