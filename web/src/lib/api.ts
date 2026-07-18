import { httpRequest, request } from "@/lib/request";

export type AccountType = string;
export type AccountStatus = "正常" | "限流" | "异常" | "禁用";
export type ImagePoolState = "ready" | "probation" | "cooldown" | "exhausted" | "quarantined" | "disabled";
export type ImageQuotaConfidence = "verified" | "estimated" | "unknown";
export type ImageModel = string;
export type AuthRole = "admin" | "user";
export type ImageStorageMode = "local" | "webdav" | "both";

export type ImageStorageSettings = {
  enabled: boolean;
  mode: ImageStorageMode;
  webdav_url: string;
  webdav_username: string;
  webdav_password: string;
  webdav_root_path: string;
  public_base_url: string;
};

export type Account = {
  access_token: string;
  type: AccountType;
  source_type?: string | null;
  status: AccountStatus;
  quota: number;
  image_quota_unknown?: boolean;
  email?: string | null;
  user_id?: string | null;
  limits_progress?: Array<{
    feature_name?: string;
    remaining?: number;
    reset_after?: string;
  }>;
  default_model_slug?: string | null;
  restore_at?: string | null;
  success: number;
  fail: number;
  /** 当前图片在途数(正在生成、尚未结束的图片数)。号池空闲时持续 > 0 表示并发槽位泄漏。 */
  image_inflight?: number;
  image_pool_state?: ImagePoolState;
  image_pool_reason?: string | null;
  image_quota_confidence?: ImageQuotaConfidence;
  image_quota_updated_at?: string | null;
  image_cooldown_until?: number | string | null;
  image_next_probe_at?: number | string | null;
  image_last_probe_at?: string | null;
  image_last_probe_error?: string | null;
  image_success_ema?: number;
  image_latency_ema_ms?: number;
  image_consecutive_failures?: number;
  image_probe_inflight?: boolean;
  last_used_at?: string | null;
  proxy?: string | null;
  runtime_profile_id?: string | null;
  profile_status?: string | null;
  fp?: Record<string, string>;
  profile_snapshot?: RuntimeProfile | null;
  proxy_policy?: RuntimeProfile["proxy_policy"] | null;
};

export type AccountImportPayload = {
  access_token: string;
  accessToken?: string;
  type?: string;
  export_type?: string;
  source_type?: string;
  [key: string]: unknown;
};

export type Model = {
  id: string;
  object: string;
  created: number;
  owned_by: string;
  permission: unknown[];
  root: string;
  parent: string | null;
};

type AccountListResponse = {
  items: Account[];
};

type ModelListResponse = {
  object: string;
  data: Model[];
};

type AccountMutationResponse = {
  items: Account[];
  added?: number;
  skipped?: number;
  removed?: number;
  refreshed?: number;
  relogined?: number;
  errors?: Array<{ access_token: string; error: string }>;
};

export type AccountRefreshResponse = {
  items: Account[];
  refreshed: number;
  relogined?: number;
  errors: Array<{ access_token: string; error: string }>;
};

export type RefreshProgressResponse = {
  total: number;
  processed: number;
  done: boolean;
  error: string | null;
  status_counts?: Record<string, number>;
  total_quota?: number;
  result?: AccountRefreshResponse | null;
  results?: Array<{ token: string; status: string; error?: string | null }>;
};

type AccountUpdateResponse = {
  item: Account;
  items: Account[];
};

export type RuntimeProfile = {
  id: string;
  account_key?: string;
  template?: string;
  status?: string;
  tls?: {
    impersonate?: string;
  };
  headers?: {
    "user-agent"?: string;
    user_agent_preview?: string;
    "sec-ch-ua"?: string;
    "sec-ch-ua-mobile"?: string;
    "sec-ch-ua-platform"?: string;
    "accept-language"?: string;
    [key: string]: string | undefined;
  };
  openai?: {
    "oai-device-id"?: string;
    "oai-session-id"?: string;
  };
  locale?: {
    timezone?: string;
    language?: string;
  };
  proxy_policy?: {
    mode?: string;
    group?: string;
    register_proxy?: string;
    runtime_proxy?: string;
    allow_rotate?: boolean;
  };
  clearance?: {
    scope?: string;
    last_refresh_at?: string | null;
  };
  version?: number;
  created_at?: string;
  updated_at?: string;
};

export type RuntimeProfileAuditItem = {
  account_key?: string;
  runtime_profile_id?: string;
  ok: boolean;
  missing: string[];
  warnings: string[];
  profile: RuntimeProfile;
};

export type RuntimeProfileAudit = {
  total: number;
  ok: number;
  failed: number;
  items: RuntimeProfileAuditItem[];
};

export type RuntimeProfileListResponse = {
  items: RuntimeProfile[];
  audit: RuntimeProfileAudit;
};

export type RuntimeProfileAccountResponse = {
  item: Account;
  profile: RuntimeProfile;
  audit: RuntimeProfileAuditItem;
};

export type RuntimeProfileBackfillResponse = {
  repaired: number;
  rebounded: number;
  items: Account[];
  profiles: RuntimeProfile[];
  audit: RuntimeProfileAudit;
};

export type ProxyRuntimeEgressMode = "direct" | "single_proxy";
export type ProxyRuntimeClearanceMode = "none" | "manual" | "flaresolverr";

