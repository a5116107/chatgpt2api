import type { ComponentType } from "react";

import { Input } from "@/components/ui";

import type {
  MailProviderConfig,
} from "./mail-provider-catalog";
import type { RegisterMailProviderValue } from "@/lib/api";

type ProviderFieldProps = {
  provider: MailProviderConfig;
  disabled: boolean;
  onChange: (patch: MailProviderConfig) => void;
};

type RandomDomainProviderFieldsProps = ProviderFieldProps & {
  providerName: string;
};

function optionalFieldText(value: RegisterMailProviderValue | undefined): string {
  return value == null ? "" : String(value);
}

function RandomDomainAttemptsField({ provider, disabled, onChange }: ProviderFieldProps) {
  return (
    <div className="space-y-2">
      <label className="text-sm text-stone-700">随机域名尝试次数</label>
      <Input
        type="number"
        min={1}
        max={20}
        value={Number(provider.random_domain_attempts || 8)}
        onChange={(event) => onChange({ random_domain_attempts: Number(event.target.value) })}
        className="h-10 rounded-xl border-stone-200 bg-white"
        disabled={disabled}
      />
    </div>
  );
}

function TempMailRandomDomainFields(props: ProviderFieldProps) {
  return <RandomDomainAttemptsField {...props} />;
}

function DropMailRandomDomainFields({ provider, disabled, onChange }: ProviderFieldProps) {
  return (
    <>
      <div className="space-y-2">
        <label className="text-sm text-stone-700">API Base</label>
        <Input
          value={optionalFieldText(provider.api_base)}
          onChange={(event) => onChange({ api_base: event.target.value })}
          className="h-10 rounded-xl border-stone-200 bg-white"
          disabled={disabled}
        />
      </div>
      <div className="space-y-2">
        <label className="text-sm text-stone-700">Token 生命周期</label>
        <Input
          value={String(provider.token_lifetime || "1d")}
          onChange={(event) => onChange({ token_lifetime: event.target.value })}
          className="h-10 rounded-xl border-stone-200 bg-white"
          disabled={disabled}
        />
      </div>
      <RandomDomainAttemptsField
        provider={provider}
        disabled={disabled}
        onChange={onChange}
      />
    </>
  );
}

const randomDomainEditors: Record<string, ComponentType<ProviderFieldProps>> = {
  tempmail_lol: TempMailRandomDomainFields,
  dropmail: DropMailRandomDomainFields,
};

export function RandomDomainProviderFields({
  providerName,
  provider,
  disabled,
  onChange,
}: RandomDomainProviderFieldsProps) {
  const ProviderFields = randomDomainEditors[providerName];
  return ProviderFields ? (
    <ProviderFields provider={provider} disabled={disabled} onChange={onChange} />
  ) : null;
}
