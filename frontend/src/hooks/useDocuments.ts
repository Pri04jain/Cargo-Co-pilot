/**
 * React Query hooks for all document-related API calls.
 * Centralising these keeps components clean and makes it easy to
 * add optimistic updates, pagination, or cache invalidation later.
 */

import { useEffect, useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import type {
  AuditResponse,
  Document,
  DocumentListResponse,
  DocumentStatus,
  FieldsResponse,
  RoutingResponse,
  ValidationResponse,
} from "../types";

// ── List & status ─────────────────────────────────────────────────────────────

export function useDocuments(page = 1, statusFilter?: DocumentStatus) {
  return useQuery<DocumentListResponse>({
    queryKey: ["documents", page, statusFilter],
    queryFn: () =>
      api
        .get("/api/v1/documents", {
          params: { page, page_size: 20, status_filter: statusFilter },
        })
        .then((r) => r.data),
    refetchInterval: (query) => {
      // query.state.data is the DocumentListResponse directly (not an infinite query)
      const items = query.state.data?.items;
      const hasActive = items?.some(
        (d: Document) => d.status === "queued" || d.status === "processing"
      );
      return hasActive ? 3000 : false;
    },
  });
}

export function useDocument(id: string | undefined) {
  return useQuery<Document>({
    queryKey: ["document", id],
    queryFn: () => api.get(`/api/v1/documents/${id}`).then((r) => r.data),
    enabled: !!id,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "processing" ? 3000 : false;
    },
  });
}

// ── Detail sub-resources ──────────────────────────────────────────────────────

export function useDocumentFields(id: string | undefined) {
  return useQuery<FieldsResponse>({
    queryKey: ["document-fields", id],
    queryFn: () => api.get(`/api/v1/documents/${id}/fields`).then((r) => r.data),
    enabled: !!id,
  });
}

export function useDocumentValidation(id: string | undefined) {
  return useQuery<ValidationResponse>({
    queryKey: ["document-validation", id],
    queryFn: () => api.get(`/api/v1/documents/${id}/validation`).then((r) => r.data),
    enabled: !!id,
  });
}

export function useDocumentRouting(id: string | undefined) {
  return useQuery<RoutingResponse>({
    queryKey: ["document-routing", id],
    queryFn: () => api.get(`/api/v1/documents/${id}/routing`).then((r) => r.data),
    enabled: !!id,
  });
}

export function useDocumentAudit(id: string | undefined) {
  return useQuery<AuditResponse>({
    queryKey: ["document-audit", id],
    queryFn: () => api.get(`/api/v1/documents/${id}/audit`).then((r) => r.data),
    enabled: !!id,
  });
}

// ── Review actions ────────────────────────────────────────────────────────────

export function useApproveDocument(documentId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (comment?: string) =>
      api.post(`/api/v1/documents/${documentId}/approve`, { comment }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["document", documentId] });
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: ["document-audit", documentId] });
    },
  });
}

export function useRejectDocument(documentId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (comment?: string) =>
      api.post(`/api/v1/documents/${documentId}/reject`, { comment }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["document", documentId] });
      qc.invalidateQueries({ queryKey: ["documents"] });
    },
  });
}

export function useCorrectField(documentId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      field_name: string;
      original_value?: string;
      corrected_value: string;
    }) => api.post(`/api/v1/documents/${documentId}/correct`, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["document-fields", documentId] });
      qc.invalidateQueries({ queryKey: ["document-audit", documentId] });
    },
  });
}

// ── SSE: real-time status stream ──────────────────────────────────────────────

/**
 * Subscribe to server-sent events for a document.
 * Automatically updates the React Query cache when the backend pushes a
 * status_update event, eliminating the need to poll for in-progress docs.
 *
 * The hook closes the SSE connection when the document reaches a terminal
 * status or when the component unmounts.
 */
export function useDocumentSSE(id: string | undefined) {
  const qc = useQueryClient();
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    if (!id) return;

    // Only open SSE for non-terminal statuses
    const cached = qc.getQueryData<Document>(["document", id]);
    const terminalStatuses = new Set(["approved", "rejected", "failed", "needs_review"]);
    if (cached && terminalStatuses.has(cached.status)) return;

    const token = JSON.parse(
      localStorage.getItem("cargo-copilot-auth") ?? "{}"
    )?.state?.token as string | undefined;

    // SSE doesn't support custom headers — pass token as query param
    const url = `/api/v1/documents/${id}/events${token ? `?token=${token}` : ""}`;
    const es = new EventSource(url);
    esRef.current = es;

    es.addEventListener("status_update", (e) => {
      try {
        const payload = JSON.parse(e.data) as Partial<Document>;
        // Merge the pushed fields into the cached document
        qc.setQueryData<Document>(["document", id], (prev) =>
          prev ? { ...prev, ...payload } : prev
        );
        // Refresh the list view badge too
        qc.invalidateQueries({ queryKey: ["documents"] });
      } catch {
        // malformed event — ignore
      }
    });

    es.addEventListener("done", () => {
      // Terminal status reached — close the stream and do a final fetch
      es.close();
      qc.invalidateQueries({ queryKey: ["document", id] });
      qc.invalidateQueries({ queryKey: ["documents"] });
    });

    es.onerror = () => {
      // Connection error — fall back to the existing 3s polling in useDocument
      es.close();
    };

    return () => {
      es.close();
      esRef.current = null;
    };
  }, [id, qc]);
}
