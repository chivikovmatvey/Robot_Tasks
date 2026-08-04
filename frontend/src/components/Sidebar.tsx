import { NavLink } from 'react-router-dom';

// Ручные инструменты (/scan-adapt, /clean, /inject, /anchors, /optimize,
// /preview) из меню убраны — весь рабочий поток идёт через Сессии; сами
// страницы живы и доступны по прямым ссылкам.

interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
  theme: 'dark' | 'light';
  onToggleTheme: () => void;
}

export function Sidebar({ collapsed, onToggle, theme, onToggleTheme }: SidebarProps) {
  return (
    <aside className="sidebar">
      <div className="sidebar-header">
        <div className="logo">
          <div className="logo-mark" />
          <div className="logo-text">
            <div className="logo-name">Offer Processor</div>
            <div className="logo-subtitle">v0.1.0</div>
          </div>
        </div>
        <button
          className="sidebar-toggle"
          onClick={onToggle}
          title={collapsed ? 'Развернуть меню' : 'Свернуть меню'}
          aria-label={collapsed ? 'Развернуть меню' : 'Свернуть меню'}
        >
          {collapsed ? '»' : '«'}
        </button>
      </div>

      <nav className="sidebar-nav">
        <NavLink to="/" end className="nav-item" title="Главная">
          <span className="nav-item-icon">⌂</span>
          <span className="nav-item-text">
            <span className="nav-item-label">Главная</span>
            <span className="nav-item-desc">Состояние и быстрый старт</span>
          </span>
        </NavLink>

        <div className="nav-section">Работа</div>

        <NavLink to="/tasks" className="nav-item" title="Задачи">
          <span className="nav-item-icon">▤</span>
          <span className="nav-item-text">
            <span className="nav-item-label">Задачи</span>
            <span className="nav-item-desc">Пул Anyone + личные (mch)</span>
          </span>
        </NavLink>
        <NavLink to="/sessions" className="nav-item" title="Сессии">
          <span className="nav-item-icon">▶</span>
          <span className="nav-item-text">
            <span className="nav-item-label">Сессии</span>
            <span className="nav-item-desc">Адаптация лендов из задач</span>
          </span>
        </NavLink>
        <NavLink to="/published" className="nav-item" title="Опубликованные">
          <span className="nav-item-icon">✓</span>
          <span className="nav-item-text">
            <span className="nav-item-label">Опубликованные</span>
            <span className="nav-item-desc">Залитые ленды по дням/неделям</span>
          </span>
        </NavLink>

        {/* Переключатель темы — в самом низу меню */}
        <button
          type="button"
          className="nav-item nav-theme-toggle"
          onClick={onToggleTheme}
          title={`Переключить на ${theme === 'dark' ? 'светлую' : 'тёмную'} тему`}
        >
          <span className="nav-item-icon">{theme === 'dark' ? '☼' : '☾'}</span>
          <span className="nav-item-text">
            <span className="nav-item-label">{theme === 'dark' ? 'Светлая тема' : 'Тёмная тема'}</span>
            <span className="nav-item-desc">Переключить оформление</span>
          </span>
        </button>
      </nav>

      <div className="sidebar-footer">
        <a href="https://localhost:8000/docs" target="_blank" rel="noopener" className="dim">
          API docs ↗
        </a>
      </div>
    </aside>
  );
}
