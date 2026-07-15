import { useInfiniteQuery } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";

import type { ReportSummary } from "../../api/contracts";
import { getReports } from "../../api/runs";

const dateLabel = (value: string) =>
  new Intl.DateTimeFormat("ko-KR", {
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));

const ReportListCard = ({ report }: { report: ReportSummary }) => (
  <Link className="report-list-card" to={`/app/reports/${report.runId}`}>
    <div>
      <p className="eyebrow">{report.status === "completed" ? "COMPLETED" : "FAILED"}</p>
      <h2>{report.config.location}</h2>
      <p>{report.config.searchKeyword} · {dateLabel(report.createdAt)}</p>
    </div>
    <div className="report-list-count">
      <strong>{report.resultCount}</strong><span>places</span>
    </div>
    <span className="arrow-link" aria-hidden="true">↗</span>
  </Link>
);

export const ReportsPage = () => {
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = {
    status: searchParams.get("status") ?? "",
    location: searchParams.get("location") ?? "",
    searchKeyword: searchParams.get("searchKeyword") ?? "",
    dateFrom: searchParams.get("dateFrom") ?? "",
    dateTo: searchParams.get("dateTo") ?? "",
  };
  const [draft, setDraft] = useState(filters);
  const query = useInfiniteQuery({
    queryKey: ["reports", filters],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => getReports({ ...filters, cursor: pageParam }),
    getNextPageParam: (lastPage) => lastPage.nextCursor || undefined,
  });
  const reports = query.data?.pages.flatMap((page) => page.items) ?? [];
  const invalidCount = Math.max(
    0,
    ...(query.data?.pages.map((page) => page.invalidReportCount) ?? []),
  );

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const next = new URLSearchParams();
    for (const [key, value] of Object.entries(draft)) {
      if (value.trim()) next.set(key, value.trim());
    }
    setSearchParams(next);
  };

  return (
    <main className="reports-page page-shell">
      <header className="page-heading">
        <div><p className="eyebrow">ARCHIVE</p><h1>저장 리포트</h1></div>
        <p>이전에 찾은 장소와 평가 근거를 다시 확인함.</p>
      </header>

      <form className="report-filters" onSubmit={submit}>
        <label><span>상태</span><select value={draft.status} onChange={(event) => setDraft({ ...draft, status: event.target.value })}>
          <option value="">전체</option><option value="completed">완료</option><option value="failed">실패</option>
        </select></label>
        <label><span>지역</span><input value={draft.location} onChange={(event) => setDraft({ ...draft, location: event.target.value })} placeholder="예: 성수역" /></label>
        <label><span>검색어</span><input value={draft.searchKeyword} onChange={(event) => setDraft({ ...draft, searchKeyword: event.target.value })} placeholder="예: 이탈리안" /></label>
        <label><span>시작일</span><input type="date" value={draft.dateFrom} onChange={(event) => setDraft({ ...draft, dateFrom: event.target.value })} /></label>
        <label><span>종료일</span><input type="date" value={draft.dateTo} onChange={(event) => setDraft({ ...draft, dateTo: event.target.value })} /></label>
        <button type="submit" className="filter-button">필터 적용</button>
      </form>

      {invalidCount ? <p className="archive-notice">손상된 리포트 {invalidCount}개는 제외됨</p> : null}
      {query.isLoading ? <div className="loading-surface">리포트를 불러오는 중…</div> : null}
      {query.isError ? <div className="error-surface" role="alert">{query.error.message}</div> : null}
      {!query.isLoading && !query.isError && reports.length === 0 ? <div className="empty-surface">조건에 맞는 저장 리포트가 없음.</div> : null}
      <div className="report-list">{reports.map((report) => <ReportListCard key={report.runId} report={report} />)}</div>
      {query.hasNextPage ? <button className="secondary-button load-more" onClick={() => query.fetchNextPage()} disabled={query.isFetchingNextPage}>{query.isFetchingNextPage ? "불러오는 중…" : "더 보기"}</button> : null}
    </main>
  );
};
