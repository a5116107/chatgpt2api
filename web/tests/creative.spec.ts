import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mockApi, signIn, navigateTo } from "./fixtures";

const conversation = {
  id: "design-review",
  title: "创作工作台设计讨论",
  model: "gpt-5",
  message_count: 2,
  updated_at: "2026-10-31T08:00:00Z",
  messages: [
    { id: "u1", role: "user", content: "帮我梳理一个清晰、易用的创作工作台。" },
    {
      id: "a1",
      role: "assistant",
      content:
        '## 让创作回到中心\n\n保留一个历史侧栏，把注意力留给内容。\n\n- 对话支持 **流式回复**\n- 生成结果可继续编辑与下载\n- 在手机上也能随时发送\n\n| 区域 | 交互原则 |\n| --- | --- |\n| 消息 | 易读、可复制 |\n| 输入 | 始终可见 |\n\n~~~ts\nconst focus = "your next idea";\n~~~',
    },
  ],
};
async function chatApi(page: Page) {
  await mockApi(page);
  let active = structuredClone(conversation);
  await page.route("**/api/conversations**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/export")) {
      expect(route.request().headers().authorization).toBe("Bearer fixture-admin-key");
      await route.fulfill({ contentType: "text/markdown", body: "# Conversation" });
    } else if (path.endsWith("design-review")) await route.fulfill({ json: { item: active } });
    else await route.fulfill({ json: { items: [active] } });
  });
  return {
    setActive: (value: typeof conversation) => {
      active = value;
    },
  };
}
async function openConversation(page: Page) {
  await signIn(page);
  await navigateTo(page, "conversations");
  await page
    .locator(".studio-desktop-history")
    .getByRole("button", { name: /创作工作台设计讨论/ })
    .click();
  await expect(page.locator(".chat-markdown h2")).toHaveText("让创作回到中心");
}
async function expectComposerInViewport(page: Page, label: string) {
  const bounds = await page.getByLabel(label, { exact: true }).boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds!.y).toBeGreaterThanOrEqual(0);
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual((page.viewportSize()?.height || 0) - 12);
  expect(await page.evaluate(() => document.documentElement.scrollHeight <= innerHeight + 1)).toBe(
    true,
  );
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
}

test("chat renders Markdown, exports with authentication, preserves drafts and collapses history", async ({
  page,
}) => {
  await chatApi(page);
  await openConversation(page);
  await expect(page.locator(".chat-markdown table")).toBeVisible();
  await expect(page.locator(".workspace-sidebar")).toBeHidden();
  await page.getByLabel("对话内容", { exact: true }).fill("尚未发送的草稿");
  await page.getByRole("button", { name: "新建对话", exact: true }).click();
  await expect(page.getByLabel("对话内容", { exact: true })).toHaveValue("");
  await page
    .locator(".studio-desktop-history")
    .getByRole("button", { name: /创作工作台设计讨论/ })
    .click();
  await expect(page.getByLabel("对话内容", { exact: true })).toHaveValue("尚未发送的草稿");
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出对话" }).click();
  expect((await download).suggestedFilename()).toMatch(/\.md$/);
  await page.getByRole("button", { name: "收起对话历史" }).click();
  await expect(page.locator(".studio-desktop-history")).toBeHidden();
  await expectComposerInViewport(page, "对话内容");
  await page.screenshot({ path: "../artifacts/frontend/chat-desktop.png" });
});