export type ProxyRuntimeClearanceSettings = {
  enabled: boolean;
  mode: ProxyRuntimeClearanceMode;
  cf_cookies: string;
  cf_clearance: string;
  user_agent: string;
  browser: string;
  flaresolverr_url: string;
  timeout_sec: number | string;
  refresh_interval: number | string;
  warm_up_on_start: boolean;
  has_cf_cookies?: boolean;
  has_cf_clearance?: boolean;
};

export type ProxyRuntimeSettings = {
  enabled: boolean;
  egress_mode: ProxyRuntimeEgressMode;
  proxy_url: string;
  resource_proxy_url: string;
  skip_ssl_verify: boolean;
  reset_session_status_codes: number[];
  clearance: ProxyRuntimeClearanceSettings;
};

export type ProxyRuntimeStatus = {
  enabled: boolean;
  egress_mode: ProxyRuntimeEgressMode | string;
  proxy_source: string;
  has_proxy: boolean;
  runtime_profile_id?: string;
  impersonate?: string;
  clearance_enabled: boolean;
  clearance_mode: ProxyRuntimeClearanceMode | string;
  has_clearance_bundle: boolean;
  cached_clearance_count?: number;
  cached_clearance_hosts: string[];
  cached_clearance_profiles?: string[];
};

export type ProxyRuntimeResponse = {
  runtime: ProxyRuntimeSettings;
  status: ProxyRuntimeStatus;
};

export type ThirdPartyAppsSettings = {
  infinite_canvas: {
    enabled: boolean;
    url: string;
  };
};

export type FeatureFlags = {
  chat: boolean;
  image: boolean;
  video: boolean;
  register: boolean;
};

export type VideoSettings = {
  enabled: boolean;
  provider: string;
  poll_interval_secs: number | string;
  poll_timeout_secs: number | string;
  storage_dir: string;
};

export type SettingsConfig = {
  proxy: string;
  base_url?: string;
  global_system_prompt?: string;
  sensitive_words?: string[];
  ai_review?: {
    enabled?: boolean;
    base_url?: string;
    api_key?: string;
    model?: string;
    prompt?: string;
  };
  refresh_account_interval_minute?: number | string;
  image_retention_days?: number | string;
  image_poll_timeout_secs?: number | string;
  image_account_concurrency?: number | string;
  image_parallel_generation?: boolean;
  image_settle_enabled?: boolean;
  image_check_before_hit_enabled?: boolean;
  image_settle_secs?: number | string;
  image_timeout_retry_secs?: number | string;
  auto_remove_invalid_accounts?: boolean;
  auto_relogin_after_refresh?: boolean;
  log_levels?: string[];
  image_storage?: ImageStorageSettings;
  proxy_runtime?: ProxyRuntimeSettings;
  third_party_apps?: ThirdPartyAppsSettings;
  features?: FeatureFlags;
  video?: VideoSettings;
  backup?: BackupSettings;
  backup_state?: BackupState;
  [key: string]: unknown;
};

export type BackupInclude = {
  config: boolean;
  register: boolean;
  cpa: boolean;
  sub2api: boolean;
  logs: boolean;
  image_tasks: boolean;
  accounts_snapshot: boolean;
  auth_keys_snapshot: boolean;
  images: boolean;
};

export type BackupSettings = {
  enabled: boolean;
  provider: "cloudflare_r2" | string;
  account_id: string;
  access_key_id: string;
  secret_access_key: string;
  bucket: string;
  prefix: string;
  interval_minutes: number | string;
  rotation_keep: number | string;
  encrypt: boolean;
  passphrase: string;
  include: BackupInclude;
};

export type BackupState = {
  running: boolean;
  last_started_at?: string | null;
  last_finished_at?: string | null;
  last_status?: string;
  last_error?: string | null;
  last_object_key?: string | null;
};

export type BackupItem = {
  key: string;
  name: string;
  size: number;
  updated_at?: string | null;
  encrypted: boolean;
};

export type BackupDetail = {
  key: string;
  name: string;
  encrypted: boolean;
  created_at?: string | null;
  trigger?: string | null;
  app_version?: string | null;
  storage_backend?: Record<string, unknown> | null;
  files: Array<{
    name: string;
    exists: boolean;
    content_type?: string;
    size: number;
    sha256?: string;
  }>;
  snapshots: Array<{
    name: string;
    count: number;
  }>;
};

export type ManagedImage = {
  rel: string;
  path?: string;
  name: string;
  date: string;
  size: number;
  url: string;
  thumbnail_url?: string;
  created_at: string;
  width?: number;
  height?: number;
  tags?: string[];
};

export type SystemLog = {
  id: string;
  time: string;
  type: "call" | "account" | string;
  summary?: string;
  detail?: Record<string, unknown>;
  [key: string]: unknown;
};

export type ImageData = {
  b64_json?: string;
  url?: string;
  original_url?: string;
  revised_prompt?: string;
  source_width?: number;
  source_height?: number;
  width?: number;
  height?: number;
  requested_width?: number | null;
  requested_height?: number | null;
  output_transform?: "original" | "lanczos_upscale" | string;
};

export type ImageResponse = {
  created: number;
  data: ImageData[];
};

