import { useEffect, useState } from 'react';
import { Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { Sidebar } from './components/Sidebar';
import { HomePage } from './pages/HomePage';
import { ScanAdaptPage } from './pages/ScanAdaptPage';
import { CleanPage } from './pages/CleanPage';
import { InjectPage } from './pages/InjectPage';
import { CleanInjectPage } from './pages/CleanInjectPage';
import { AnchorsPage } from './pages/AnchorsPage';
import { OptimizePage } from './pages/OptimizePage';
import { PreviewPage } from './pages/PreviewPage';
import { TasksPage } from './pages/TasksPage';
import { SessionsPage } from './pages/SessionsPage';
import { NewSessionPage } from './pages/NewSessionPage';
import { SessionDetailPage } from './pages/SessionDetailPage';
import { PublishedPage } from './pages/PublishedPage';
import './app.css';
import { HelpWidget } from './components/HelpWidget';

type Theme = 'dark' | 'light';

export function App() {
  const [theme, setTheme] = useState<Theme>(() => {
    return (localStorage.getItem('theme') as Theme) || 'dark';
  });

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
  }, [theme]);

  const [collapsed, setCollapsed] = useState<boolean>(() => {
    return localStorage.getItem('sidebarCollapsed') === '1';
  });

  useEffect(() => {
    localStorage.setItem('sidebarCollapsed', collapsed ? '1' : '0');
  }, [collapsed]);

  const location = useLocation();
  // Страница конкретной сессии — во всю ширину (для крупного превью).
  const wide = /^\/sessions\/(?!new$)[^/]+$/.test(location.pathname);

  return (
    <div className={`app-layout${collapsed ? ' app-layout--collapsed' : ''}`}>
      <Sidebar
        collapsed={collapsed}
        onToggle={() => setCollapsed((v) => !v)}
        theme={theme}
        onToggleTheme={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
      />

      <main className="app-main">
        <div className={`app-content${wide ? ' app-content--wide' : ''}`}>
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/tasks"           element={<TasksPage />} />
            <Route path="/sessions"        element={<SessionsPage />} />
            <Route path="/sessions/new"    element={<NewSessionPage />} />
            <Route path="/sessions/:sid"   element={<SessionDetailPage />} />
            <Route path="/published"       element={<PublishedPage />} />
            <Route path="/scan-adapt"   element={<ScanAdaptPage />} />
            <Route path="/clean"        element={<CleanPage />} />
            <Route path="/inject"       element={<InjectPage />} />
            <Route path="/clean-inject" element={<CleanInjectPage />} />
            <Route path="/anchors"      element={<AnchorsPage />} />
            <Route path="/batch-widget" element={<Navigate to="/" replace />} />
            <Route path="/assets" element={<Navigate to="/scan-adapt" replace />} />
            <Route path="/optimize"      element={<OptimizePage />} />
            <Route path="/preview"       element={<PreviewPage />} />
          </Routes>
        </div>
      </main>
      <HelpWidget />
    </div>
  );
}
