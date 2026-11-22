/**
 * Review queue — shows only documents with status needs_review.
 * Sorted by age (oldest first) so nothing gets forgotten.
 */

import { useNavigate } from "react-router-dom";
import { AlertCircle, ChevronRight } from "lucide-react";
import StatusBadge from "../components/StatusBadge";
import ConfidencePill from "../components/ConfidencePill";
import { useDocuments } from "../hooks/useDocuments";
import type { Document } from "../types";

export default function ReviewQueuePage() {
  const navigate = useNavigate();
  const { data, isLoading } = useDocuments(1, "needs_review");

  const docs = data?.items ?? [];

  return (
    <div className="p-8">
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center gap-3 mb-6">
          <AlertCircle className="w-6 h-6 text-yellow-500" />
          <div>
            <h2 className="text-2xl font-bold text-gray-900">Review Queue</h2>
            <p className="text-sm text-gray-500">
              {data?.total ?? 0} document{data?.total !== 1 ? "s" : ""} awaiting review
            </p>
          </div>
        </div>

        {isLoading ? (
          <div className="text-center py-16 text-gray-400">Loading queue…</div>
        ) : docs.length === 0 ? (
          <div className="text-center py-16">
            <AlertCircle className="w-12 h-12 text-green-400 mx-auto mb-3" />
            <p className="text-gray-500 font-medium">Queue is empty</p>
            <p className="text-sm text-gray-400 mt-1">All documents have been reviewed.</p>
          </div>
        ) : (
          <div className="space-y-3">
            {docs.map((doc: Document) => (
              <button
                key={doc.id}
                onClick={() => navigate(`/documents/${doc.id}`)}
                className="w-full bg-white border border-gray-200 rounded-xl p-4 hover:border-blue-300 hover:shadow-sm transition-all text-left group"
              >
                <div className="flex items-center justify-between">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <StatusBadge status={doc.status} />
                      <span className="text-xs text-gray-400 capitalize">
                        {doc.doc_type.replace(/_/g, " ")}
                      </span>
                    </div>
                    <p className="font-medium text-gray-900 truncate">{doc.original_filename}</p>
                    <div className="flex items-center gap-3 mt-2 text-xs text-gray-400">
                      <span>Uploaded {new Date(doc.created_at).toLocaleDateString()}</span>
                      {doc.overall_confidence != null && (
                        <span className="flex items-center gap-1">
                          Confidence: <ConfidencePill value={doc.overall_confidence} />
                        </span>
                      )}
                      {doc.grounding_rate != null && (
                        <span>Grounding: {Math.round(doc.grounding_rate * 100)}%</span>
                      )}
                      {doc.total_cost_usd != null && (
                        <span>${doc.total_cost_usd.toFixed(4)}</span>
                      )}
                    </div>
                  </div>
                  <ChevronRight className="w-5 h-5 text-gray-300 group-hover:text-gray-500 shrink-0 ml-4 transition-colors" />
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
