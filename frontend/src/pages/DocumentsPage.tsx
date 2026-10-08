import { useState, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { Upload, FileText, Clock, CheckCircle, XCircle, AlertCircle } from "lucide-react";
import { api } from "../lib/api";
import type { Document, DocumentListResponse, DocumentStatus } from "../types";

const STATUS_STYLES: Record<DocumentStatus, { color: string; icon: React.ElementType }> = {
  queued: { color: "text-gray-500 bg-gray-100", icon: Clock },
  processing: { color: "text-blue-600 bg-blue-100", icon: Clock },
  needs_review: { color: "text-yellow-700 bg-yellow-100", icon: AlertCircle },
  approved: { color: "text-green-700 bg-green-100", icon: CheckCircle },
  rejected: { color: "text-red-700 bg-red-100", icon: XCircle },
  failed: { color: "text-red-700 bg-red-100", icon: XCircle },
};

function StatusBadge({ status }: { status: DocumentStatus }) {
  const { color, icon: Icon } = STATUS_STYLES[status];
  return (
    <span className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-xs font-medium ${color}`}>
      <Icon className="w-3 h-3" />
      {status.replace("_", " ")}
    </span>
  );
}

export default function DocumentsPage() {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const { data, isLoading } = useQuery<DocumentListResponse>({
    queryKey: ["documents"],
    queryFn: () => api.get("/api/v1/documents").then((r) => r.data),
    refetchInterval: 5000, // poll every 5s to show live status updates
  });

  const uploadMutation = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return api.post("/api/v1/documents/upload", form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      setUploadError(null);
    },
    onError: (err: any) => {
      setUploadError(err.response?.data?.detail ?? "Upload failed");
    },
  });

  function handleFiles(files: FileList | null) {
    if (!files?.length) return;
    Array.from(files).forEach((f) => uploadMutation.mutate(f));
  }

  return (
    <div className="p-8">
      <div className="max-w-5xl mx-auto">
        <h2 className="text-2xl font-bold text-gray-900 mb-6">Documents</h2>

        {/* Upload dropzone */}
        <div
          onClick={() => fileInputRef.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(e) => { e.preventDefault(); setDragOver(false); handleFiles(e.dataTransfer.files); }}
          className={`border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition-colors mb-6 ${
            dragOver ? "border-blue-500 bg-blue-50" : "border-gray-300 hover:border-blue-400 hover:bg-gray-50"
          }`}
        >
          <Upload className="w-10 h-10 text-gray-400 mx-auto mb-3" />
          <p className="text-gray-600 font-medium">Drop shipping documents here</p>
          <p className="text-sm text-gray-400 mt-1">PDF, PNG, JPG, TIFF · max 50 MB</p>
          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            accept=".pdf,.png,.jpg,.jpeg,.tiff"
            multiple
            onChange={(e) => handleFiles(e.target.files)}
          />
        </div>

        {uploadError && (
          <div className="mb-4 p-3 bg-red-50 text-red-700 rounded-lg text-sm">{uploadError}</div>
        )}

        {uploadMutation.isPending && (
          <div className="mb-4 p-3 bg-blue-50 text-blue-700 rounded-lg text-sm">Uploading...</div>
        )}

        {/* Document list */}
        {isLoading ? (
          <div className="text-center py-16 text-gray-400">Loading documents...</div>
        ) : !data?.items.length ? (
          <div className="text-center py-16 text-gray-400">
            <FileText className="w-12 h-12 mx-auto mb-3 opacity-40" />
            <p>No documents yet. Upload one to get started.</p>
          </div>
        ) : (
          <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">File</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Type</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Status</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Confidence</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Cost</th>
                  <th className="text-left px-4 py-3 font-medium text-gray-600">Uploaded</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {data.items.map((doc: Document) => (
                  <tr key={doc.id} className="hover:bg-gray-50">
                    <td className="px-4 py-3 font-medium text-gray-900 max-w-xs truncate">
                      {doc.original_filename}
                    </td>
                    <td className="px-4 py-3 text-gray-500">
                      {doc.doc_type.replace("_", " ")}
                    </td>
                    <td className="px-4 py-3">
                      <StatusBadge status={doc.status} />
                    </td>
                    <td className="px-4 py-3 text-gray-500">
                      {doc.overall_confidence != null
                        ? `${(doc.overall_confidence * 100).toFixed(0)}%`
                        : "—"}
                    </td>
                    <td className="px-4 py-3 text-gray-500">
                      {doc.total_cost_usd != null
                        ? `$${doc.total_cost_usd.toFixed(4)}`
                        : "—"}
                    </td>
                    <td className="px-4 py-3 text-gray-400">
                      {new Date(doc.created_at).toLocaleDateString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
