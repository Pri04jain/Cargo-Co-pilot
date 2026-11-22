// ── Document ──────────────────────────────────────────────────────────────────

export type DocumentStatus =
  | "queued" | "processing" | "needs_review"
  | "approved" | "rejected" | "failed";

export type DocumentType =
  | "bill_of_lading" | "commercial_invoice" | "packing_list" | "unknown";

export interface Document {
  id: string;
  tenant_id: string;
  original_filename: string;
  file_size_bytes: number;
  mime_type: string;
  page_count: number | null;
  status: DocumentStatus;
  doc_type: DocumentType;
  overall_confidence: number | null;
  grounding_rate: number | null;
  total_tokens: number | null;
  total_cost_usd: number | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface DocumentListResponse {
  items: Document[];
  total: number;
  page: number;
  page_size: number;
}

// ── Evidence & Fields ─────────────────────────────────────────────────────────

export interface BBox {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface Evidence {
  page: number;
  snippet: string;
  bbox: BBox | null;
}

export type FieldStatus = "grounded" | "ungrounded" | "corrected";

export interface ExtractedField {
  id: string;
  field_name: string;
  value: string | null;
  normalized_value: string | null;
  confidence: number;
  evidence: Evidence | null;
  status: FieldStatus;
}

export interface ExtractionInfo {
  id: string;
  prompt_version: string;
  model: string;
  attempt: number;
  created_at: string;
}

export interface FieldsResponse {
  extraction: ExtractionInfo | null;
  fields: ExtractedField[];
}

// ── Validation ────────────────────────────────────────────────────────────────

export type ValidationStatus = "pass" | "fail";
export type ValidationSeverity = "info" | "warning" | "error";

export interface ValidationResult {
  id: string;
  check_name: string;
  status: ValidationStatus;
  severity: ValidationSeverity;
  message: string;
  field_name: string | null;
}

export interface ValidationResponse {
  results: ValidationResult[];
  error_count: number;
  warning_count: number;
}

// ── Routing ───────────────────────────────────────────────────────────────────

export type RoutingDecision = "auto_approve" | "manager_approval" | "exception_queue";

export interface RoutingResponse {
  id: string;
  decision: RoutingDecision;
  rule_id: string;
  reason: string;
  rules_version: string;
  evaluation_context: Record<string, unknown>;
  created_at: string;
}

// ── Audit ─────────────────────────────────────────────────────────────────────

export interface AuditEntry {
  id: string;
  actor: string;
  event_type: string;
  description: string;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface AuditResponse {
  entries: AuditEntry[];
}
