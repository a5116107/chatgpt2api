import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mockApi, signIn } from "./fixtures";

const taskId = "fixture-video-1";
const contentPath = "/v1/videos/" + taskId + "/content";
let video: Buffer;

test.beforeAll(async ({ browser }) => {
  const page = await browser.newPage();
  try {
    const encoded = await page.evaluate(async () => {
      const canvas = document.createElement("canvas");
      canvas.width = 160;
      canvas.height = 90;
      const context = canvas.getContext("2d")!;
      context.fillStyle = "#163f33";
      context.fillRect(0, 0, canvas.width, canvas.height);
      const stream = canvas.captureStream(10);
      const recorder = new MediaRecorder(stream, { mimeType: "video/webm;codecs=vp8" });
      const chunks: BlobPart[] = [];
      const finished = new Promise<Blob>((resolve) => {
        recorder.ondataavailable = (event) => chunks.push(event.data);
        recorder.onstop = () => resolve(new Blob(chunks, { type: "video/webm" }));
      });
      let frame = 0;
      const redraw = setInterval(() => {
        context.fillStyle = frame++ % 2 ? "#163f33" : "#35745f";
        context.fillRect(0, 0, canvas.width, canvas.height);
      }, 50);
      try {
        recorder.start();
        await new Promise((resolve) => setTimeout(resolve, 600));
        recorder.stop();
        const bytes = new Uint8Array(await (await finished).arrayBuffer());
        return btoa(String.fromCharCode(...bytes));
      } finally {
        clearInterval(redraw);
        stream.getTracks().forEach((track) => track.stop());
      }
    });
    video = Buffer.from(encoded, "base64");
    expect(video.length).toBeGreaterThan(50);
  } finally {
    await page.close();
  }
});

type MediaResources = { created: string[]; revoked: string[] };
async function completedVideo(page: Page) {
  const api = await mockApi(page);
  await page.addInitScript(() => {
    const resources = { created: [] as string[], revoked: [] as string[] };
    Object.assign(window, { mediaResources: resources });
    const create = URL.createObjectURL.bind(URL);
    const revoke = URL.revokeObjectURL.bind(URL);
    URL.createObjectURL = (value) => {
      const url = create(value);
      resources.created.push(url);
      return url;
    };
    URL.revokeObjectURL = (url) => {
      resources.revoked.push(url);
      revoke(url);
    };
  });
  await page.route(/\/v1\/videos(?:\?.*)?$/, (route) =>
    route.fulfill({
      json: {
        items: [
          { id: taskId, status: "success", prompt: "森林中的晨光", model: "sora", seconds: 5 },
        ],
        missing_ids: [],
      },
    }),
  );
  await signIn(page);
  await page.goto("/video/");
  await expect(page.getByRole("button", { name: "加载视频预览", exact: true })).toBeVisible();
  return api;
}
async function resources(page: Page) {
  return page.evaluate(
    () => (window as unknown as { mediaResources: MediaResources }).mediaResources,
  );
}

test("video is authenticated on demand, playable, downloadable and released", async ({
  page,
}, testInfo) => {
  const requests: Array<{ authorization?: string; url: string }> = [];
  const api = await completedVideo(page);
  await page.route("**" + contentPath, async (route) => {
    requests.push({
      authorization: route.request().headers().authorization,
      url: route.request().url(),
    });
    await route.fulfill({ body: video, contentType: "video/webm" });
  });
  expect(requests).toEqual([]);
  await page.getByRole("button", { name: "加载视频预览", exact: true }).click();
  const preview = page.getByLabel("任务视频预览", { exact: true });
  await expect(preview).toHaveAttribute("src", /^blob:/);
  await expect
    .poll(() => preview.evaluate((node) => (node as HTMLVideoElement).readyState))
    .toBeGreaterThanOrEqual(2);
  await preview.evaluate((node) => (node as HTMLVideoElement).play());
  await expect
    .poll(() => preview.evaluate((node) => (node as HTMLVideoElement).currentTime))
    .toBeGreaterThan(0);
  expect(requests).toHaveLength(1);
  expect(requests[0]).toEqual({
    authorization: "Bearer fixture-admin-key",
    url: "http://127.0.0.1:3097" + contentPath,
  });
  const downloadEvent = page.waitForEvent("download");
  await page.getByRole("link", { name: "下载视频", exact: true }).click();
  expect((await downloadEvent).suggestedFilename()).toBe("video-" + taskId + ".webm");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "切换为深色主题" }).click();
  const axe = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(axe.violations.map((item) => item.id)).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("video-mobile-dark.png"), fullPage: true });
  await page.getByRole("button", { name: "释放预览", exact: true }).click();
  await expect(preview).toHaveCount(0);
  let state = await resources(page);
  expect(state.revoked).toEqual(state.created);
  await page.getByRole("button", { name: "加载视频预览", exact: true }).click();
  await expect(preview).toHaveAttribute("src", /^blob:/);
  await page.getByRole("button", { name: "打开导航" }).click();
  await page.getByRole("dialog").getByRole("link", { name: "图像创作", exact: true }).click();
  await expect(page).toHaveURL(/image/);
  state = await resources(page);
  expect(state.created).toHaveLength(2);
  expect(state.revoked).toEqual(state.created);
  expect(api.writes).toEqual([]);
  expect(api.unexpected).toEqual([]);
});

