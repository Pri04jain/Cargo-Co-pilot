/**
 * Rules editor page.
 *
 * Left panel: YAML text editor with live validation feedback.
 * Right panel: dry-run panel — pick a document and see which rule fires.
 *
 * Live validation calls POST /api/v1/rules/validate on every change
 * with a 600ms debounce so we don't spam the server while typing.
 *
 * Dry-run calls POST /api/v1/rules/dry-run and shows the full evaluation
 * log — which rules were checked, which one matched, and why.
 */

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle, XCircle, Play, FileText, ChevronDown } from "lucide-react";
import { api } from "../lib/api";
import type { DocumentListResponse } from "../types";

export default function RulesPage() {
  const [selectedFile, setSelectedFile] = useState("default_rules.yaml");
  const [content, setContent] = useState("");
  const [validationState, setValidationState] = useState<{
    valid: boolean | null;
    error: string;
    ruleCount: number;
  }>({ valid: null, error: "", ruleCount: 0 });
  const [selectedDocId, setSelectedDocId] = useState("");
  const [dryRunResult, setDryRunResult] = useState<any>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Load list of rules files
  const { data: fileList } = useQuery<{ files: string[] }>({
    queryKey: ["rules-files"],
    queryFn: () => api.get("/api/v1/rules").then((r) => r.data),
  });

  // Load content of selected file
  const { data: fileContent, isLoading: fileLoading } = useQuery<{
    filename: string;
    content: string;
  }>({
    queryKey: ["rules-file", selectedFile],
    queryFn: () => api.get(`/api/v1/rules/${selectedFile}`).then((r) => r.data),
    enabled: !!selectedFile,
  });

  useEffect(() => {
    if (fileContent?.content) setContent(fileContent.content);
  }, [fileContent]);

  // Debounced live validation
  const handleContentChange = (value: string) => {
    setContent(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => validateContent(value), 600);
  };

  const validateContent = async (yaml: string) => {
    try {
      const res = await api.post("/api/v1/rules/validate", { content: yaml });
      setValidationState({
        valid: res.data.valid,
        error: res.data.error || "",
        ruleCount: res.data.rule_count || 0,
      });
    } catch {
      setValidationState({ valid: false, error: "Failed to validate", ruleCount: 0 });
    }
  };

  // Load documents for dry-run selector
  const { data: docsData } = useQuery<DocumentListResponse>({
    queryKey: ["documents-for-dryrun"],
    queryFn: () => api.get("/api/v1/documents", { params: { page_size: 50 } }).then((r) => r.data),
  });

  // Dry-run mutation
  const dryRunMut = useMutation({
    mutationFn: (docId: string) =>
      api
        .post("/api/v1/rules/dry-run", {
          document_id: docId,
          rules_file: selectedFile,
        })
        .then((r) => r.data),
    onSuccess: (data) => setDryRunResult(data),
  });

  const decisionColor = (d: string) =>
    d === "auto_approve"
      ? "bg-green-100 text-green-700"
      : d === "manager_approval"
      ? "bg-yellow-100 text-yellow-700"
      : "bg-red-100 text-red-700";

  return (
    <div className="p-8 h-full flex flex-col">
      <div className="max-w-7xl mx-auto flex-1 flex flex-col">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-2xl font-bold text-gray-900">Routing Rules</h2>
            <p className="text-sm text-gray-500 mt-1">
              Edit YAML rules — changes here apply immediately once deployed.
            </p>
          </div>

          {/* File selector */}
          <div className="relative">
            <select
              value={selectedFile}
              onChange={(e) => setSelectedFile(e.target.value)}
              className="appearance-none border border-gray-300 rounded-lg px-4 py-2 pr-8 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-blue-500"
            >
              {(fileList?.files ?? ["default_rules.yaml"]).map((f) => (
                <option key={f} value={f}>{f}</option>
              ))}
            </select>
            <ChevronDown className="absolute right-2 top-2.5 w-4 h-4 text-gray-400 pointer-events-none" />
          </div>
        </div>

        <div className="flex gap-6 flex-1 min-h-0">
          {/* Editor panel */}
          <div className="flex-1 flex flex-col">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-medium text-gray-700">YAML Editor</span>
              <div className="flex items-center gap-2">
                {validationState.valid === true && (
                  <span className="flex items-center gap-1 text-xs text-green-600">
                    <CheckCircle className="w-3.5 h-3.5" />
                    Valid · {validationState.ruleCount} rules
                  </span>
                )}
                {validationState.valid === false && (
                  <span className="flex items-center gap-1 text-xs text-red-600">
                    <XCircle className="w-3.5 h-3.5" />
                    Invalid
                  </span>
                )}
              </div>
            </div>

            <textarea
              value={content}
              onChange={(e) => handleContentChange(e.target.value)}
              className={`flex-1 font-mono text-sm border rounded-xl p-4 resize-none focus:outline-none focus:ring-2 bg-gray-950 text-green-400 leading-relaxed ${
                validationState.valid === false
                  ? "border-red-400 focus:ring-red-400"
                  : validationState.valid === true
                  ? "border-green-400 focus:ring-green-400"
                  : "border-gray-300 focus:ring-blue-500"
              }`}
              style={{ minHeight: "500px" }}
              spellCheck={false}
              placeholder={fileLoading ? "Loading…" : "# Paste YAML rules here"}
            />

            {validationState.valid === false && validationState.error && (
              <div className="mt-2 text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
                {validationState.error}
              </div>
            )}
          </div>

          {/* Dry-run panel */}
          <div className="w-80 flex flex-col">
            <p className="text-sm font-medium text-gray-700 mb-2">Dry Run</p>
            <div className="bg-white border border-gray-200 rounded-xl p-4 flex flex-col gap-3 flex-1">
              <p className="text-xs text-gray-500">
                Test the current rules against a processed document to see which
                rule would fire without making any changes.
              </p>

              <div>
                <label className="text-xs font-medium text-gray-600 block mb-1">
                  Select document
                </label>
                <select
                  value={selectedDocId}
                  onChange={(e) => setSelectedDocId(e.target.value)}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                >
                  <option value="">— choose a document —</option>
                  {(docsData?.items ?? []).map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.original_filename.slice(0, 35)}
                    </option>
                  ))}
                </select>
              </div>

              <button
                onClick={() => selectedDocId && dryRunMut.mutate(selectedDocId)}
                disabled={!selectedDocId || dryRunMut.isPending}
                className="flex items-center justify-center gap-2 bg-blue-600 text-white rounded-lg py-2 text-sm hover:bg-blue-700 disabled:opacity-50 transition-colors"
              >
                <Play className="w-4 h-4" />
                {dryRunMut.isPending ? "Running…" : "Run"}
              </button>

              {dryRunMut.isError && (
                <p className="text-xs text-red-500">Dry run failed. Is the document processed?</p>
              )}

              {/* Result */}
              {dryRunResult && (
                <div className="space-y-3 mt-2">
                  <div className={`rounded-lg px-3 py-2.5 ${decisionColor(dryRunResult.decision)}`}>
                    <p className="font-semibold capitalize text-sm">
                      {dryRunResult.decision.replace(/_/g, " ")}
                    </p>
                    <p className="text-xs opacity-75 mt-0.5">{dryRunResult.reason}</p>
                  </div>

                  <div>
                    <p className="text-xs font-medium text-gray-500 mb-1">Evaluation log</p>
                    <div className="space-y-1">
                      {(dryRunResult.evaluation_log ?? []).map((e: any, i: number) => (
                        <div key={i} className="flex items-start gap-2 text-xs">
                          <span
                            className={`mt-0.5 shrink-0 ${
                              e.matched ? "text-green-500" : "text-gray-300"
                            }`}
                          >
                            {e.matched ? "✓" : "·"}
                          </span>
                          <div className="min-w-0">
                            <span className="font-mono text-gray-700">{e.rule_id}</span>
                            <p className="text-gray-400 truncate">{e.when}</p>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
