import type { RegisterMailProvider } from "@/lib/api";

export type MailProviderConfig = RegisterMailProvider;

export const mailProviderOptions = [
  { value: "cloudmail_gen", label: "cloudmail_gen" },
  { value: "cloudflare_temp_email", label: "cloudflare_temp_email" },
  { value: "mailfree", label: "mailfree (自建邮箱)" },
  { value: "tempmail_lol", label: "tempmail_lol" },
  { value: "dropmail", label: "dropmail (动态随机域名)" },
  { value: "moemail", label: "moemail" },
  { value: "inbucket", label: "inbucket_mail" },
  { value: "duckmail", label: "duckmail" },
  { value: "gptmail", label: "gptmail(未测试)" },
  { value: "yyds_mail", label: "yyds_mail" },
  { value: "ddg_mail", label: "ddg_mail (DDG邮箱+CF中转)" },
  { value: "outlook_external", label: "outlook_external (Outlook 外部 API)" },
  { value: "outlook_token", label: "outlook_token (Outlook/Hotmail 邮箱池)" },
] as const;

const mailfreeDefaultDomainIndices = [
  482, 1383, 1443, 1503, 1563, 1623, 1720, 1726, 1732,
];

const providerDefaults: Record<string, MailProviderConfig> = {
  cloudmail_gen: {
    api_base: "",
    admin_email: "",
    admin_password: "",
    domain: [],
    subdomain: [],
    email_prefix: "",
  },
  cloudflare_temp_email: { api_base: "", admin_password: "", domain: [] },
  mailfree: {
    api_base: "https://mailfree.cavanal.workers.dev",
    admin_password: "",
    domain_index: 4,
    domain_indices: mailfreeDefaultDomainIndices,
  },
  tempmail_lol: { api_key: "", domain: [], random_domain_attempts: 8 },
  dropmail: {
    api_base: "https://dropmail.me",
    token_lifetime: "1d",
    random_domain_attempts: 8,
  },
  moemail: { api_base: "", api_key: "", domain: [] },
  inbucket: { api_base: "", domain: [], random_subdomain: true },
  duckmail: { api_key: "", default_domain: "duckmail.sbs" },
  gptmail: { api_key: "", default_domain: "" },
  yyds_mail: {
    api_base: "https://maliapi.215.im/v1",
    api_key: "",
    domain: [],
    subdomain: "",
    wildcard: false,
  },
  ddg_mail: { ddg_token: "", cf_inbox_jwt: "", cf_domain: [], admin_password: "" },
  outlook_external: {
    api_base: "https://mail.acica.top",
    api_key: "",
    proxy: "",
    request_timeout: 30,
    group_id: 1,
    folder: "all",
    top: 20,
    server_otp_enabled: true,
    otp_wait_seconds: 120,
    otp_poll_interval: 3,
    otp_subject_contains: "",
    otp_from_contains: "openai",
    otp_keyword: "",
    otp_code_regex: "(?<!\\d)(\\d{6})(?!\\d)",
    prefer_alias: true,
    use_plus_alias: true,
    realtime_preflight: true,
    preflight_attempts: 8,
  },
  outlook_token: {
    mailboxes: "",
    mode: "graph",
    imap_host: "outlook.office365.com",
    message_limit: 10,
  },
};

export function mailProviderDefaults(providerName: string): MailProviderConfig {
  const selectedDefaults = providerDefaults[providerName];
  return selectedDefaults ? { ...selectedDefaults } : {};
}
