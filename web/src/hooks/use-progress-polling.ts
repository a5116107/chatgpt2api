import { useCallback, useEffect, useRef } from "react";
import { pollProgress, type ProgressState } from "@/lib/progress-polling";

export function useProgressPolling() {
  const state = useRef({ disposed: false, controllers: new Set<AbortController>() });
  useEffect(() => {
    const scope = state.current;
    scope.disposed = false;
    return () => {
      scope.disposed = true;
      for (const controller of scope.controllers) controller.abort();
      scope.controllers.clear();
    };
  }, []);
  return useCallback(
    async <T extends ProgressState>(
      read: (signal: AbortSignal) => Promise<T>,
      onUpdate: (value: T) => void,
    ) => {
      const scope = state.current;
      if (scope.disposed) throw new DOMException("页面已离开", "AbortError");
      const controller = new AbortController();
      scope.controllers.add(controller);
      try {
        return await pollProgress(read, onUpdate, controller.signal);
      } finally {
        scope.controllers.delete(controller);
      }
    },
    [],
  );
}
