import { expect, type Page } from "@playwright/test";
export const registerConfig = {
  enabled: false,
  mail: {
    request_timeout: 30,
    wait_timeout: 30,
    wait_interval: 2,
    providers: [
      {
        type: "outlook_external",
        enable: true,
        api_base: "https://mail.example.test",
        api_key: "",
        group_id: 1,
        folder: "all",
        prefer_alias: true,
        use_plus_alias: true,
      },
    ],
  },
  proxy: "",
  proxy_session_ttl_seconds: 900,
  proxy_ttl_unit: "seconds",
  proxy_region: "",
  total: 5,
  threads: 5,
  max_attempts: 1,
  mode: "total",
  target_quota: 0,
  target_available: 0,
  check_interval: 60,
  stats: { success: 0, fail: 0, done: 0, running: 0, threads: 5 },
  logs: [],
};
export const accounts = [
  {
    access_token: "fixture-token-one",
    type: "plus",
    status: "正常",
    quota: 24,
    success: 12,
    fail: 0,
    email: "creator@example.test",
    image_pool_state: "ready",
    image_quota_confidence: "verified",
    image_inflight: 0,
  },
  {
    access_token: "fixture-token-two",
    type: "free",
    status: "限流",
    quota: 0,
    success: 3,
    fail: 1,
    email: "studio@example.test",
    image_pool_state: "cooldown",
    image_inflight: 0,
  },
  {
    access_token: "fixture-token-three",
    type: "free",
    status: "异常",
    quota: 0,
    success: 0,
    fail: 1,
    email: "review@example.test",
    image_pool_state: "quarantined",
    image_inflight: 0,
  },
];
export async function mockApi(
  page: Page,
  options: { empty?: boolean; failTasks?: boolean; failAccounts?: boolean } = {},
) {
  const unexpected: string[] = [];
  const writes: string[] = [];
  let role = "admin";
  await page.route("**/*", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname.replace(/\/$/, "");
    if (url.hostname !== "127.0.0.1" && url.hostname !== "localhost") {
      await route.abort();
      return;
    }
    if (!path.startsWith("/api/") && !path.startsWith("/v1/") && path !== "/auth/login") {
      await route.continue();
      return;
    }
    const headers = {
      "access-control-allow-origin": "*",
      "access-control-allow-headers": "*",
      "access-control-allow-methods": "*",
    };
    const respond = (data: unknown, status = 200) => route.fulfill({ status, json: data, headers });
    if (req.method() === "OPTIONS") {
      await respond({});
      return;
    }
    if (path === "/auth/login") {
      const key = (req.postDataJSON() as { auth_key?: string; key?: string }) || {};
      const value =
        (req.headers()["authorization"] || "").replace(/^Bearer\s+/i, "") ||
        key.auth_key ||
        key.key ||
        "";
      if (value === "invalid-fixture-key") {
        await respond({ detail: "密钥无效，请检查后重试" }, 401);
        return;
      }
      role = value.includes("user") ? "user" : "admin";
      await respond({
        role,
        subject_id: "fixture-subject",
        name: role === "admin" ? "设计验收" : "创作者",
      });
      return;
    }
    if (req.method() !== "GET") {
      writes.push(req.method() + " " + path);
      await respond({ detail: "Unexpected mutation blocked by browser fixture" }, 409);
      return;
    }
    if (path === "/api/register/events") {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: "data: " + JSON.stringify(registerConfig) + "\n\n",
        headers,
      });
      return;
    }
    const routes: Record<string, unknown> = {
      "/api/accounts": { items: options.empty ? [] : accounts },
      "/api/accounts/health-check/current": { job: null },
      "/v1/models": {
        object: "list",
        data: [
          { id: "gpt-5", object: "model", owned_by: "openai" },
          { id: "gpt-image-1", object: "model", owned_by: "openai" },
        ],
      },
      "/api/third-party-apps": {
        third_party_apps: { infinite_canvas: { enabled: false, url: "" } },
      },
      "/api/runtime-profiles": { items: [], audit: { total: 0, ok: 0, failed: 0, errors: [] } },
      "/api/risk/summary": {
        settings: {},
        accounts: { total: 0, capabilities: {} },
        profiles: { total: 0, ok: 0, failed: 0 },
        proxies: { total: 0, healthy: 0, cooldown: 0, dead: 0 },
        risk_events: { total: 0, recent_by_code: {} },
        tasks: { total: 0, by_status: {} },
        updated_at: "2026-10-31T00:00:00Z",
      },
      "/api/risk/events": { items: [] },
      "/api/accounts/capabilities": { items: [] },
      "/api/proxies": { items: [] },
      "/api/proxies/platform": {
        status: "not_configured",
        source: "proxy-control",
        nodes: [],
        bindings: {
          unit: "profile_binding",
          enabled: true,
          strict: true,
          limit: 5,
          used: 0,
          remaining: 5,
          groups: 5,
        },
      },
      "/api/tasks": {
        items: options.empty
          ? []
          : [
              {
                id: "fixture-task-1",
                type: "image",
                status: "success",
                source: "fixture",
                progress: "完成",
                created_at: "2026-10-31T00:00:00Z",
              },
            ],
      },
      "/api/conversations": { items: [] },
      "/v1/videos": { items: [], missing_ids: [] },
      "/api/images": { items: [], groups: [] },
      "/api/images/tags": { tags: [] },
      "/api/images/storage": {
        disk_total_mb: 10240,
        disk_used_mb: 1024,
        disk_free_mb: 9216,
        image_count: 0,
        image_size_mb: 0,
        image_size_bytes: 0,
      },
      "/api/logs": { items: [] },
      "/api/register": { register: registerConfig },
      "/api/settings": {
        config: {
          proxy: "",
          base_url: "",
          sensitive_words: [],
          log_levels: [],
          features: { chat: true, image: true, video: true, register: true },
        },
      },
      "/api/cpa/pools": { pools: [] },
      "/api/sub2api/servers": { servers: [] },
      "/api/auth/users": { items: [] },
      "/api/backups": { items: [], state: { running: false }, settings: { enabled: false } },
      "/v1/editable-file-tasks": { items: [], missing_ids: [] },
    };
    if (path === "/api/accounts" && options.failAccounts) {
      await respond({ detail: "账户服务暂时不可用" }, 503);
      return;
    }
    if (path === "/api/tasks" && options.failTasks) {
      await respond({ detail: "测试网络故障，请重试" }, 503);
      return;
    }
    if (path in routes) {
      await respond(routes[path]);
      return;
    }
    unexpected.push(path);
    await respond({ detail: "Missing explicit test fixture: " + path }, 404);
  });
  return { unexpected, writes };
}
export async function signIn(page: Page, role = "admin") {
  await page.goto("/login/");
  await page.getByLabel("访问密钥", { exact: true }).fill("fixture-" + role + "-key");
  await page.getByRole("button", { name: "进入工作空间" }).click();
  await expect(page).toHaveURL(role === "admin" ? /accounts/ : /image/);
  await expect(page.locator(".workspace-topbar")).toBeVisible();
}

export async function navigateTo(page: Page, route: string) {
  const link = page.locator('.workspace-sidebar nav a[href="/' + route + '/"]');
  if (await link.isVisible()) await link.click();
  else {
    await page.getByRole("button", { name: "打开导航", exact: true }).click();
    await page
      .getByRole("dialog")
      .locator('nav a[href="/' + route + '/"]')
      .click();
  }
  await expect(page).toHaveURL(new RegExp("/" + route + "/$"));
}
