/** Global "active" sim paper account. The authoritative account LIST lives on the server (fetched
 * via React Query — `api.paperAccounts`); this store holds only the user's currently-selected
 * account id, so it can never drift from the server. Account-aware widgets default to this id and
 * may override it per panel (Dockview panel params win when present).
 */

import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { PaperBook } from "../api/types";

/** The `book` token for a sim account id (account 1 keeps the historical bare `sim`). */
export function simBook(accountId: number): PaperBook {
  return accountId === 1 ? "sim" : (`sim:${accountId}` as PaperBook);
}

interface AccountsState {
  activeAccountId: number;
  setActiveAccount: (id: number) => void;
}

export const useAccounts = create<AccountsState>()(
  persist(
    (set) => ({
      activeAccountId: 1, // Default account always exists server-side
      setActiveAccount: (id) => set({ activeAccountId: id }),
    }),
    { name: "oft-accounts", version: 1 },
  ),
);

/** Convenience selector hook for the global active account id. */
export const useActiveAccountId = () => useAccounts((s) => s.activeAccountId);