test("chat consumes split UTF-8 SSE frames, retains multi-turn context and prevents duplicate sends", async ({
  page,
}) => {
  const api = await chatApi(page);
  await openConversation(page);
  api.setActive({
    ...conversation,
    message_count: 4,
    messages: [
      ...conversation.messages,
      { id: "u2", role: "user", content: "继续" },
      { id: "a2", role: "assistant", content: "新的流式回复" },
    ],
  });
  await page.evaluate(() => {
    const native = window.fetch;
    const calls: unknown[] = [];
    Object.assign(window, { chatCalls: calls });
    window.fetch = async (input, init) => {
      if (!String(input).endsWith("/v1/chat/completions")) return native(input, init);
      calls.push({ body: JSON.parse(String(init?.body)), headers: init?.headers });
      const encoder = new TextEncoder();
      const bytes = encoder.encode(
        'data: {"choices":[{"delta":{"content":"新的"}}]}\r\n\r\ndata: {"choices":[{"delta":{"content":"流式回复"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n',
      );
      let offset = 0;
      return new Response(
        new ReadableStream({
          async pull(controller) {
            await new Promise((resolve) => setTimeout(resolve, 2));
            if (offset < bytes.length) controller.enqueue(bytes.slice(offset, ++offset));
            else controller.close();
          },
        }),
        { headers: { "content-type": "text/event-stream" } },
      );
    };
  });
  await page.getByLabel("对话内容", { exact: true }).fill("继续");
  await page.getByRole("button", { name: "发送消息", exact: true }).dblclick();
  await expect(page.getByText("新的流式回复", { exact: true })).toBeVisible();
  const calls = await page.evaluate(
    () =>
      (
        window as unknown as {
          chatCalls: Array<{
            body: { messages: unknown[]; stream: boolean };
            headers: { Authorization: string };
          }>;
        }
      ).chatCalls,
  );
  expect(calls).toHaveLength(1);
  expect(calls[0].body.stream).toBe(true);
  expect(calls[0].body.messages).toHaveLength(3);
  expect(calls[0].headers.Authorization).toBe("Bearer fixture-admin-key");
  await expect(page.getByRole("button", { name: "停止接收回复" })).toHaveCount(0);
});

test("chat reports upstream quota failures and never fakes a reply", async ({ page }) => {
  await chatApi(page);
  await openConversation(page);
  await page.route("**/v1/chat/completions", (route) =>
    route.fulfill({
      status: 429,
      json: { detail: { error: { message: "no available text account" } } },
    }),
  );
  await page.getByLabel("对话内容", { exact: true }).fill("保留这个问题");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  await expect(page.locator(".creative-error[role=alert]")).toContainText("暂无可用的对话账号");
  await expect(page.getByLabel("对话内容", { exact: true })).toHaveValue("保留这个问题");
  await expect(page.locator(".chat-markdown")).toHaveCount(1);
  await expectComposerInViewport(page, "对话内容");
});

