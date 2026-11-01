import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mockApi, signIn, navigateTo, registerConfig } from "./fixtures";

test("blocked registration displays its reason and preserves historical counters", async ({
  page,
}) => {
  await mockApi(page);
  const blocked = {
    ...registerConfig,
    stats: {
      ...registerConfig.stats,
      success: 5,
      fail: 66891,
      done: 66896,
      start_blocked_reason: "preflight_failed",
      last_error: "account proxy pool capacity is exhausted",
    },
  };
  await page.route("**/api/register", (route) => route.fulfill({ json: { register: blocked } }));
  await page.route("**/api/register/events", (route) =>
    route.fulfill({
      contentType: "text/event-stream",
      body: "data: " + JSON.stringify(blocked) + "\n\n",
    }),
  );
  await signIn(page);
  await page.goto("/register/");
  await expect(page.getByTestId("registration-readiness")).toContainText("准备就绪");
  await page.getByText("上次任务说明", { exact: true }).click();
  await expect(page.getByText("66891", { exact: true })).toBeVisible();
  await expect(
    page.getByText("account proxy pool capacity is exhausted", { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "开始任务", exact: true })).toBeVisible();
});

const routes = [
  "accounts",
  "conversations",
  "image",
  "video",
  "image-manager",
  "runtime-profiles",
  "proxies",
  "register",
  "tasks",
  "risk",
  "logs",
  "debug",
  "settings",
];

test("shared proxy capacity separates bindings, connections and exits on both pages", async ({
  page,
}) => {
  const api = await mockApi(page);
  await page.route("**/api/proxies/platform", (route) =>
    route.fulfill({
      json: {
        status: "ready",
        source: "proxy-control",
        revision: 10,
        observed_at: new Date().toISOString(),
        valid_until: new Date(Date.now() + 60000).toISOString(),
        bindings: {
          unit: "profile_binding",
          enabled: true,
          strict: true,
          limit: 5,
          used: 1,
          remaining: 4,
          groups: 5,
        },
        capacity: {
          unit: "gateway_connection",
          max_concurrency: 40,
          observed_active_connections: 3,
          observed_remaining_connections: 37,
          eligible_nodes: 2,
          runtime_routes: 4,
          observed_unique_exits: 1,
          rate_limit_per_minute: 1200,
          live_gateways: 1,
        },
        nodes: [
          {
            id: "node-fixture",
            country: "US",
            geo_verified: false,
            health: "healthy",
            observed_at: new Date().toISOString(),
            exit_ip: "192.0.2.1",
          },
        ],
      },
    }),
  );
  await signIn(page);
  for (const route of ["proxies", "register"]) {
    await page.goto("/" + route + "/");
    const card = page.getByTestId("shared-proxy-status");
    await expect(card.getByText("4 个", { exact: true })).toBeVisible();
    await card.locator("summary").click();
    await expect(card).toContainText("4 / 5");
    await expect(card).toContainText("37 / 40");
    await expect(card.getByText("连接正常", { exact: true }).first()).toBeVisible();
    await expect(card.getByText("192.0.2.1", { exact: true })).toBeVisible();
    await expect(card.getByText("美国（待核实）", { exact: true })).toBeVisible();
  }
  expect(api.unexpected).toEqual([]);
});

test("stale shared status never displays remaining connections as available", async ({ page }) => {
  await mockApi(page);
  await page.route("**/api/proxies/platform", (route) =>
    route.fulfill({
      json: {
        status: "ready",
        source: "proxy-control",
        valid_until: "2000-01-01T00:00:00Z",
        nodes: [],
        capacity: { max_concurrency: 40, observed_remaining_connections: 37 },
        bindings: { limit: 5, remaining: 4, groups: 5 },
      },
    }),
  );
  await signIn(page);
  await page.goto("/proxies/");
  const card = page.getByTestId("shared-proxy-status");
  await expect(card.getByText("正在等待最新检查结果", { exact: true })).toBeVisible();
  await card.locator("summary").click();
  await expect(card).toContainText("— / 40");
  await expect(card).not.toContainText("37 / 40");
});

