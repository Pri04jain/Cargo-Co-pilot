/**
 * Document list page with upload dropzone.
 *
 * Upload flow:
 *   1. User drops or selects a file → multipart POST to /api/v1/documents/upload
 *   2. API returns immediately with document_id + task_id (202 Accepted)
 *   3. React Query polls the list every 3s and shows live status badges
 *
 * Clicking a row navigates to the document detail page.
 */

import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Upload, FileText, ChevronRight } from "lucide-react";
import StatusBadge from "../components/StatusBadge";
import ConfidencePill from "../components/ConfidencePill";
import { useDocuments } from "../hooks/useDocuments";
import { api } from "../lib/api";
import type { Document } from "../types";

export default function DocumentsPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);

  const { data, isLoading } = useDocuments();

  const uploadMut = useMutation({
    mutationFn: (file: File) => {
      const form = new FormData();
      form.append("file", file);
      return api.post("/api/v1/documents/upload", form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["documents"] });
      setUploadError(null);
    },
    onError: (err: any) => {
      setUploadError(err.response?.data?.detail ?? "Upload failed");
    },
  });

  function handleFiles(files: FileList | null) {
    if (!files?.length) return;
    Array.from(files).forEach((f) => uploadMut.mutate(f));
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
          onDrop={(e) => {
            e.preventDefault();
            setDragOver(false);
            handleFiles(e.dataTransfer.files);
          }}
          className={`border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition-colors mb-6 ${
            dragOver
              ? "border-blue-500 bg-blue-50"
              : "border-gray-300 hover:border-blue-400 hover:bg-gray-50"
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
          <div className="mb-4 p-3 bg-red-50 text-red-700 rounded-lg text-sm">
            {uploadError}
          </div>
        )}
        {uploadMut.isPending && (
          <div className="mb-4 p-3 bg-blue-50 text-blue-700 rounded-lg text-sm">
            Uploading…
          </div>
        )}

        {/* Document list */}
        {isLoading ? (
          <div className="text-center py-16 text-gray-400">Loading…</div>
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
                  <th className="w-8" />
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {data.items.map((doc: Document) => (
                  <tr
                    key={doc.id}
                    onClick={() => navigate(`/documents/${doc.id}`)}
                    className="hover:bg-gray-50 cursor-pointer transition-colors"
                  >
                    <td className="px-4 py-3 font-medium text-gray-900 max-w-xs">
                      <p className="truncate">{doc.original_filename}</p>
                    </td>
                    <td className="px-4 py-3 text-gray-500 capitalize whitespace-nowrap">
                      {doc.doc_type.replace(/_/g, " ")}
                    </td>
                    <td className="px-4 py-3">
                      <StatusBadge status={doc.status} />
                    </td>
                    <td className="px-4 py-3">
                      <ConfidencePill value={doc.overall_confidence} />
                    </td>
                    <td className="px-4 py-3 text-gray-500 whitespace-nowrap">
                      {doc.total_cost_usd != null
                        ? `$${doc.total_cost_usd.toFixed(4)}`
                        : "—"}
                    </td>
                    <td className="px-4 py-3 text-gray-400 whitespace-nowrap">
                      {new Date(doc.created_at).toLocaleDateString()}
                    </td>
                    <td className="px-4 py-3">
                      <ChevronRight className="w-4 h-4 text-gray-300" />
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
