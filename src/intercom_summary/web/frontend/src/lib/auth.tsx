import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, User } from "./api";

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  /** Turn a stored login username into the name people know it by. */
  displayName: (username?: string | null) => string;
}

const AuthCtx = createContext<AuthState>({
  user: null,
  loading: true,
  login: async () => {},
  logout: async () => {},
  displayName: (u) => u ?? "",
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  // username → display name, for rows that were written with a username (grade
  // overrides, comment authors, disputes…). Empty until loaded, and it stays empty
  // if the fetch fails — every lookup then falls back to the raw username.
  const [names, setNames] = useState<Record<string, string>>({});

  const loadNames = useCallback(() => {
    api
      .get<Record<string, string>>("/api/users/display-names")
      .then(setNames)
      .catch(() => setNames({}));
  }, []);

  useEffect(() => {
    api
      .get<User>("/api/auth/me")
      .then((u) => {
        setUser(u);
        loadNames();
      })
      .catch(() => setUser(null))
      .finally(() => setLoading(false));
  }, [loadNames]);

  const login = async (username: string, password: string) => {
    const u = await api.post<User>("/api/auth/login", { username, password });
    setUser(u);
    loadNames();
  };
  const logout = async () => {
    await api.post("/api/auth/logout");
    setUser(null);
    setNames({});
  };

  const displayName = useCallback(
    (username?: string | null) => (username ? names[username] || username : ""),
    [names],
  );

  return (
    <AuthCtx.Provider value={{ user, loading, login, logout, displayName }}>
      {children}
    </AuthCtx.Provider>
  );
}

export const useAuth = () => useContext(AuthCtx);
/** Just the name resolver, for components that do not care who is signed in. */
export const useDisplayName = () => useContext(AuthCtx).displayName;
export const canWrite = (role?: string) => role === "admin" || role === "analyst";