test("manual proxy feedback uses an opaque id rather than a masked credential", async ({
  page,
}) => {
  await mockApi(page);
  await page.route("**/api/proxies", (route) =>
    route.fulfill({
      json: {
        items: [
          {
            id: "px-fixture",
            proxy: "http://***:***@proxy.example.test:8080",
            status: "unknown",
            score: null,
            success: 0,
            fail: 0,
            http_403: 0,
            http_429: 0,
            timeout: 0,
          },
        ],
      },
    }),
  );
  let sent: unknown;
  await page.route("**/api/proxies/events", async (route) => {
    sent = route.request().postDataJSON();
    await route.fulfill({ json: { item: { id: "px-fixture" } } });
  });
  await signIn(page);
  await page.goto("/proxies/");
  await page.getByText("连接记录与维护", { exact: true }).click();
  await expect(page.getByText("尚无记录", { exact: true }).first()).toBeVisible();
  await page.getByText("手动记录结果", { exact: true }).click();
  await page.getByRole("button", { name: "成功", exact: true }).click();
  await expect.poll(() => sent).toEqual({ proxy_id: "px-fixture", code: "success" });
});
test("client navigation visits every route without malformed RSC or document reloads", async ({
  page,
}) => {
  const api = await mockApi(page);
  await signIn(page);
  await page.evaluate(() => Object.assign(window, { navigationDocumentMarker: "same-document" }));
  const malformed: string[] = [];
  page.on("response", async (response) => {
    const path = new URL(response.url()).pathname;
    if (path.endsWith(".txt") && /(?:__next\.|index\.txt)/.test(path)) {
      if (!response.ok() || !(response.headers()["content-type"] || "").includes("text/plain"))
        malformed.push(path);
    }
  });
  for (const route of routes) {
    await navigateTo(page, route);
    await expect(page).toHaveURL(new RegExp("/" + route + "/$"));
    await expect(
      page.locator(route === "debug" ? "main [role=tablist]" : "main h1").first(),
    ).toBeVisible();
    expect(
      await page.evaluate(
        () => (window as unknown as { navigationDocumentMarker?: string }).navigationDocumentMarker,
      ),
    ).toBe("same-document");
  }
  expect(malformed).toEqual([]);
  expect(api.unexpected).toEqual([]);
  expect(api.writes).toEqual([]);
});
for (const route of routes)
  test("admin page: " + route, async ({ page }) => {
    const failures: string[] = [];
    page.on("pageerror", (error) => failures.push(error.message));
    const api = await mockApi(page);
    await signIn(page);
    await page.goto("/" + route + "/");
    await expect(page.locator(".workspace-topbar")).toBeVisible();
    await expect(page.locator(".workspace-sidebar [aria-current=page]")).toHaveAttribute(
      "href",
      "/" + route + "/",
    );
    await expect(page.getByText("页面暂时未能加载", { exact: true })).toHaveCount(0);
    await page.waitForTimeout(400);
    expect(failures).toEqual([]);
    expect(api.unexpected).toEqual([]);
    expect(api.writes).toEqual([]);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
  });

test("leaving tasks aborts an unfinished list request", async ({ page }) => {
  const api = await mockApi(page);
  await signIn(page);
  let finish: () => void = () => {};
  const waiting = new Promise<void>((resolve) => {
    finish = resolve;
  });
  const aborted: string[] = [];
  page.on("requestfailed", (request) => {
    if (new URL(request.url()).pathname === "/api/tasks")
      aborted.push(request.failure()?.errorText || "");
  });
  await page.route("**/api/tasks?**", async (route) => {
    await waiting;
    await route.fulfill({ json: { items: [] } });
  });
  const request = page.waitForRequest(
    (request) => new URL(request.url()).pathname === "/api/tasks",
  );
  await page.goto("/tasks/");
  await request;
  await page.locator('.workspace-sidebar nav a[href="/accounts/"]').click();
  await expect(page).toHaveURL(/accounts/);
  await expect.poll(() => aborted.length).toBe(1);
  finish();
  expect(api.writes).toEqual([]);
});

test("login validation, visibility and rejected credentials", async ({ page }) => {
  const api = await mockApi(page);
  await page.goto("/login/");
  await page.getByRole("button", { name: "进入工作空间" }).click();
  await expect(page.locator("#login-error")).toHaveText("请输入访问密钥后再登录。");
  const key = page.getByLabel("访问密钥", { exact: true });
  await key.fill("invalid-fixture-key");
  await page.getByRole("button", { name: "显示密钥" }).click();
  await expect(key).toHaveAttribute("type", "text");
  await page.getByRole("button", { name: "隐藏密钥" }).click();
  await key.press("Enter");
  await expect(page.locator("#login-error")).toContainText("密钥无效");
  expect(api.writes).toEqual([]);
});

