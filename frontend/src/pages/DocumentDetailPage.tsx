/**
 * Document detail page — the core review interface.
 *
 * Layout:
 *   Left panel  (60%): PDF viewer with highlight overlay
 *   Right panel (40%): tabs for Fields, Validation, Routing, Audit
 *
 * Interaction:
 *   Clicking a field row → highlights its evidence bbox in the PDF viewer.
 *   Clicking "Edit" on a field → inline edit with save/cancel.
 */

import { useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  ArrowLeft, CheckCircle, XCircle, Edit3, Save, X,
  AlertTriangle, Info, Shield, Clock, ChevronRight,
} from "lucide-react";
import PdfViewer from "../components/PdfViewer";
import StatusBadge from "../components/StatusBadge";
import ConfidencePill from "../components/ConfidencePill";
import {
  useDocument,
  useDocumentFields,
  useDocumentValidation,
  useDocumentRouting,
  useDocumentAudit,
  useApproveDocument,
  useRejectDocument,
  useCorrectField,
  useDocumentSSE,
} from "../hooks/useDocuments";
import { api } from "../lib/api";
import type { ExtractedField, BBox } from "../types";

type Tab = "fields" | "validation" | "routing" | "audit";

interface Highlight {
  page: number;
  bbox: BBox;
  label: string;
  color: string;
}

