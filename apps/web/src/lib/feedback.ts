// Questions and notices for the user, callable from anywhere (hooks included); drawn by
// <Feedback /> in App with the app's own dialog instead of the browser's blocking ones.

export interface ConfirmRequest {
  id: number;
  title: string;
  message?: string;
  confirmLabel?: string;
  destructive?: boolean;
  resolve: (ok: boolean) => void;
}

export interface Toast {
  id: number;
  tone: "error" | "warning" | "info";
  message: string;
}

interface State {
  confirm: ConfirmRequest | null;
  toasts: Toast[];
}

const TOAST_MS = 8000;
let state: State = { confirm: null, toasts: [] };
let nextId = 1;
const listeners = new Set<() => void>();

function set(next: Partial<State>) {
  state = { ...state, ...next };
  listeners.forEach((listener) => listener());
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function snapshot(): State {
  return state;
}

/** Ask before an expensive or irreversible action; a newer question cancels an open one. */
export function confirmAction(request: Omit<ConfirmRequest, "id" | "resolve">): Promise<boolean> {
  state.confirm?.resolve(false);
  return new Promise((resolve) => set({ confirm: { ...request, id: nextId++, resolve } }));
}

export function answer(ok: boolean) {
  const current = state.confirm;
  if (!current) return;
  set({ confirm: null });
  current.resolve(ok);
}

export function notify(message: string, tone: Toast["tone"] = "error") {
  const toast = { id: nextId++, tone, message };
  set({ toasts: [...state.toasts, toast].slice(-4) });
  setTimeout(() => dismiss(toast.id), TOAST_MS);
}

export function dismiss(id: number) {
  if (state.toasts.some((toast) => toast.id === id)) {
    set({ toasts: state.toasts.filter((toast) => toast.id !== id) });
  }
}
