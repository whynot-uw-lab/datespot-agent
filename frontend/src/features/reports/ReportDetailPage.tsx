import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { getPersistedReport } from "../../api/runs";
import { ReportView } from "./ReportView";

export const ReportDetailPage = () => {
  const { runId = "" } = useParams();
  const query = useQuery({
    queryKey: ["persisted-report", runId],
    queryFn: () => getPersistedReport(runId),
    enabled: Boolean(runId),
  });

  if (query.isLoading) return <main className="loading-page">저장 리포트를 불러오는 중…</main>;
  if (query.isError) return <main className="error-page"><p>{query.error.message}</p><Link to="/app/reports">목록으로</Link></main>;
  if (!query.data) return null;
  return <main className="report-detail-page page-shell"><Link className="back-link" to="/app/reports">← 저장 리포트</Link><ReportView report={query.data} /></main>;
};