export default function DocumentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [activeTab, setActiveTab] = useState<Tab>("fields");
  const [highlight, setHighlight] = useState<Highlight | null>(null);
  const [editingField, setEditingField] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [pageWidthPts, setPageWidthPts] = useState<number | undefined>();
  const [pageHeightPts, setPageHeightPts] = useState<number | undefined>();
  const [approveComment, setApproveComment] = useState("");
  const [rejectComment, setRejectComment] = useState("");
  const [showApproveModal, setShowApproveModal] = useState(false);
  const [showRejectModal, setShowRejectModal] = useState(false);

  const { data: doc, isLoading } = useDocument(id);
  const { data: fieldsData } = useDocumentFields(id);
  const { data: validation } = useDocumentValidation(id);
  const { data: routing } = useDocumentRouting(id);
  const { data: audit } = useDocumentAudit(id);

  // SSE for live status — replaces polling while document is in-progress
  useDocumentSSE(id);

  const approveMut = useApproveDocument(id!);
  const rejectMut = useRejectDocument(id!);
  const correctMut = useCorrectField(id!);

  // Fetch page dimensions for highlight scaling (page 1 on first load)
  const loadPageDimensions = async (pageNum: number) => {
    if (!id) return;
    try {
      const res = await api.get(`/api/v1/documents/${id}/page/${pageNum}`);
      setPageWidthPts(res.data.width);
      setPageHeightPts(res.data.height);
    } catch {
      // non-critical
    }
  };

  const handleFieldClick = (field: ExtractedField) => {
    if (field.evidence?.bbox && field.evidence.page) {
      setHighlight({
        page: field.evidence.page,
        bbox: field.evidence.bbox,
        label: field.field_name,
        color: "rgba(59, 130, 246, 0.25)",
      });
      loadPageDimensions(field.evidence.page);
    }
  };

  const handleSaveCorrection = async (field: ExtractedField) => {
    await correctMut.mutateAsync({
      field_name: field.field_name,
      original_value: field.value ?? undefined,
      corrected_value: editValue,
    });
    setEditingField(null);
  };

  const handleApprove = async () => {
    await approveMut.mutateAsync(approveComment || undefined);
    setShowApproveModal(false);
  };

  const handleReject = async () => {
    await rejectMut.mutateAsync(rejectComment || undefined);
    setShowRejectModal(false);
  };

  if (isLoading) {
    return <div className="p-8 text-gray-400">Loading document…</div>;
  }
  if (!doc) {
    return <div className="p-8 text-red-500">Document not found.</div>;
  }

  const fileUrl = `/api/v1/documents/${id}/file`;
  const isReviewable = doc.status === "needs_review";

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="border-b border-gray-200 px-6 py-4 flex items-center justify-between bg-white">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate("/documents")}
            className="text-gray-400 hover:text-gray-600 transition-colors"
          >
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div>
            <h2 className="font-semibold text-gray-900 truncate max-w-md">
              {doc.original_filename}
            </h2>
            <div className="flex items-center gap-2 mt-0.5">
              <StatusBadge status={doc.status} />
              <span className="text-xs text-gray-400">
                {doc.doc_type.replace(/_/g, " ")}
              </span>
              {doc.overall_confidence != null && (
                <ConfidencePill value={doc.overall_confidence} />
              )}
              {doc.total_cost_usd != null && (
                <span className="text-xs text-gray-400">
                  ${doc.total_cost_usd.toFixed(4)}
                </span>
              )}
            </div>
          </div>
        </div>

        {/* Review actions */}
        {isReviewable && (
          <div className="flex gap-2">
            <button
              onClick={() => setShowRejectModal(true)}
              className="flex items-center gap-1.5 px-4 py-2 border border-red-200 text-red-600 rounded-lg text-sm hover:bg-red-50 transition-colors"
            >
              <XCircle className="w-4 h-4" /> Reject
            </button>
            <button
              onClick={() => setShowApproveModal(true)}
              className="flex items-center gap-1.5 px-4 py-2 bg-green-600 text-white rounded-lg text-sm hover:bg-green-700 transition-colors"
            >
              <CheckCircle className="w-4 h-4" /> Approve
            </button>
          </div>
        )}
      </div>

      {/* Main 2-column layout */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left: PDF viewer */}
        <div className="w-3/5 border-r border-gray-200 overflow-auto p-4 bg-gray-50">
          <PdfViewer
            fileUrl={fileUrl}
            highlight={highlight}
            pageWidth={580}
            pageWidthPts={pageWidthPts}
            pageHeightPts={pageHeightPts}
            onPageCountChange={(n) => n === 1 && loadPageDimensions(1)}
          />
        </div>

        {/* Right: tabs */}
        <div className="w-2/5 flex flex-col overflow-hidden">
          {/* Tab bar */}
          <div className="flex border-b border-gray-200 bg-white">
            {(["fields", "validation", "routing", "audit"] as Tab[]).map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`px-4 py-3 text-sm font-medium capitalize transition-colors border-b-2 ${
                  activeTab === tab
                    ? "border-blue-600 text-blue-600"
                    : "border-transparent text-gray-500 hover:text-gray-700"
                }`}
              >
                {tab}
                {tab === "validation" && validation && validation.error_count > 0 && (
                  <span className="ml-1.5 bg-red-100 text-red-700 text-xs px-1.5 rounded-full">
                    {validation.error_count}
                  </span>
                )}
              </button>
            ))}
          </div>

          <div className="flex-1 overflow-auto p-4">
            {activeTab === "fields" && <FieldsTab fields={fieldsData?.fields ?? []}
              onFieldClick={handleFieldClick} selectedField={highlight?.label ?? null}
              onEdit={(f) => { setEditingField(f.field_name); setEditValue(f.value ?? ""); }}
              editingField={editingField} editValue={editValue}
              onEditChange={setEditValue}
              onSave={(f) => handleSaveCorrection(f)}
              onCancelEdit={() => setEditingField(null)}
            />}
            {activeTab === "validation" && <ValidationTab validation={validation} />}
            {activeTab === "routing" && <RoutingTab routing={routing} />}
            {activeTab === "audit" && <AuditTab audit={audit} />}
          </div>
        </div>
      </div>

      {/* Approve modal */}
      {showApproveModal && (
        <Modal title="Approve Document" onClose={() => setShowApproveModal(false)}>
          <textarea
            className="w-full border border-gray-300 rounded-lg p-3 text-sm resize-none h-24 focus:outline-none focus:ring-2 focus:ring-green-500"
            placeholder="Optional comment…"
            value={approveComment}
            onChange={(e) => setApproveComment(e.target.value)}
          />
          <div className="flex gap-2 justify-end mt-4">
            <button onClick={() => setShowApproveModal(false)} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800">Cancel</button>
            <button onClick={handleApprove} disabled={approveMut.isPending}
              className="px-4 py-2 bg-green-600 text-white text-sm rounded-lg hover:bg-green-700 disabled:opacity-50">
              {approveMut.isPending ? "Approving…" : "Confirm Approve"}
            </button>
          </div>
        </Modal>
      )}

      {/* Reject modal */}
      {showRejectModal && (
        <Modal title="Reject Document" onClose={() => setShowRejectModal(false)}>
          <textarea
            className="w-full border border-gray-300 rounded-lg p-3 text-sm resize-none h-24 focus:outline-none focus:ring-2 focus:ring-red-500"
            placeholder="Reason for rejection…"
            value={rejectComment}
            onChange={(e) => setRejectComment(e.target.value)}
          />
          <div className="flex gap-2 justify-end mt-4">
            <button onClick={() => setShowRejectModal(false)} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800">Cancel</button>
            <button onClick={handleReject} disabled={rejectMut.isPending}
              className="px-4 py-2 bg-red-600 text-white text-sm rounded-lg hover:bg-red-700 disabled:opacity-50">
              {rejectMut.isPending ? "Rejecting…" : "Confirm Reject"}
            </button>
          </div>
        </Modal>
      )}
    </div>
  );
}