test("command search is keyboard accessible and supports an empty result", async ({ page }) => {
  await mockApi(page);
  await signIn(page);
  await page.keyboard.press("Control+k");
  const search = page.getByRole("combobox", { name: "搜索页面和功能" });
  await search.fill("不存在的功能");
  await expect(page.getByText("没有匹配的功能")).toBeVisible();
  await search.fill("邮箱");
  await search.press("Enter");
  await expect(page).toHaveURL(/register/);
  await page.keyboard.press("Control+k");
  await expect(search).toBeVisible();
  await search.press("Escape");
  await expect(search).toHaveCount(0);
});

test("user role has only creative destinations", async ({ page }) => {
  await mockApi(page);
  await signIn(page, "user");
  await expect(page.locator(".workspace-sidebar nav a")).toHaveCount(3);
  await expect(page.locator(".workspace-sidebar a[href='/settings']")).toHaveCount(0);
  await page.goto("/accounts/", { waitUntil: "commit" });
  await expect(page).toHaveURL(/image/);
});

test("failed data load exposes retry rather than fake empty statistics", async ({ page }) => {
  await mockApi(page, { failTasks: true });
  await signIn(page);
  await page.goto("/tasks/");
  await expect(page.locator(".state-panel[role=alert]")).toContainText("这次加载没有完成");
  await expect(page.getByRole("button", { name: "重新加载" })).toBeVisible();
});

test("account load failure has a working retry and no misleading zero dashboard", async ({
  page,
}) => {
  const options = { failAccounts: true };
  const api = await mockApi(page, options);
  await signIn(page);
  await expect(page.locator(".state-panel[role=alert]")).toContainText("账户服务暂时不可用");
  await expect(page.getByText("账户总数", { exact: true })).toHaveCount(0);
  options.failAccounts = false;
  await page.getByRole("button", { name: "重新加载", exact: true }).click();
  await expect(page.getByText("creator@example.test", { exact: true })).toBeVisible();
  expect(api.writes).toEqual([]);
});

for (const width of [360, 768, 1440])
  test("responsive navigation and theme at " + width, async ({ page }) => {
    await page.setViewportSize({ width, height: 1000 });
    await mockApi(page);
    await signIn(page);
    if (width < 1024) {
      await page.getByRole("button", { name: "打开导航" }).click();
      await page.getByRole("dialog").getByRole("link", { name: "任务中心" }).click();
      await expect(page).toHaveURL(/tasks/);
      await expect(page.getByRole("dialog")).toHaveCount(0);
    }
    await page.getByRole("button", { name: "切换为深色主题" }).click();
    await expect(page.locator("html")).toHaveClass(/dark/);
    await page.reload();
    await expect(page.locator("html")).toHaveClass(/dark/);
    expect(
      await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth),
    ).toBe(true);
  });

for (const route of ["login", ...routes])
  test("accessibility: " + route, async ({ page }) => {
    await mockApi(page);
    if (route !== "login") await signIn(page);
    await page.goto("/" + route + "/");
    await expect(
      page.locator(route === "login" ? ".login-form" : ".workspace-topbar"),
    ).toBeVisible();
    await page.waitForTimeout(300);
    const result = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
      .analyze();
    expect(
      result.violations.map((item) => ({
        id: item.id,
        impact: item.impact,
        nodes: item.nodes.map((node) => node.target),
      })),
    ).toEqual([]);
  });

test("account search and status filter preserve summary and clear selection", async ({ page }) => {
  const api = await mockApi(page);
  await signIn(page);
  await expect(page.getByText("creator@example.test", { exact: true })).toBeVisible();
  await page.getByPlaceholder("搜索邮箱").fill("studio");
  await expect(page.getByText("studio@example.test", { exact: true })).toBeVisible();
  await expect(page.getByText("creator@example.test", { exact: true })).toHaveCount(0);
  await page.getByPlaceholder("搜索邮箱").clear();
  await page.getByRole("combobox", { name: "筛选状态", exact: true }).click();
  await page.getByRole("option", { name: "异常", exact: true }).click();
  await expect(page.getByText("review@example.test", { exact: true })).toBeVisible();
  await expect(page.getByText("studio@example.test", { exact: true })).toHaveCount(0);
  expect(api.writes).toEqual([]);
});

