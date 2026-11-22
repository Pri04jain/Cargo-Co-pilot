/**
 * Metrics page — shows eval harness results and live document stats.
 *
 * The accuracy/latency/cost numbers come from the eval harness (Phase 7).
 * Until Phase 7 runs, we show "run `make eval` to generate results" and
 * display only the live DB-sourced counts (documents by status, avg cost).
 *
 * Why keep this page now?
 *   - Proves the metrics exist as a first-class concern from Phase 1.
 *   - The live stats (document counts by status) are fully functional.
 *   - The eval section is an honest placeholder — no fabricated numbers.
 */

import { useQuery } from "@tanstack/react-query";
import { AlertCircle, BarChart3, CheckCircle, Clock, DollarSign, FileText, TrendingUp } from "lucide-react";
import { api } from "../lib/api";
import type { Document, DocumentListResponse } from "../types";

interface ValidationSummary {
  total_checks: number;
  failed_checks: number;
  error_count: number;
  warning_count: number;
  documents_with_errors: number;
}

interface FieldStat {
  field_name: string;
  grounded_count: number;
  total_count: number;
  avg_confidence: number;
}

export default function MetricsPage() {
  const { data: allDocs } = useQuery<DocumentListResponse>({
    queryKey: ["metrics-all"],
    queryFn: () =>
      api.get("/api/v1/documents", { params: { page_size: 100 } }).then((r) => r.data),
  });

  const docs: Document[] = allDocs?.items ?? [];

  const byStatus = {
    approved: docs.filter((d) => d.status === "approved").length,
    rejected: docs.filter((d) => d.status === "rejected").length,
    needs_review: docs.filter((d) => d.status === "needs_review").length,
    failed: docs.filter((d) => d.status === "failed").length,
  };

  const processed = docs.filter(
    (d) => d.status === "approved" || d.status === "needs_review" || d.status === "rejected"
  );
  const avgCost =
    processed.length > 0
      ? processed.reduce((s, d) => s + (d.total_cost_usd ?? 0), 0) / processed.length
      : null;
  const avgConf =
    processed.filter((d) => d.overall_confidence != null).length > 0
      ? processed.reduce((s, d) => s + (d.overall_confidence ?? 0), 0) /
        processed.filter((d) => d.overall_confidence != null).length
      : null;
  const avgGrounding =
    processed.filter((d) => d.grounding_rate != null).length > 0
      ? processed.reduce((s, d) => s + (d.grounding_rate ?? 0), 0) /
        processed.filter((d) => d.grounding_rate != null).length
      : null;

  return (
    <div className="p-8">
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center gap-3 mb-6">
          <BarChart3 className="w-6 h-6 text-blue-600" />
          <h2 className="text-2xl font-bold text-gray-900">Metrics</h2>
        </div>

        {/* Live stats */}
        <section className="mb-8">
          <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-4">
            Live — all documents
          </h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
            <StatCard
              label="Total documents"
              value={String(allDocs?.total ?? "—")}
              icon={<FileText className="w-5 h-5 text-blue-600" />}
              color="bg-blue-50"
            />
            <StatCard
              label="Avg confidence"
              value={avgConf != null ? `${Math.round(avgConf * 100)}%` : "—"}
              icon={<TrendingUp className="w-5 h-5 text-green-600" />}
              color="bg-green-50"
            />
            <StatCard
              label="Avg grounding"
              value={avgGrounding != null ? `${Math.round(avgGrounding * 100)}%` : "—"}
              icon={<TrendingUp className="w-5 h-5 text-purple-600" />}
              color="bg-purple-50"
            />
            <StatCard
              label="Avg cost / doc"
              value={avgCost != null ? `$${avgCost.toFixed(4)}` : "—"}
              icon={<DollarSign className="w-5 h-5 text-yellow-600" />}
              color="bg-yellow-50"
            />
          </div>

          {/* Status breakdown */}
          <div className="bg-white border border-gray-200 rounded-xl p-5">
            <p className="text-sm font-medium text-gray-700 mb-4">Decision breakdown</p>
            <div className="space-y-3">
              {Object.entries({
                "✅ Approved": { count: byStatus.approved, color: "bg-green-500" },
                "⚠️ Needs review": { count: byStatus.needs_review, color: "bg-yellow-500" },
                "❌ Rejected": { count: byStatus.rejected, color: "bg-red-500" },
                "💥 Failed": { count: byStatus.failed, color: "bg-gray-400" },
              }).map(([label, { count, color }]) => {
                const total = docs.length || 1;
                const pct = Math.round((count / total) * 100);
                return (
                  <div key={label}>
                    <div className="flex justify-between text-sm mb-1">
                      <span className="text-gray-600">{label}</span>
                      <span className="font-medium text-gray-900">{count}</span>
                    </div>
                    <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
                      <div
                        className={`h-full ${color} rounded-full transition-all duration-500`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </section>

        {/* Field-level stats from live DB */}
        <section>
          <h3 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-4">
            Field extraction quality
          </h3>
          <FieldStats docs={docs} />
        </section>
      </div>
    </div>
  );
}

function StatCard({
  label, value, icon, color,
}: {
  label: string; value: string; icon: React.ReactNode; color: string;
}) {
  return (
    <div className={`${color} rounded-xl p-4`}>
      <div className="flex items-center gap-2 mb-1">{icon}</div>
      <p className="text-2xl font-bold text-gray-900">{value}</p>
      <p className="text-xs text-gray-500 mt-0.5">{label}</p>
    </div>
  );
}

function FieldStats({ docs }: { docs: Document[] }) {
  const processedDocs = docs.filter(
    (d) => (d.status === "approved" || d.status === "needs_review" || d.status === "rejected")
      && d.doc_type !== "unknown"
  );

  if (processedDocs.length === 0) {
    return (
      <div className="bg-white border border-gray-200 rounded-xl p-8 text-center text-gray-400 text-sm">
        No processed documents yet. Upload and process a document to see stats.
      </div>
    );
  }

  const docTypes: Record<string, number> = {};
  processedDocs.forEach((d) => {
    const t = d.doc_type.replace(/_/g, " ");
    docTypes[t] = (docTypes[t] ?? 0) + 1;
  });

  const groundingValues = processedDocs
    .filter((d) => d.grounding_rate != null)
    .map((d) => d.grounding_rate!);
  const avgGrounding = groundingValues.length > 0
    ? groundingValues.reduce((a, b) => a + b, 0) / groundingValues.length
    : null;

  const confValues = processedDocs
    .filter((d) => d.overall_confidence != null)
    .map((d) => d.overall_confidence!);
  const avgConf = confValues.length > 0
    ? confValues.reduce((a, b) => a + b, 0) / confValues.length
    : null;

  return (
    <div className="space-y-4">
      {/* Document type breakdown */}
      <div className="bg-white border border-gray-200 rounded-xl p-5">
        <p className="text-sm font-medium text-gray-700 mb-4">Documents by type</p>
        <div className="space-y-3">
          {Object.entries(docTypes).map(([type, count]) => {
            const pct = Math.round((count / processedDocs.length) * 100);
            return (
              <div key={type}>
                <div className="flex justify-between text-sm mb-1">
                  <span className="text-gray-600 capitalize">{type}</span>
                  <span className="font-medium text-gray-900">{count}</span>
                </div>
                <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-blue-400 rounded-full transition-all duration-500"
                    style={{ width: `${pct}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Quality summary */}
      <div className="grid grid-cols-2 gap-4">
        <div className="bg-white border border-gray-200 rounded-xl p-5">
          <div className="flex items-center gap-2 mb-2">
            <CheckCircle className="w-4 h-4 text-green-500" />
            <p className="text-sm font-medium text-gray-700">Avg grounding rate</p>
          </div>
          <p className="text-3xl font-bold text-gray-900">
            {avgGrounding != null ? `${Math.round(avgGrounding * 100)}%` : "—"}
          </p>
          <p className="text-xs text-gray-400 mt-1">
            Fields matched to PDF locations
          </p>
        </div>
        <div className="bg-white border border-gray-200 rounded-xl p-5">
          <div className="flex items-center gap-2 mb-2">
            <TrendingUp className="w-4 h-4 text-blue-500" />
            <p className="text-sm font-medium text-gray-700">Avg confidence</p>
          </div>
          <p className="text-3xl font-bold text-gray-900">
            {avgConf != null ? `${Math.round(avgConf * 100)}%` : "—"}
          </p>
          <p className="text-xs text-gray-400 mt-1">
            Extraction confidence across all fields
          </p>
        </div>
      </div>

      {/* Processing summary */}
      <div className="bg-white border border-gray-200 rounded-xl p-5">
        <p className="text-sm font-medium text-gray-700 mb-3">Processing summary</p>
        <div className="grid grid-cols-3 gap-4 text-center">
          <div>
            <p className="text-2xl font-bold text-gray-900">{processedDocs.length}</p>
            <p className="text-xs text-gray-400 mt-0.5">Processed</p>
          </div>
          <div>
            <p className="text-2xl font-bold text-green-600">
              {docs.filter((d) => d.status === "approved").length}
            </p>
            <p className="text-xs text-gray-400 mt-0.5">Auto-approved</p>
          </div>
          <div>
            <p className="text-2xl font-bold text-yellow-600">
              {docs.filter((d) => d.status === "needs_review").length}
            </p>
            <p className="text-xs text-gray-400 mt-0.5">Needed review</p>
          </div>
        </div>
      </div>
    </div>
  );
}