export type ImageTask = {
  id: string;
  status: "queued" | "running" | "success" | "error";
  mode: "generate" | "edit";
  model?: ImageModel;
  size?: string;
  quality?: string;
  created_at: string;
  updated_at: string;
  conversation_id?: string;
  data?: ImageData[];
  error?: string;
  progress?: string;
  elapsed_secs?: number;
  duration_ms?: number;
};

type ImageTaskListResponse = {
  items: ImageTask[];
  missing_ids: string[];
};

export type LoginResponse = {
  ok: boolean;
  version: string;
  role: AuthRole;
  subject_id: string;
  name: string;
};

export type UserKey = {
  id: string;
  name: string;
  role: "user";
  enabled: boolean;
  created_at: string | null;
  last_used_at: string | null;
};

export type OutlookPoolStats = {
  unused: number;
  in_use: number;
  used: number;
  token_invalid: number;
  failed: number;
};

export type RegisterConfig = {
  enabled: boolean;
  mail: {
    request_timeout: number;
    wait_timeout: number;
    wait_interval: number;
    providers: Array<Record<string, unknown>>;
  };
  proxy: string;
  total: number;
  threads: number;
  mode: "total" | "quota" | "available";
  target_quota: number;
  target_available: number;
  check_interval: number;
  stats: {
    job_id?: string;
    success: number;
    fail: number;
    done: number;
    running: number;
    threads: number;
    elapsed_seconds?: number;
    avg_seconds?: number;
    success_rate?: number;
    current_quota?: number;
    current_available?: number;
    started_at?: string;
    updated_at?: string;
    finished_at?: string;
  };
  logs?: Array<{
    time: string;
    text: string;
    level: string;
  }>;
};

export async function login(authKey: string) {
  const normalizedAuthKey = String(authKey || "").trim();
  return httpRequest<LoginResponse>("/auth/login", {
    method: "POST",
    body: {},
    headers: {
      Authorization: `Bearer ${normalizedAuthKey}`,
    },
    redirectOnUnauthorized: false,
  });
}

export async function fetchAccounts() {
  return httpRequest<AccountListResponse>("/api/accounts");
}

export type ImagePoolProbeResponse = {
  checked: number;
  healthy: number;
  removed: number;
  quarantined: number;
  failures: Array<{ account_hash: string; error: string }>;
  stats?: Record<string, unknown>;
  items: Account[];
};

export async function probeImagePool(limit = 20) {
  return httpRequest<ImagePoolProbeResponse>("/api/accounts/image-pool/probe", {
    method: "POST",
    body: { limit },
  });
}

export async function fetchModels() {
  return httpRequest<ModelListResponse>("/v1/models");
}

export async function createAccounts(tokens: string[], accounts: AccountImportPayload[] = []) {
  return httpRequest<AccountMutationResponse>("/api/accounts", {
    method: "POST",
    body: { tokens, accounts },
  });
}

export type OAuthLoginStartResponse = {
  session_id: string;
  authorize_url: string;
  expires_in: string;
  redirect_uri_prefix: string;
};

export async function startOAuthLogin(emailHint?: string) {
  return httpRequest<OAuthLoginStartResponse>("/api/accounts/oauth/start", {
    method: "POST",
    body: { email_hint: emailHint ?? "" },
  });
}

export async function finishOAuthLogin(sessionId: string, callback: string) {
  return httpRequest<AccountMutationResponse>("/api/accounts/oauth/finish", {
    method: "POST",
    body: { session_id: sessionId, callback },
  });
}

export async function deleteAccounts(tokens: string[]) {
  return httpRequest<AccountMutationResponse>("/api/accounts", {
    method: "DELETE",
    body: { tokens },
  });
}

export async function refreshAccounts(accessTokens: string[]) {
  return httpRequest<{ progress_id: string }>("/api/accounts/refresh", {
    method: "POST",
    body: { access_tokens: accessTokens },
  });
}

export async function fetchRefreshProgress(progressId: string) {
  return httpRequest<RefreshProgressResponse>(`/api/accounts/refresh/progress/${progressId}`);
}

export async function reLoginAccounts(accessTokens: string[]) {
  return httpRequest<{ progress_id: string }>("/api/accounts/re-login", {
    method: "POST",
    body: { access_tokens: accessTokens },
  });
}

export async function fetchReLoginProgress(progressId: string) {
  return httpRequest<RefreshProgressResponse>(`/api/accounts/re-login/progress/${progressId}`);
}

export async function updateAccount(
  accessToken: string,
  updates: {
    type?: AccountType;
    status?: AccountStatus;
    quota?: number;
    proxy?: string;
  },
) {
  return httpRequest<AccountUpdateResponse>("/api/accounts/update", {
    method: "POST",
    body: {
      access_token: accessToken,
      ...updates,
    },
  });
}

export async function fetchRuntimeProfiles() {
  return httpRequest<RuntimeProfileListResponse>("/api/runtime-profiles");
}

export async function auditRuntimeProfiles() {
  return httpRequest<RuntimeProfileAudit>("/api/runtime-profiles/audit");
}

export async function backfillRuntimeProfiles(accessTokens: string[] = []) {
  return httpRequest<RuntimeProfileBackfillResponse>("/api/runtime-profiles/backfill", {
    method: "POST",
    body: { access_tokens: accessTokens },
  });
}