test("chat supports stopping a pending response and guards IME Enter", async ({ page }) => {
  await chatApi(page);
  await openConversation(page);
  await page.evaluate(() => {
    const native = window.fetch;
    window.fetch = (input, init) =>
      String(input).endsWith("/v1/chat/completions")
        ? new Promise((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () =>
              reject(new DOMException("Stopped", "AbortError")),
            );
          })
        : native(input, init);
  });
  await page.getByLabel("对话内容", { exact: true }).fill("测试停止");
  await page
    .getByLabel("对话内容", { exact: true })
    .dispatchEvent("keydown", { key: "Enter", isComposing: true });
  await expect(page.getByRole("button", { name: "发送消息", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  await expect(page.getByRole("button", { name: "停止接收回复" })).toBeVisible();
  await page.getByRole("button", { name: "停止接收回复" }).click();
  await expect(page.getByText(/已停止接收回复/)).toBeVisible();
  await expect(page.getByRole("button", { name: "发送消息", exact: true })).toBeVisible();
});

async function seedImages(page: Page, mode = "generate") {
  await mockApi(page);
  await signIn(page);
  await page.route("**/fixture-art.svg", (route) =>
    route.fulfill({
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="960" height="720" viewBox="0 0 960 720"><rect width="960" height="720" fill="#d9e4df"/><circle cx="710" cy="180" r="74" fill="#fff3d9"/><path d="M0 540L310 180L640 540Z" fill="#6d8e83"/><path d="M280 600L640 270L960 600Z" fill="#a5b7ad"/><path d="M0 540Q480 450 960 570V720H0Z" fill="#3f685f"/><path d="M300 720Q440 570 640 530Q450 650 500 720Z" fill="#bdd5ce"/></svg>',
    }),
  );
  await page.evaluate(async (mode) => {
    const open = (version?: number) =>
      new Promise<IDBDatabase>((resolve, reject) => {
        const request = indexedDB.open("chatgpt2api", version);
        request.onupgradeneeded = () => {
          if (!request.result.objectStoreNames.contains("image_conversations"))
            request.result.createObjectStore("image_conversations");
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
    let db = await open();
    if (!db.objectStoreNames.contains("image_conversations")) {
      const version = db.version + 1;
      db.close();
      db = await open(version);
    }
    const value = [
      {
        id: "art-history",
        title: "山野里的宁静时刻",
        createdAt: "2026-10-31T08:00:00Z",
        updatedAt: "2026-10-31T08:01:00Z",
        turns: [
          {
            id: "turn-one",
            prompt: "一片宁静的山野，温柔的日光，平面插画风格。",
            model: "gpt-image-1",
            mode,
            referenceImages:
              mode === "edit"
                ? [
                    {
                      name: "original-reference.png",
                      type: "image/png",
                      dataUrl:
                        "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==",
                    },
                  ]
                : [],
            count: 2,
            size: "1536x1024",
            ratio: "3:2",
            tier: "1.5k",
            quality: "auto",
            createdAt: "2026-10-31T08:00:00Z",
            status: "error",
            images: [
              {
                id: "success-art",
                status: "success",
                url: "/fixture-art.svg",
                width: 1536,
                height: 1024,
                durationMs: 3000,
              },
              {
                id: "failed-art",
                status: "error",
                error:
                  "bootstrap failed status=403 <html><style>body { display: none }</style><body>Forbidden</body></html>",
              },
            ],
          },
        ],
      },
    ];
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction("image_conversations", "readwrite");
      tx.objectStore("image_conversations").put(value, "items");
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
    db.close();
    localStorage.setItem("chatgpt2api:image_active_conversation_id", "art-history");
  }, mode);
  await page.goto("/image/");
  await expect(page.getByLabel("第 1 轮生成", { exact: true })).toBeVisible();
}

for (const width of [390, 1440])
  test(
    "image studio populated history, settings and bounded errors at " + width,
    async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      const errors: string[] = [];
      page.on("pageerror", (e) => errors.push(e.message));
      await seedImages(page);
      await expect(
        page.getByText("上游服务拒绝了请求（403）。请检查账号或代理状态后重试。", { exact: true }),
      ).toBeVisible();
      await expect(page.getByText(/bootstrap failed/)).toBeHidden();
      await expect(page.getByRole("button", { name: "下载图片", exact: true })).toBeVisible();
      await expectComposerInViewport(page, "图片提示词");
      await page.getByRole("button", { name: "生成设置", exact: true }).click();
      await expect(page.getByRole("dialog")).toBeVisible();
      await page.getByLabel("生成数量", { exact: true }).fill("3");
      await page.getByRole("button", { name: "完成", exact: true }).click();
      await expect(page.getByRole("button", { name: "生成设置", exact: true })).toContainText(
        "3 张",
      );
      if (width < 1024) {
        await page.getByRole("button", { name: "打开图像历史" }).click();
        await page.getByRole("dialog").getByLabel("搜索图像历史").fill("山野");
        await expect(
          page.getByRole("dialog").getByRole("button", { name: /山野里的宁静时刻/ }),
        ).toBeVisible();
        await page.keyboard.press("Escape");
      }
      await page.getByRole("button", { name: "切换为深色主题" }).click();
      await expect(page.locator("html")).toHaveClass(/dark/);
      const accessibility = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
        .analyze();
      expect(
        accessibility.violations.map((item) => ({
          id: item.id,
          nodes: item.nodes.map((node) => node.target),
        })),
      ).toEqual([]);
      await page.screenshot({ path: "../artifacts/frontend/image-studio-" + width + ".png" });
      expect(errors).toEqual([]);
    },
  );

test("retrying an old failed image creates one fresh task and preserves successful siblings", async ({
  page,
}) => {
  await seedImages(page);
  const submitted: Record<string, unknown>[] = [];
  await page.route("**/api/image-tasks/generations", async (route) => {
    const body = route.request().postDataJSON();
    submitted.push(body);
    await route.fulfill({
      json: {
        id: body.client_task_id,
        status: "success",
        model: body.model,
        data: [{ url: "/fixture-art.svg" }],
        account_hash: "token:fixture-new",
      },
    });
  });
  await page.getByRole("button", { name: "重新生成这一张", exact: true }).click();
  await expect(page.getByRole("button", { name: "下载图片", exact: true })).toHaveCount(2);
  expect(submitted).toHaveLength(1);
  expect(submitted[0]).toMatchObject({
    prompt: "一片宁静的山野，温柔的日光，平面插画风格。",
    model: "gpt-image-1",
    size: "1536x1024",
    quality: "auto",
  });
  expect(submitted[0].client_task_id).not.toBe("failed-art");
  await page.reload();
  await expect(page.getByRole("button", { name: "下载图片", exact: true })).toHaveCount(2);
  expect(submitted).toHaveLength(1);
});

test("a rejected image submission does not fail an accepted sibling", async ({ page }) => {
  await seedImages(page);
  let submits = 0;
  await page.route("**/api/image-tasks/generations", async (route) => {
    const body = route.request().postDataJSON();
    const index = submits++;
    if (index === 0) {
      await route.fulfill({ status: 503, json: { detail: "fixture submission unavailable" } });
    } else {
      await new Promise((resolve) => setTimeout(resolve, 300));
      await route.fulfill({
        json: { id: body.client_task_id, status: "success", data: [{ url: "/fixture-art.svg" }] },
      });
    }
  });
  await page.getByRole("button", { name: "全部重新生成", exact: true }).click();
  await expect(
    page
      .getByLabel("第 2 轮生成", { exact: true })
      .getByRole("button", { name: "下载图片", exact: true }),
  ).toHaveCount(1);
  expect(submits).toBe(2);
  await expect(
    page
      .getByLabel("第 1 轮生成", { exact: true })
      .getByRole("button", { name: "下载图片", exact: true }),
  ).toHaveCount(1);
});

test("retrying a failed edit preserves its reference file and original parameters", async ({
  page,
}) => {
  await seedImages(page, "edit");
  let submitted = "";
  await page.route("**/api/image-tasks/edits", async (route) => {
    submitted = route.request().postData() || "";
    const id = submitted.match(/name="client_task_id"\r\n\r\n([^\r]+)/)?.[1];
    expect(id).toBeTruthy();
    expect(id).not.toBe("failed-art");
    await route.fulfill({
      json: { id, status: "success", mode: "edit", data: [{ url: "/fixture-art.svg" }] },
    });
  });
  await page.getByRole("button", { name: "重新生成这一张", exact: true }).click();
  await expect(page.getByRole("button", { name: "下载图片", exact: true })).toHaveCount(2);
  expect(submitted).toContain('filename="original-reference.png"');
  expect(submitted).toContain("1536x1024");
  expect(submitted).toContain("gpt-image-1");
});

for (const width of [360, 768])
  test("chat mobile long content keeps composer visible at " + width, async ({ page }) => {
    await page.setViewportSize({ width, height: 740 });
    await chatApi(page);
    await signIn(page);
    await navigateTo(page, "conversations");
    await page.getByRole("button", { name: "打开对话历史" }).click();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: /创作工作台设计讨论/ })
      .click();
    await expect(page.locator(".chat-markdown table")).toBeVisible();
    await page.getByLabel("对话内容", { exact: true }).fill("这是较长的输入内容。".repeat(80));
    await expectComposerInViewport(page, "对话内容");
    const result = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
      .analyze();
    expect(result.violations.map((item) => item.id)).toEqual([]);
    await page.screenshot({ path: "../artifacts/frontend/chat-mobile-" + width + ".png" });
  });

test("partial stream failure preserves received text and a retryable prompt", async ({ page }) => {
  await chatApi(page);
  await openConversation(page);
  await page.route("**/v1/chat/completions", (route) =>
    route.fulfill({
      contentType: "text/event-stream",
      body: 'data: {"choices":[{"delta":{"content":"已接收的一部分"}}]}\n\n',
    }),
  );
  await page.getByLabel("对话内容", { exact: true }).fill("继续分析");
  await page.getByRole("button", { name: "发送消息", exact: true }).click();
  await expect(page.locator(".creative-error[role=alert]")).toContainText("连接提前断开");
  await expect(page.getByText("已接收的一部分", { exact: true })).toBeVisible();
  await expect(page.getByLabel("对话内容", { exact: true })).toHaveValue("继续分析");
});

test("image reference editing, diagnostics and small-height viewport retain composer", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await seedImages(page);
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await expect(page.getByRole("button", { name: /预览参考图/ })).toBeVisible();
  await page.getByRole("button", { name: /移除参考图/ }).click();
  await expect(page.getByRole("button", { name: /预览参考图/ })).toHaveCount(0);
  await page.locator(".image-failure-card summary").click();
  await expect(page.locator(".image-failure-card pre")).toContainText("失败阶段：bootstrap");
  await expect(page.locator(".image-failure-card pre")).toContainText("HTTP 状态：403");
  await expect(page.locator(".image-failure-card pre")).not.toContainText("<html>");
  await expect(page.locator(".image-failure-card pre")).not.toContainText("display: none");
  expect(
    await page
      .locator(".image-failure-card pre")
      .evaluate((node) => node.getBoundingClientRect().height),
  ).toBeLessThanOrEqual(140);
  await page.setViewportSize({ width: 390, height: 480 });
  await page.getByLabel("图片提示词", { exact: true }).fill("新的画面".repeat(200));
  await expectComposerInViewport(page, "图片提示词");
});
