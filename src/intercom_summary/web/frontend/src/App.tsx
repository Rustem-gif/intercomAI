import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./lib/auth";
import { Spinner } from "./components/ui/primitives";
import AppShell from "./components/AppShell";
import Login from "./pages/Login";
import Overview from "./pages/Overview";
import Conversations from "./pages/Conversations";
import Agents from "./pages/Agents";
import Accuracy from "./pages/Accuracy";
import Ruleset from "./pages/Ruleset";
import Evaluation from "./pages/Evaluation";
import NeedsAttention from "./pages/NeedsAttention";
import KnowledgeBase from "./pages/KnowledgeBase";

export default function App() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <Spinner className="h-6 w-6 text-primary" />
      </div>
    );
  }

  if (!user) return <Login />;

  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Overview />} />
        <Route path="/conversations" element={<Conversations />} />
        <Route path="/agents" element={<Agents />} />
        <Route path="/accuracy" element={<Accuracy />} />
        <Route path="/ruleset" element={<Ruleset />} />
        <Route path="/evaluation" element={<Evaluation />} />
        <Route path="/needs-attention" element={<NeedsAttention />} />
        <Route path="/knowledge-base" element={<KnowledgeBase />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}
