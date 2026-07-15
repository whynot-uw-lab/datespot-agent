import { NavLink, Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";

import { NewRunPage } from "../features/new-run/NewRunPage";
import { ReportDetailPage } from "../features/reports/ReportDetailPage";
import { ReportsPage } from "../features/reports/ReportsPage";
import { RunProgressPage } from "../features/run-progress/RunProgressPage";

const AppShell = () => {
  const location = useLocation();
  const isRunning = location.pathname.startsWith("/app/runs/");
  return (
    <div className="app-shell">
      <header className="site-header">
        <NavLink className="brand" to="/app/" aria-label="DateSpot 홈">
          <span className="brand-mark">D</span><span>DateSpot</span>
        </NavLink>
        <nav aria-label="주요 메뉴">
          <NavLink to="/app/" end>새 탐색</NavLink>
          <span className={isRunning ? "active nav-status" : "nav-status"}>진행 중</span>
          <NavLink to="/app/reports">저장 리포트</NavLink>
        </nav>
        <NavLink className="header-cta" to="/app/">새 장소 찾기 <span aria-hidden="true">↗</span></NavLink>
      </header>
      <Outlet />
      <footer className="site-footer"><span>DateSpot</span><p>좋은 대화가 시작되는 장소를 찾음.</p><small>LOCAL CURATION AGENT</small></footer>
    </div>
  );
};

const NotFoundPage = () => <main className="error-page"><p>페이지를 찾을 수 없음.</p><NavLink to="/app/">홈으로</NavLink></main>;

export const App = () => (
  <Routes>
    <Route path="/" element={<Navigate replace to="/app/" />} />
    <Route path="/app" element={<AppShell />}>
      <Route index element={<NewRunPage />} />
      <Route path="runs/:runId" element={<RunProgressPage />} />
      <Route path="reports" element={<ReportsPage />} />
      <Route path="reports/:runId" element={<ReportDetailPage />} />
    </Route>
    <Route path="*" element={<NotFoundPage />} />
  </Routes>
);