export async function repairRuntimeProfile(accessToken: string, profileId = "") {
  return httpRequest<RuntimeProfileAccountResponse>("/api/runtime-profiles/repair", {
    method: "POST",
    body: { access_token: accessToken, profile_id: profileId },
  });
}

export async function rebindRuntimeProfile(accessToken: string, profileId = "") {
  return httpRequest<RuntimeProfileAccountResponse>("/api/runtime-profiles/rebind", {
    method: "POST",
    body: { access_token: accessToken, profile_id: profileId },
  });
}

export async function generateImage(prompt: string, model?: ImageModel, size?: string, quality = "auto") {
  return httpRequest<ImageResponse>(
    "/v1/images/generations",
    {
      method: "POST",
      body: {
        prompt,
        ...(model ? { model } : {}),
        ...(size ? { size } : {}),
        quality,
        n: 1,
        response_format: "b64_json",
      },
    },
  );
}

export async function editImage(files: File | File[], prompt: string, model?: ImageModel, size?: string, quality = "auto") {
  const formData = new FormData();
  const uploadFiles = Array.isArray(files) ? files : [files];

  uploadFiles.forEach((file) => {
    formData.append("image", file);
  });
  formData.append("prompt", prompt);
  if (model) {
    formData.append("model", model);
  }
  if (size) {
    formData.append("size", size);
  }
  formData.append("quality", quality);
  formData.append("n", "1");

  return httpRequest<ImageResponse>(
    "/v1/images/edits",
    {
      method: "POST",
      body: formData,
    },
  );
}

export async function createImageGenerationTask(clientTaskId: string, prompt: string, model?: ImageModel, size?: string, quality = "auto") {
  return httpRequest<ImageTask>("/api/image-tasks/generations", {
    method: "POST",
    body: {
      client_task_id: clientTaskId,
      prompt,
      ...(model ? { model } : {}),
      ...(size ? { size } : {}),
      quality,
    },
  });
}

export async function createImageEditTask(
  clientTaskId: string,
  files: File | File[],
  prompt: string,
  model?: ImageModel,
  size?: string,
  quality = "auto",
) {
  const formData = new FormData();
  const uploadFiles = Array.isArray(files) ? files : [files];

  uploadFiles.forEach((file) => {
    formData.append("image", file);
  });
  formData.append("client_task_id", clientTaskId);
  formData.append("prompt", prompt);
  if (model) {
    formData.append("model", model);
  }
  if (size) {
    formData.append("size", size);
  }
  formData.append("quality", quality);

  return httpRequest<ImageTask>("/api/image-tasks/edits", {
    method: "POST",
    body: formData,
  });
}

export async function fetchImageTasks(ids: string[]) {
  const params = new URLSearchParams();
  if (ids.length > 0) {
    params.set("ids", ids.join(","));
  }
  params.set("_t", String(Date.now()));
  return httpRequest<ImageTaskListResponse>(`/api/image-tasks?${params.toString()}`);
}

export async function resumeImagePoll(taskId: string, extraTimeoutSecs = 30) {
  return httpRequest<ImageTask>(`/api/image-tasks/${encodeURIComponent(taskId)}/resume-poll`, {
    method: "POST",
    body: { extra_timeout_secs: extraTimeoutSecs },
  });
}

export async function fetchSettingsConfig() {
  return httpRequest<{ config: SettingsConfig }>("/api/settings");
}

export async function updateSettingsConfig(settings: SettingsConfig) {
  return httpRequest<{ config: SettingsConfig }>("/api/settings", {
    method: "POST",
    body: settings,
  });
}

export async function fetchThirdPartyApps() {
  return httpRequest<{ third_party_apps: ThirdPartyAppsSettings }>("/api/third-party-apps");
}

export async function testBackupConnection() {
  return httpRequest<{ result: { ok: boolean; status: number } }>("/api/backup/test", {
    method: "POST",
    body: {},
  });
}

export async function testImageStorageConnection() {
  return httpRequest<{ result: { ok: boolean; status: number; error?: string } }>("/api/image-storage/test", {
    method: "POST",
    body: {},
  });
}

export async function syncImageStorage() {
  return httpRequest<{ result: { uploaded: number; skipped: number; failed: number } }>("/api/image-storage/sync", {
    method: "POST",
    body: {},
  });
}

export async function fetchBackups() {
  return httpRequest<{ items: BackupItem[]; state: BackupState; settings: BackupSettings }>("/api/backups");
}

export async function runBackupNow() {
  return httpRequest<{ result: { key: string; size: number; encrypted: boolean } }>("/api/backups/run", {
    method: "POST",
    body: {},
  });
}

export async function deleteBackup(key: string) {
  return httpRequest<{ ok: boolean }>("/api/backups/delete", {
    method: "POST",
    body: { key },
  });
}

export async function fetchBackupDetail(key: string) {
  const params = new URLSearchParams();
  params.set("key", key);
  return httpRequest<{ item: BackupDetail }>(`/api/backups/detail?${params.toString()}`);
}

export function getBackupDownloadUrl(key: string) {
  const params = new URLSearchParams();
  params.set("key", key);
  return `/api/backups/download?${params.toString()}`;
}

