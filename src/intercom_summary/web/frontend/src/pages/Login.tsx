import { useState } from "react";
import { useAuth } from "@/lib/auth";
import { Button, Input } from "@/components/ui/primitives";
import { BarChart3 } from "lucide-react";

export default function Login() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(username, password);
    } catch (err: any) {
      setError(err.message || "Login failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-screen">
      {/* Left: form */}
      <div className="flex w-full flex-col justify-center px-8 md:w-1/2 lg:px-24">
        <div className="mx-auto w-full max-w-sm">
          <div className="mb-8 flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-md bg-primary text-primary-foreground text-sm font-bold">
              QA
            </div>
            <span className="text-lg font-semibold">Intercom QA Dashboard</span>
          </div>
          <h1 className="text-2xl font-bold">Welcome back</h1>
          <p className="mt-1 text-sm text-muted-foreground">Sign in to review support conversations.</p>

          <form onSubmit={submit} className="mt-8 space-y-4">
            <div>
              <label className="mb-1 block text-sm font-medium">Username</label>
              <Input value={username} onChange={(e) => setUsername(e.target.value)} autoFocus />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium">Password</label>
              <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
            </div>
            {error && <p className="text-sm text-destructive">{error}</p>}
            <Button type="submit" className="w-full" disabled={busy}>
              {busy ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        </div>
      </div>

      {/* Right: visual panel */}
      <div className="hidden w-1/2 items-center justify-center bg-gradient-to-br from-indigo-500 to-indigo-700 md:flex">
        <div className="max-w-md px-12 text-white">
          <BarChart3 className="mb-6 h-12 w-12 opacity-90" />
          <h2 className="text-3xl font-bold leading-tight">
            Read, slice, and QA-grade your Intercom conversations.
          </h2>
          <p className="mt-4 text-indigo-100">
            One dashboard to fetch agent conversations, score them against your support
            ruleset, and spot coaching opportunities.
          </p>
        </div>
      </div>
    </div>
  );
}
