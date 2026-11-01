import { test, expect } from "@playwright/test";
import { mockApi, signIn, registerConfig } from "./fixtures";

for (const width of [1440, 390]) {
  test(`registration explains capacity before starting and preserves edits at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    const api = await mockApi(page);
    const current = { ...registerConfig, mode: "available", target_available: 10, threads: 10 };
    await page.route("**/api/register", (route) => route.fulfill({ json: { register: current } }));
    await page.route("**/api/register/events", (route) =>
      route.fulfill({
        contentType: "text/event-stream",
        body: "data: " + JSON.stringify(current) + "\n\n",
      }),
    );
    await page.route("**/api/proxies/platform", (route) =>
      route.fulfill({
        json: {
          status: "ready",
          source: "proxy-control",
          valid_until: new Date(Date.now() + 60000).toISOString(),
          capacity: { max_concurrency: 40, observed_remaining_connections: 40, eligible_nodes: 1 },
          bindings: { enabled: true, strict: true, limit: 5, remaining: 5, used: 0, groups: 5 },
          nodes: [],
        },
      }),
    );
    await signIn(page);
    await page.goto("/register/");
    const readiness = page.getByTestId("registration-readiness");
    await expect(readiness).toContainText("差 5 个名额");
    await expect(page.getByRole("button", { name: "开始任务", exact: true })).toBeDisabled();
    await expect(page.getByLabel("代理会话租期（秒）")).not.toBeVisible();
    await page.getByRole("button", { name: "将目标调整为 5 个", exact: true }).click();
    await expect(page.getByLabel("目标账号数", { exact: true })).toHaveValue("5");
    await expect(page.getByLabel("同时执行数", { exact: true })).toHaveValue("5");
    await expect(readiness).toContainText("准备就绪");
    await expect(page.getByRole("button", { name: "保存并开始", exact: true })).toBeEnabled();
    await page.getByTestId("shared-proxy-status").getByRole("button").click();
    await expect(page.getByLabel("目标账号数", { exact: true })).toHaveValue("5");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
      true,
    );
    await page.screenshot({
      path: `../artifacts/frontend/registration-workflow-${width}.png`,
      fullPage: true,
    });
    expect(api.writes).toEqual([]);
  });
}

test("background registration events update progress without overwriting the unsaved target", async ({
  page,
}) => {
  await mockApi(page);
  await page.route(/\/api\/register\/events(?:\?.*)?$/, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1800));
    await route.fulfill({
      contentType: "text/event-stream",
      body:
        "data: " +
        JSON.stringify({ ...registerConfig, stats: { ...registerConfig.stats, success: 2 } }) +
        "\n\n",
    });
  });
  await signIn(page);
  await page.goto("/register/");
  await page.getByLabel("新增账号数", { exact: true }).fill("3");
  await expect(page.getByText("2 / 0%", { exact: true })).toBeVisible();
  await expect(page.getByLabel("新增账号数", { exact: true })).toHaveValue("3");
});