export async function fetchManagedImages(filters: { start_date?: string; end_date?: string }) {
  const params = new URLSearchParams();
  if (filters.start_date) params.set("start_date", filters.start_date);
  if (filters.end_date) params.set("end_date", filters.end_date);
  return httpRequest<{ items: ManagedImage[]; groups: Array<{ date: string; items: ManagedImage[] }> }>(
    `/api/images${params.toString() ? `?${params.toString()}` : ""}`,
  );
}

export async function deleteManagedImages(body: { paths?: string[]; start_date?: string; end_date?: string; all_matching?: boolean }) {
  return httpRequest<{ removed: number }>("/api/images/delete", { method: "POST", body });
}

export async function downloadImages(paths: string[]) {
  const response = await request.post("/api/images/download", { paths }, { responseType: "blob" });
  const blob = response.data as Blob;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "images.zip";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export async function downloadSingleImage(path: string) {
  const response = await request.get(`/api/images/download/${path}`, { responseType: "blob" });
  const blob = response.data as Blob;
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = path.split("/").pop() || "image.png";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export async function fetchImageTags() {
  return httpRequest<{ tags: string[] }>("/api/images/tags");
}

export async function setImageTags(path: string, tags: string[]) {
  return httpRequest<{ ok: boolean; tags: string[] }>("/api/images/tags", {
    method: "POST",
    body: { path, tags },
  });
}

export async function deleteImageTag(tag: string) {
  return httpRequest<{ ok: boolean; removed_from: number }>(`/api/images/tags/${encodeURIComponent(tag)}`, {
    method: "DELETE",
  });
}

export type ImageStorageStats = {
  disk_total_mb: number; disk_used_mb: number; disk_free_mb: number;
  image_count: number; image_size_mb: number; image_size_bytes: number;
};

export async function fetchImageStorage() {
  return httpRequest<ImageStorageStats>("/api/images/storage");
}

export async function compressAllImages() {
  return httpRequest<{ compressed: number; saved_bytes: number; saved_mb: number }>("/api/images/storage/compress", { method: "POST" });
}

export async function deleteToTarget(targetFreeMb: number) {
  return httpRequest<{ removed: number; freed_mb: number; done: boolean }>(
    `/api/images/storage/cleanup-to-target?target_free_mb=${targetFreeMb}&dry_run=false`,
    { method: "POST" },
  );
}

export async function fetchSystemLogs(filters: { type?: string; start_date?: string; end_date?: string }) {
  const params = new URLSearchParams();
  if (filters.type) params.set("type", filters.type);
  if (filters.start_date) params.set("start_date", filters.start_date);
  if (filters.end_date) params.set("end_date", filters.end_date);
  return httpRequest<{ items: SystemLog[] }>(`/api/logs${params.toString() ? `?${params.toString()}` : ""}`);
}

export async function deleteSystemLogs(ids: string[]) {
  return httpRequest<{ removed: number }>("/api/logs/delete", {
    method: "POST",
    body: { ids },
  });
}

export async function fetchUserKeys() {
  return httpRequest<{ items: UserKey[] }>("/api/auth/users");
}

export async function createUserKey(name: string) {
  return httpRequest<{ item: UserKey; key: string; items: UserKey[] }>("/api/auth/users", {
    method: "POST",
    body: { name },
  });
}

export async function updateUserKey(keyId: string, updates: { enabled?: boolean; name?: string; key?: string }) {
  return httpRequest<{ item: UserKey; items: UserKey[] }>(`/api/auth/users/${keyId}`, {
    method: "POST",
    body: updates,
  });
}

export async function deleteUserKey(keyId: string) {
  return httpRequest<{ items: UserKey[] }>(`/api/auth/users/${keyId}`, {
    method: "DELETE",
  });
}

export async function fetchRegisterConfig() {
  return httpRequest<{ register: RegisterConfig }>("/api/register");
}

export async function updateRegisterConfig(updates: Partial<RegisterConfig>) {
  return httpRequest<{ register: RegisterConfig }>("/api/register", {
    method: "POST",
    body: updates,
  });
}

export async function startRegister() {
  return httpRequest<{ register: RegisterConfig }>("/api/register/start", {
    method: "POST",
    body: { confirm: true },
  });
}

export async function stopRegister() {
  return httpRequest<{ register: RegisterConfig }>("/api/register/stop", { method: "POST" });
}

export async function resetRegister() {
  return httpRequest<{ register: RegisterConfig }>("/api/register/reset", { method: "POST" });
}

export async function resetOutlookPool(scope: "all" | "failed" | "unused" = "all") {
  return httpRequest<{ register: RegisterConfig }>("/api/register/outlook-pool/reset", {
    method: "POST",
    body: { scope },
  });
}

// ── CPA (CLIProxyAPI) ──────────────────────────────────────────────

export type CPAPool = {
  id: string;
  name: string;
  base_url: string;
  import_job?: CPAImportJob | null;
};

export type CPARemoteFile = {
  name: string;
  email: string;
};

export type CPAImportJob = {
  job_id: string;
  status: "pending" | "running" | "completed" | "failed";
  created_at: string;
  updated_at: string;
  total: number;
  completed: number;
  added: number;
  skipped: number;
  refreshed: number;
  failed: number;
  errors: Array<{ name: string; error: string }>;
};

export async function fetchCPAPools() {
  return httpRequest<{ pools: CPAPool[] }>("/api/cpa/pools");
}

export async function createCPAPool(pool: { name: string; base_url: string; secret_key: string }) {
  return httpRequest<{ pool: CPAPool; pools: CPAPool[] }>("/api/cpa/pools", {
    method: "POST",
    body: pool,
  });
}

export async function updateCPAPool(
  poolId: string,
  updates: { name?: string; base_url?: string; secret_key?: string },
) {
  return httpRequest<{ pool: CPAPool; pools: CPAPool[] }>(`/api/cpa/pools/${poolId}`, {
    method: "POST",
    body: updates,
  });
}

export async function deleteCPAPool(poolId: string) {
  return httpRequest<{ pools: CPAPool[] }>(`/api/cpa/pools/${poolId}`, {
    method: "DELETE",
  });
}

export async function fetchCPAPoolFiles(poolId: string) {
  return httpRequest<{ pool_id: string; files: CPARemoteFile[] }>(`/api/cpa/pools/${poolId}/files`);
}

export async function startCPAImport(poolId: string, names: string[]) {
  return httpRequest<{ import_job: CPAImportJob | null }>(`/api/cpa/pools/${poolId}/import`, {
    method: "POST",
    body: { names },
  });
}

export async function fetchCPAPoolImportJob(poolId: string) {
  return httpRequest<{ import_job: CPAImportJob | null }>(`/api/cpa/pools/${poolId}/import`);
}

// ── Sub2API ────────────────────────────────────────────────────────

export type Sub2APIServer = {
  id: string;
  name: string;
  base_url: string;
  email: string;
  has_api_key: boolean;
  group_id: string;
  import_job?: CPAImportJob | null;
};

export type Sub2APIRemoteAccount = {
  id: string;
  name: string;
  email: string;
  plan_type: string;
  status: string;
  expires_at: string;
  has_refresh_token: boolean;
};

export type Sub2APIRemoteGroup = {
  id: string;
  name: string;
  description: string;
  platform: string;
  status: string;
  account_count: number;
  active_account_count: number;
};

export async function fetchSub2APIServers() {
  return httpRequest<{ servers: Sub2APIServer[] }>("/api/sub2api/servers");
}

export async function createSub2APIServer(server: {
  name: string;
  base_url: string;
  email: string;
  password: string;
  api_key: string;
  group_id: string;
}) {
  return httpRequest<{ server: Sub2APIServer; servers: Sub2APIServer[] }>("/api/sub2api/servers", {
    method: "POST",
    body: server,
  });
}

export async function updateSub2APIServer(
  serverId: string,
  updates: {
    name?: string;
    base_url?: string;
    email?: string;
    password?: string;
    api_key?: string;
    group_id?: string;
  },
) {
  return httpRequest<{ server: Sub2APIServer; servers: Sub2APIServer[] }>(`/api/sub2api/servers/${serverId}`, {
    method: "POST",
    body: updates,
  });
}

export async function fetchSub2APIServerGroups(serverId: string) {
  return httpRequest<{ server_id: string; groups: Sub2APIRemoteGroup[] }>(
    `/api/sub2api/servers/${serverId}/groups`,
  );
}

export async function deleteSub2APIServer(serverId: string) {
  return httpRequest<{ servers: Sub2APIServer[] }>(`/api/sub2api/servers/${serverId}`, {
    method: "DELETE",
  });
}

export async function fetchSub2APIServerAccounts(serverId: string) {
  return httpRequest<{ server_id: string; accounts: Sub2APIRemoteAccount[] }>(
    `/api/sub2api/servers/${serverId}/accounts`,
  );
}

export async function startSub2APIImport(serverId: string, accountIds: string[]) {
  return httpRequest<{ import_job: CPAImportJob | null }>(`/api/sub2api/servers/${serverId}/import`, {
    method: "POST",
    body: { account_ids: accountIds },
  });
}

export async function fetchSub2APIImportJob(serverId: string) {
  return httpRequest<{ import_job: CPAImportJob | null }>(`/api/sub2api/servers/${serverId}/import`);
}

// ── Upstream proxy ────────────────────────────────────────────────

export type ProxySettings = {
  enabled: boolean;
  url: string;
};

export type ProxyTestResult = {
  ok: boolean;
  status: number;
  latency_ms: number;
  error: string | null;
  proxy_source?: string;
  has_proxy?: boolean;
};

export type ClearanceTestResult = {
  ok: boolean;
  status: string;
  latency_ms: number;
  has_cookies: boolean;
  user_agent: string;
  error: string | null;
  runtime: ProxyRuntimeStatus;
};

export async function fetchProxy() {
  return httpRequest<{ proxy: ProxySettings }>("/api/proxy");
}

export async function updateProxy(updates: { enabled?: boolean; url?: string }) {
  return httpRequest<{ proxy: ProxySettings }>("/api/proxy", {
    method: "POST",
    body: updates,
  });
}

export async function testProxy(url?: string) {
  return httpRequest<{ result: ProxyTestResult }>("/api/proxy/test", {
    method: "POST",
    body: { url: url ?? "" },
  });
}

export async function fetchProxyRuntime() {
  return httpRequest<ProxyRuntimeResponse>("/api/proxy/runtime");
}

export async function updateProxyRuntime(runtime: ProxyRuntimeSettings) {
  return httpRequest<ProxyRuntimeResponse>("/api/proxy/runtime", {
    method: "POST",
    body: runtime,
  });
}

export async function testProxyClearance(targetUrl?: string) {
  return httpRequest<{ result: ClearanceTestResult }>("/api/proxy/clearance/test", {
    method: "POST",
    body: { target_url: targetUrl ?? "https://chatgpt.com" },
  });
}

// ── Risk / Proxy / Unified Tasks ───────────────────────────────────

export type RiskSummary = {
  settings: Record<string, unknown>;
  accounts: { total: number; capabilities: Record<string, number> };
  profiles: { total?: number; ok?: number; failed?: number; error?: string };
  proxies: { total: number; healthy: number; cooldown: number; dead: number };
  risk_events: { total: number; recent_by_code: Record<string, number> };
  tasks: { total: number; by_status: Record<string, number> };
  updated_at: string;
};

export type RiskEvent = {
  id: string;
  time: string;
  code: string;
  message?: string;
  scope?: string;
  retryable?: boolean;
  cooldown_secs?: number;
  status_code?: number | null;
  account_key?: string;
  proxy_id?: string;
  proxy?: string;
  runtime_profile_id?: string;
  task_id?: string;
  raw?: Record<string, unknown>;
};

export type AccountCapability = {
  account_key: string;
  runtime_profile_id?: string | null;
  plan?: string;
  quota?: number;
  chat?: boolean;
  responses?: boolean;
  raw_conversation?: boolean;
  search?: boolean;
  image?: boolean;
  image_edit?: boolean;
  image_variation?: boolean;
  file?: boolean;
  audio_tts?: boolean;
  audio_stt?: boolean;
  audio_translation?: boolean;
  video?: boolean;
  source?: string;
  risk_status?: string;
  cooldown_until?: number;
  updated_at?: string;
  last_probe_at?: string;
};

export type ProxyNode = {
  id: string;
  proxy: string;
  status: "healthy" | "cooldown" | "dead" | string;
  score: number;
  success: number;
  fail: number;
  http_403: number;
  http_429: number;
  timeout: number;
  country?: string;
  provider?: string;
  asn?: string;
  last_success_at?: string | null;
  last_failure_at?: string | null;
  first_seen_at?: string;
  updated_at?: string;
  cooldown_until?: number | null;
};

export type UnifiedTask = {
  id: string;
  type: "register" | "image" | "chat" | "video" | "tombstone" | string;
  status: string;
  progress?: string;
  created_at?: string | null;
  updated_at?: string | null;
  duration_ms?: number;
  error?: string | null;
  error_code?: string | null;
  retryable?: boolean | null;
  error_scope?: string | null;
  cooldown_secs?: number;
  source?: string;
  success?: number;
  fail?: number;
  total?: number;
  threads?: number;
  current_quota?: number;
  current_available?: number;
};

export type ConversationMessage = {
  id: string;
  role: "system" | "user" | "assistant" | "tool" | string;
  content: string;
  created_at?: string;
  metadata?: Record<string, unknown>;
};

export type Conversation = {
  id: string;
  owner_id?: string;
  title: string;
  model: string;
  source?: string;
  status?: string;
  created_at?: string;
  updated_at?: string;
  message_count?: number;
  messages?: ConversationMessage[];
  metadata?: Record<string, unknown>;
};

export type ChatCompletionResponse = {
  id?: string;
  object?: string;
  created?: number;
  model?: string;
  conversation_id?: string;
  choices?: Array<{
    index?: number;
    message?: { role?: string; content?: string };
    finish_reason?: string | null;
  }>;
  error?: unknown;
};

export type VideoTask = {
  id: string;
  status: "queued" | "running" | "success" | "failed" | "error" | "cancelled" | string;
  mode?: string;
  model?: string;
  prompt?: string;
  size?: string;
  seconds?: number;
  created_at?: string;
  updated_at?: string;
  progress?: string;
  data?: Array<Record<string, unknown>>;
  error?: string;
  duration_ms?: number;
  base_url?: string;
};

export async function fetchRiskSummary() {
  return httpRequest<RiskSummary>("/api/risk/summary");
}

export async function fetchRiskEvents(filters: { limit?: number; code?: string; scope?: string } = {}) {
  const params = new URLSearchParams();
  params.set("limit", String(filters.limit ?? 200));
  if (filters.code) params.set("code", filters.code);
  if (filters.scope) params.set("scope", filters.scope);
  return httpRequest<{ items: RiskEvent[] }>(`/api/risk/events?${params.toString()}`);
}

export async function fetchAccountCapabilities() {
  return httpRequest<{ items: AccountCapability[] }>("/api/accounts/capabilities");
}

export async function probeAccountCapabilities() {
  return httpRequest<{ created: number; updated: number; removed: number; total: number }>("/api/accounts/capabilities/probe-batch", { method: "POST" });
}

export async function updateAccountCapability(accountKey: string, updates: Partial<AccountCapability>) {
  return httpRequest<{ item: AccountCapability }>(`/api/accounts/${encodeURIComponent(accountKey)}/capabilities`, {
    method: "POST",
    body: updates,
  });
}

export async function fetchProxies() {
  return httpRequest<{ items: ProxyNode[] }>("/api/proxies");
}

export async function upsertProxy(body: { proxy: string; country?: string; provider?: string; asn?: string }) {
  return httpRequest<{ item: ProxyNode }>("/api/proxies", { method: "POST", body });
}

export async function reportProxyEvent(proxy: string, code: string, statusCode?: number) {
  return httpRequest<{ item: ProxyNode | null }>("/api/proxies/events", {
    method: "POST",
    body: { proxy, code, status_code: statusCode },
  });
}

export async function rebuildProxyScores() {
  return httpRequest<{ rebuilt: number; total: number }>("/api/proxies/rebuild", { method: "POST" });
}

export async function deleteProxies(body: { ids?: string[]; statuses?: string[] } = {}) {
  return httpRequest<{ removed: number; kept: number }>("/api/proxies/delete", { method: "POST", body });
}

export async function pruneProxies(body: { min_score?: number; min_failures?: number; include_cooldown?: boolean } = {}) {
  return httpRequest<{ removed: number; kept: number; removed_ids: string[] }>("/api/proxies/prune", { method: "POST", body });
}

export async function testProxyPool(body: { limit?: number; include_dead?: boolean } = {}) {
  return httpRequest<{ tested: number; ok: number; failed: number; items: Array<Record<string, unknown>> }>("/api/proxies/test-pool", { method: "POST", body });
}

export async function fetchUnifiedTasks(filters: { limit?: number; type?: string; status?: string } = {}) {
  const params = new URLSearchParams();
  params.set("limit", String(filters.limit ?? 300));
  if (filters.type) params.set("type", filters.type);
  if (filters.status) params.set("status", filters.status);
  return httpRequest<{ items: UnifiedTask[] }>(`/api/tasks?${params.toString()}`);
}

export async function syncUnifiedTasks() {
  return httpRequest<{ upserted: number; total: number }>("/api/tasks/sync", { method: "POST" });
}

export async function bulkDeleteUnifiedTasks(ids: string[] = [], terminalOnly = true) {
  return httpRequest<{ removed: number; kept: number }>("/api/tasks/bulk-delete", {
    method: "POST",
    body: { ids, terminal_only: terminalOnly },
  });
}

export async function fetchConversations(params: { limit?: number; include_messages?: boolean } = {}) {
  const query = new URLSearchParams();
  query.set("limit", String(params.limit ?? 100));
  if (params.include_messages) query.set("include_messages", "true");
  return httpRequest<{ items: Conversation[] }>(`/api/conversations?${query.toString()}`);
}

export async function createConversation(body: { title?: string; model?: string; source?: string; metadata?: Record<string, unknown> } = {}) {
  const response = await httpRequest<{ item: Conversation }>("/api/conversations", { method: "POST", body });
  return response.item;
}

export async function fetchConversation(conversationId: string) {
  const response = await httpRequest<{ item: Conversation }>(`/api/conversations/${encodeURIComponent(conversationId)}`);
  return response.item;
}

export async function updateConversation(conversationId: string, updates: Partial<Pick<Conversation, "title" | "model" | "status" | "source" | "metadata">>) {
  const response = await httpRequest<{ item: Conversation }>(`/api/conversations/${encodeURIComponent(conversationId)}`, {
    method: "POST",
    body: updates,
  });
  return response.item;
}

export async function deleteConversation(conversationId: string) {
  return httpRequest<{ removed: number }>(`/api/conversations/${encodeURIComponent(conversationId)}`, { method: "DELETE" });
}

export async function appendConversationMessage(conversationId: string, message: { role: string; content: string; metadata?: Record<string, unknown> }) {
  const response = await httpRequest<{ item: Conversation }>(`/api/conversations/${encodeURIComponent(conversationId)}/messages`, {
    method: "POST",
    body: message,
  });
  return response.item;
}

export function getConversationExportUrl(conversationId: string, format: "json" | "markdown" = "markdown") {
  const params = new URLSearchParams();
  params.set("format", format);
  return `/api/conversations/${encodeURIComponent(conversationId)}/export?${params.toString()}`;
}

export async function sendChatMessage(body: { conversation_id?: string; model?: string; messages: Array<{ role: string; content: string }>; stream?: boolean }) {
  return httpRequest<ChatCompletionResponse>("/v1/chat/completions", {
    method: "POST",
    body,
  });
}

export async function createVideoGenerationTask(body: { client_task_id?: string; prompt: string; model?: string; size?: string; seconds?: number }) {
  return httpRequest<VideoTask>("/v1/videos/generations", { method: "POST", body });
}

export async function fetchVideoTasks(ids: string[] = []) {
  const params = new URLSearchParams();
  if (ids.length) params.set("ids", ids.join(","));
  params.set("_t", String(Date.now()));
  return httpRequest<{ items: VideoTask[]; missing_ids: string[] }>(`/v1/videos?${params.toString()}`);
}

export async function fetchVideoTask(taskId: string) {
  return httpRequest<VideoTask>(`/v1/videos/${encodeURIComponent(taskId)}`);
}

export async function cancelVideoTask(taskId: string) {
  return httpRequest<VideoTask>(`/v1/videos/${encodeURIComponent(taskId)}/cancel`, {
    method: "POST",
    body: {},
  });
}

export function getVideoContentUrl(taskId: string) {
  return `/v1/videos/${encodeURIComponent(taskId)}/content`;
}
