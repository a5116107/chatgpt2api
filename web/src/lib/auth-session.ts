"use client";

import { login } from "@/lib/api";
import { HttpRequestError } from "@/lib/request";
import {
  clearStoredAuthSession,
  getStoredAuthKey,
  getStoredAuthSession,
  setStoredAuthSession,
  type StoredAuthSession,
} from "@/store/auth";

let pendingValidation: Promise<StoredAuthSession | null> | null = null;

async function validateSession(): Promise<StoredAuthSession | null> {
  const storedSession = await getStoredAuthSession();
  if (!storedSession) return null;
  try {
    const data = await login(storedSession.key);
    if ((await getStoredAuthKey()) !== storedSession.key) return null;
    const nextSession: StoredAuthSession = {
      key: storedSession.key,
      role: data.role,
      subjectId: data.subject_id,
      name: data.name,
    };
    await setStoredAuthSession(nextSession);
    return nextSession;
  } catch (error) {
    if (error instanceof HttpRequestError && (error.status === 401 || error.status === 403)) {
      if ((await getStoredAuthKey()) === storedSession.key) await clearStoredAuthSession();
      return null;
    }
    throw error;
  }
}

export function getValidatedAuthSession(): Promise<StoredAuthSession | null> {
  if (!pendingValidation)
    pendingValidation = validateSession().finally(() => {
      pendingValidation = null;
    });
  return pendingValidation;
}
