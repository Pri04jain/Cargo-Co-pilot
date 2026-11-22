import { AlertCircle, CheckCircle, Clock, XCircle } from "lucide-react";
import type { DocumentStatus } from "../types";

const CONFIG: Record<DocumentStatus, { label: string; color: string; icon: React.ElementType }> = {
  queued:       { label: "Queued",        color: "text-gray-600 bg-gray-100",    icon: Clock },
  processing:   { label: "Processing",    color: "text-blue-700 bg-blue-100",    icon: Clock },
  needs_review: { label: "Needs Review",  color: "text-yellow-700 bg-yellow-100", icon: AlertCircle },
  approved:     { label: "Approved",      color: "text-green-700 bg-green-100",  icon: CheckCircle },
  rejected:     { label: "Rejected",      color: "text-red-700 bg-red-100",      icon: XCircle },
  failed:       { label: "Failed",        color: "text-red-700 bg-red-100",      icon: XCircle },
};

export default function StatusBadge({ status }: { status: DocumentStatus }) {
  const { label, color, icon: Icon } = CONFIG[status] ?? CONFIG.failed;
  return (
    <span className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium ${color}`}>
      <Icon className="w-3 h-3" />
      {label}
    </span>
  );
}
