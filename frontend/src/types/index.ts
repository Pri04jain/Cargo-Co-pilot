export type DocumentStatus =
  | "queued"
  | "processing"
  | "needs_review"
  | "approved"
  | "rejected"
  | "failed";

export type DocumentType =
  | "bill_of_lading"
  | "commercial_invoice"
  | "packing_list"
  | "unknown";

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