// ── Sub-components ────────────────────────────────────────────────────────────

function FieldsTab({
  fields, onFieldClick, selectedField,
  onEdit, editingField, editValue, onEditChange, onSave, onCancelEdit,
}: {
  fields: ExtractedField[];
  onFieldClick: (f: ExtractedField) => void;
  selectedField: string | null;
  onEdit: (f: ExtractedField) => void;
  editingField: string | null;
  editValue: string;
  onEditChange: (v: string) => void;
  onSave: (f: ExtractedField) => void;
  onCancelEdit: () => void;
}) {
  if (!fields.length) return <EmptyState message="No fields extracted yet." />;

  const statusColor = (s: string) =>
    s === "grounded" ? "text-green-600" : s === "corrected" ? "text-blue-600" : "text-yellow-600";

  return (
    <div className="space-y-1">
      {fields.map((f) => {
        const isSelected = selectedField === f.field_name;
        const isEditing = editingField === f.field_name;
        return (
          <div
            key={f.id}
            onClick={() => !isEditing && onFieldClick(f)}
            className={`rounded-lg px-3 py-2.5 cursor-pointer transition-colors border ${
              isSelected ? "bg-blue-50 border-blue-200" : "bg-white border-gray-100 hover:bg-gray-50"
            }`}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="flex-1 min-w-0">
                <p className="text-xs text-gray-400 font-mono">{f.field_name}</p>
                {isEditing ? (
                  <div className="mt-1 flex gap-1">
                    <input
                      value={editValue}
                      onChange={(e) => onEditChange(e.target.value)}
                      onClick={(e) => e.stopPropagation()}
                      className="flex-1 border border-blue-300 rounded px-2 py-1 text-sm focus:outline-none focus:ring-1 focus:ring-blue-500"
                      autoFocus
                    />
                    <button onClick={(e) => { e.stopPropagation(); onSave(f); }}
                      className="p-1 text-green-600 hover:text-green-700"><Save className="w-4 h-4" /></button>
                    <button onClick={(e) => { e.stopPropagation(); onCancelEdit(); }}
                      className="p-1 text-gray-400 hover:text-gray-600"><X className="w-4 h-4" /></button>
                  </div>
                ) : (
                  <p className={`text-sm font-medium mt-0.5 truncate ${f.value ? "text-gray-900" : "text-gray-400 italic"}`}>
                    {f.value ?? "—"}
                  </p>
                )}
              </div>
              <div className="flex items-center gap-1.5 shrink-0">
                <ConfidencePill value={f.confidence} />
                <span className={`text-xs ${statusColor(f.status)}`}>●</span>
                {!isEditing && (
                  <button
                    onClick={(e) => { e.stopPropagation(); onEdit(f); }}
                    className="p-1 text-gray-300 hover:text-gray-500 transition-colors"
                  >
                    <Edit3 className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>
            </div>
            {f.evidence?.snippet && !isEditing && (
              <p className="text-xs text-gray-400 mt-1 truncate font-mono bg-gray-50 px-1.5 py-0.5 rounded">
                "{f.evidence.snippet}"
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}

function ValidationTab({ validation }: { validation: any }) {
  if (!validation) return <EmptyState message="No validation results yet." />;

  const { results, error_count, warning_count } = validation;

  const icon = (sev: string) => {
    if (sev === "error") return <XCircle className="w-4 h-4 text-red-500 shrink-0" />;
    if (sev === "warning") return <AlertTriangle className="w-4 h-4 text-yellow-500 shrink-0" />;
    return <Info className="w-4 h-4 text-blue-400 shrink-0" />;
  };

  return (
    <div>
      <div className="flex gap-3 mb-4">
        <div className="flex-1 bg-red-50 rounded-lg p-3 text-center">
          <p className="text-2xl font-bold text-red-700">{error_count}</p>
          <p className="text-xs text-red-500">Errors</p>
        </div>
        <div className="flex-1 bg-yellow-50 rounded-lg p-3 text-center">
          <p className="text-2xl font-bold text-yellow-700">{warning_count}</p>
          <p className="text-xs text-yellow-500">Warnings</p>
        </div>
        <div className="flex-1 bg-green-50 rounded-lg p-3 text-center">
          <p className="text-2xl font-bold text-green-700">
            {results.filter((r: any) => r.status === "pass").length}
          </p>
          <p className="text-xs text-green-500">Passed</p>
        </div>
      </div>

      <div className="space-y-2">
        {results
          .filter((r: any) => r.status === "fail")
          .map((r: any) => (
            <div key={r.id} className="flex gap-2 p-3 bg-white border border-gray-100 rounded-lg">
              {icon(r.severity)}
              <div className="min-w-0">
                <p className="text-sm font-medium text-gray-800">{r.message}</p>
                {r.field_name && (
                  <p className="text-xs text-gray-400 font-mono mt-0.5">{r.field_name}</p>
                )}
              </div>
            </div>
          ))}
        {results.filter((r: any) => r.status === "fail").length === 0 && (
          <p className="text-sm text-green-600 text-center py-4">All checks passed ✓</p>
        )}
      </div>
    </div>
  );
}

function RoutingTab({ routing }: { routing: any }) {
  if (!routing?.decision) return <EmptyState message="No routing decision yet." />;

  const decisionColor =
    routing.decision === "auto_approve" ? "bg-green-100 text-green-700"
    : routing.decision === "manager_approval" ? "bg-yellow-100 text-yellow-700"
    : "bg-red-100 text-red-700";

  return (
    <div className="space-y-4">
      <div className={`rounded-xl p-4 ${decisionColor}`}>
        <div className="flex items-center gap-2">
          <Shield className="w-5 h-5" />
          <p className="font-bold text-lg capitalize">{routing.decision.replace(/_/g, " ")}</p>
        </div>
        <p className="text-sm mt-1 opacity-80">{routing.reason}</p>
      </div>

      <div className="bg-white border border-gray-100 rounded-lg p-4 space-y-2">
        <DetailRow label="Rule fired" value={routing.rule_id} />
        <DetailRow label="Rules file" value={routing.rules_version} />
        <DetailRow label="Decided at" value={new Date(routing.created_at).toLocaleString()} />
      </div>

      {routing.evaluation_context?.evaluation_log && (
        <div>
          <p className="text-xs font-medium text-gray-500 mb-2">Evaluation log</p>
          <div className="space-y-1">
            {routing.evaluation_context.evaluation_log.map((entry: any, i: number) => (
              <div key={i} className="flex items-center gap-2 text-xs">
                <span className={entry.matched ? "text-green-600" : "text-gray-300"}>
                  {entry.matched ? "✓" : "·"}
                </span>
                <span className="font-mono text-gray-600">{entry.rule_id}</span>
                <span className="text-gray-400 truncate">{entry.when}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function AuditTab({ audit }: { audit: any }) {
  if (!audit?.entries?.length) return <EmptyState message="No audit events yet." />;

  return (
    <div className="space-y-3">
      {audit.entries.map((e: any, i: number) => (
        <div key={e.id} className="flex gap-3">
          <div className="flex flex-col items-center">
            <div className="w-7 h-7 rounded-full bg-blue-100 flex items-center justify-center shrink-0">
              <Clock className="w-3.5 h-3.5 text-blue-600" />
            </div>
            {i < audit.entries.length - 1 && (
              <div className="w-0.5 flex-1 bg-gray-100 mt-1" />
            )}
          </div>
          <div className="pb-3 flex-1 min-w-0">
            <div className="flex items-baseline gap-2">
              <p className="text-sm font-medium text-gray-800">{e.description || e.event_type}</p>
            </div>
            <p className="text-xs text-gray-400">
              {e.actor} · {new Date(e.created_at).toLocaleString()}
            </p>
          </div>
        </div>
      ))}
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between text-sm">
      <span className="text-gray-500">{label}</span>
      <span className="text-gray-800 font-mono text-right max-w-48 truncate">{value}</span>
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return <p className="text-sm text-gray-400 text-center py-8">{message}</p>;
}

function Modal({ title, children, onClose }: {
  title: string; children: React.ReactNode; onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md p-6">
        <div className="flex items-center justify-between mb-4">
          <h3 className="font-semibold text-gray-900">{title}</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
            <X className="w-5 h-5" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