test("destructive task action can be cancelled and restores focus", async ({ page }) => {
  const api = await mockApi(page);
  await signIn(page);
  await page.goto("/tasks/");
  const remove = page
    .getByRole("row")
    .filter({ hasText: "fixture-task-1" })
    .getByRole("button", { name: "删除", exact: true });
  await remove.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: "清理这些任务记录？" })).toBeVisible();
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  await expect(remove).toBeFocused();
  await remove.click();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  expect(api.writes).toEqual([]);
});

test("empty account result does not display fabricated data", async ({ page }) => {
  const api = await mockApi(page, { empty: true });
  await signIn(page);
  await expect(page.getByText("没有匹配的账户", { exact: true })).toBeVisible();
  await expect(page.getByText("creator@example.test", { exact: true })).toHaveCount(0);
  expect(api.writes).toEqual([]);
});

test("advanced mailbox settings are progressive and preserve the draft", async ({ page }) => {
  const api = await mockApi(page);
  await signIn(page);
  await page.goto("/register/");
  await page.locator("#registration-mail > summary").click();
  const advanced = page
    .locator("details")
    .filter({ has: page.locator("summary", { hasText: "取信与验证码高级设置" }) })
    .last();
  await expect(advanced).not.toHaveAttribute("open", "");
  await advanced.locator("summary").focus();
  await page.keyboard.press("Enter");
  const count = page.getByLabel("单次取信数量", { exact: true });
  await count.fill("17");
  await advanced.locator("summary").click();
  await advanced.locator("summary").click();
  await expect(count).toHaveValue("17");
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(result.violations.map((item) => item.id)).toEqual([]);
  expect(api.writes).toEqual([]);
});

test("account import opens with a named dialog and closes without writing", async ({ page }) => {
  const api = await mockApi(page);
  await signIn(page);
  await expect(page.getByText("creator@example.test", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "导入", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading").first()).toBeVisible();
  await dialog.getByRole("button", { name: /^导入 Access Token/ }).click();
  await expect(dialog.getByPlaceholder("每行一个 Access Token...")).toBeVisible();
  const result = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(result.violations.map((item) => item.id)).toEqual([]);
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  expect(api.writes).toEqual([]);
});

for (const route of ["accounts", "register", "settings", "image"])
  test("mobile and dark accessibility: " + route, async ({ page }, testInfo) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await mockApi(page);
    await signIn(page);
    await page.goto("/" + route + "/");
    await expect(page.getByRole("heading", { level: 1 }).first()).toBeVisible();
    await page.getByRole("button", { name: "切换为深色主题" }).click();
    await page.waitForTimeout(500);
    const dimensions = await page.evaluate(() => ({
      width: window.innerWidth,
      scroll: document.documentElement.scrollWidth,
      overflows: [...document.querySelectorAll("main, section, header, footer, [data-slot=card]")]
        .filter((node) => node.getBoundingClientRect().right > window.innerWidth + 1)
        .map((node) => ({
          tag: node.tagName,
          class: node.className,
          width: node.getBoundingClientRect().width,
        })),
    }));
    expect(dimensions, JSON.stringify(dimensions)).toMatchObject({ width: 390, scroll: 390 });
    const result = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
      .analyze();
    expect(
      result.violations.map((item) => ({
        id: item.id,
        nodes: item.nodes.map((node) => node.target),
      })),
    ).toEqual([]);
    await page.screenshot({
      path: testInfo.outputPath(route + "-mobile-dark.png"),
      fullPage: true,
    });
  });

test("visual evidence for desktop, mobile and dark mode", async ({ page }, testInfo) => {
  await mockApi(page);
  await page.goto("/login/");
  await expect(page.locator(".login-form")).toBeVisible();
  await expect(page.locator(".workspace-auth-loading")).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("login-desktop.png"), fullPage: true });
  await signIn(page);
  await expect(page.getByPlaceholder("搜索邮箱")).toBeVisible();
  await expect(page.getByText("creator@example.test", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "gpt-5", exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("accounts-desktop.png"), fullPage: true });
  await page.getByRole("button", { name: "切换为深色主题" }).click();
  await page.screenshot({ path: testInfo.outputPath("accounts-dark.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  await page.evaluate(
    () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
  );
  await page.screenshot({ path: testInfo.outputPath("accounts-mobile.png"), fullPage: true });
});
