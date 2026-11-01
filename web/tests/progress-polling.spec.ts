import { expect, test } from "@playwright/test";
import { pollProgress } from "../src/lib/progress-polling";

test("progress polling is sequential and resolves only real completion", async () => {
  let calls = 0;
  let active = 0;
  let maxActive = 0;
  const updates: number[] = [];
  const result = await pollProgress(
    async () => {
      active++;
      maxActive = Math.max(maxActive, active);
      await new Promise((resolve) => setTimeout(resolve, 5));
      active--;
      calls++;
      return { done: calls === 3, count: calls };
    },
    (value) => updates.push(value.count),
    new AbortController().signal,
    { intervalMs: 1 },
  );
  expect(result.count).toBe(3);
  expect(updates).toEqual([1, 2, 3]);
  expect(maxActive).toBe(1);
});

test("progress polling propagates backend errors", async () => {
  await expect(
    pollProgress(
      async () => ({ done: true, error: "fixture failure" }),
      () => {},
      new AbortController().signal,
    ),
  ).rejects.toThrow("fixture failure");
});

test("progress polling cancels pending delay without another request", async () => {
  const controller = new AbortController();
  let calls = 0;
  const pending = pollProgress(
    async () => {
      calls++;
      return { done: false };
    },
    () => {
      setTimeout(() => controller.abort(), 5);
    },
    controller.signal,
    { intervalMs: 1000 },
  );
  await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  expect(calls).toBe(1);
});

test("cancelled progress responses never update a departed view", async () => {
  const controller = new AbortController();
  let updated = false;
  await expect(
    pollProgress(
      async () => {
        controller.abort();
        return { done: true };
      },
      () => {
        updated = true;
      },
      controller.signal,
    ),
  ).rejects.toMatchObject({ name: "AbortError" });
  expect(updated).toBe(false);
});

test("progress timeout reports background state without inventing success", async () => {
  await expect(
    pollProgress(
      async () => ({ done: false }),
      () => {},
      new AbortController().signal,
      { timeoutMs: 0 },
    ),
  ).rejects.toThrow("后台任务未被取消");
});