for (const status of [401, 404])
  test("video error " + status + " keeps the session and supports retry", async ({ page }) => {
    const api = await completedVideo(page);
    let attempts = 0;
    await page.route("**" + contentPath, (route) => {
      attempts++;
      return attempts === 1
        ? route.fulfill({ status, json: { detail: "unavailable media fixture" } })
        : route.fulfill({ body: video, contentType: "video/webm" });
    });
    await page.getByRole("button", { name: "加载视频预览", exact: true }).click();
    await expect(
      page.getByRole("group", { name: "视频预览", exact: true }).getByRole("alert"),
    ).toContainText(status === 401 ? "权限不足" : "视频文件不存在");
    await expect(page).toHaveURL(/video/);
    await page.getByRole("button", { name: "重试加载视频", exact: true }).click();
    await expect(page.getByLabel("任务视频预览", { exact: true })).toHaveAttribute("src", /^blob:/);
    await expect(
      page.getByRole("group", { name: "视频预览", exact: true }).getByRole("alert"),
    ).toHaveCount(0);
    expect(attempts).toBe(2);
    expect(api.writes).toEqual([]);
  });

for (const invalid of ["empty", "html"])
  test("video rejects " + invalid + " rather than creating a broken player", async ({ page }) => {
    const api = await completedVideo(page);
    await page.route("**" + contentPath, (route) =>
      route.fulfill({
        body: invalid === "empty" ? Buffer.alloc(0) : Buffer.from("<html>Error</html>"),
        contentType: invalid === "empty" ? "video/mp4" : "text/html",
      }),
    );
    await page.getByRole("button", { name: "加载视频预览", exact: true }).click();
    await expect(
      page.getByRole("group", { name: "视频预览", exact: true }).getByRole("alert"),
    ).toContainText(invalid === "empty" ? "视频文件为空" : "未返回可播放的视频");
    await expect(page.getByLabel("任务视频预览", { exact: true })).toHaveCount(0);
    expect((await resources(page)).created).toEqual([]);
    expect(api.writes).toEqual([]);
  });

test("corrupt video files retain a visible decode error, download and release actions", async ({
  page,
}) => {
  const api = await completedVideo(page);
  await page.route("**" + contentPath, (route) =>
    route.fulfill({ body: Buffer.from("invalid historical mp4"), contentType: "video/mp4" }),
  );
  await page.getByRole("button", { name: "加载视频预览", exact: true }).click();
  const group = page.getByRole("group", { name: "视频预览", exact: true });
  await expect(group.getByRole("alert")).toContainText("当前浏览器未能播放此格式");
  await expect(group.getByRole("link", { name: "下载视频", exact: true })).toBeVisible();
  await group.getByRole("button", { name: "释放预览", exact: true }).click();
  await expect(group.getByRole("alert")).toHaveCount(0);
  expect((await resources(page)).revoked).toEqual((await resources(page)).created);
  expect(api.writes).toEqual([]);
});

for (const action of ["cancel", "navigate"])
  test("video aborts in-flight content on " + action, async ({ page }) => {
    const api = await completedVideo(page);
    let finish: () => void = () => {};
    const pending = new Promise<void>((resolve) => {
      finish = resolve;
    });
    let requests = 0;
    const failures: string[] = [];
    page.on("requestfailed", (request) => {
      if (request.url().endsWith(contentPath)) failures.push(request.failure()?.errorText || "");
    });
    await page.route("**" + contentPath, async (route) => {
      requests++;
      if (requests === 1) await pending;
      await route.fulfill({ body: video, contentType: "video/webm" });
    });
    await page.getByRole("button", { name: "加载视频预览", exact: true }).click();
    await expect(page.getByText("正在加载视频…", { exact: true })).toBeVisible();
    if (action === "cancel")
      await page.getByRole("button", { name: "取消加载", exact: true }).click();
    else await page.locator('.workspace-sidebar nav a[href="/image/"]').click();
    await expect.poll(() => failures.length).toBe(1);
    finish();
    expect((await resources(page)).created).toEqual([]);
    if (action === "cancel") {
      await page.getByRole("button", { name: "加载视频预览", exact: true }).click();
      await expect(page.getByLabel("任务视频预览", { exact: true })).toHaveAttribute(
        "src",
        /^blob:/,
      );
      expect(requests).toBe(2);
    } else await expect(page).toHaveURL(/image/);
    expect(api.writes).toEqual([]);
  });
