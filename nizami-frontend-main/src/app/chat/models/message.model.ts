/** Structured RAG-backed fields from legal-advice JSON (see backend flow / LEGAL_ADVICE prompt). */
export interface LegalAnswerMetadataJson {
  citations?: Array<{
    label?: string;
    context_source_index?: number;
    source_title?: string;
    law_name?: string;
    law_number?: string;
    article_or_clause?: string;
    date_text?: string;
    reference?: string;
    excerpt?: string;
  }>;
  dates_mentioned?: Array<{
    date_text?: string;
    description?: string;
    context_source_index?: number;
  }>;
  numbers_and_percentages?: Array<{
    label?: string;
    value?: string;
    source_quote?: string;
  }>;
  statistics_from_context?: Array<{
    metric?: string;
    value?: string;
    unit_or_period?: string;
    source_quote?: string;
  }>;
}

export interface MessageModel {
  id: number | null;
  uuid?: string | null;
  text: string;
  role?: "system" | "user";
  chat_id?: any;
  create_at?: any;

  language?: string;
  show_translation_disclaimer?: boolean;
  translation_disclaimer_language?: string;

  /** Citations, dates, figures from RAG (assistant messages). */
  metadata_json?: LegalAnswerMetadataJson | null;

  messageFiles?: FileModel[] | null;
  message_file_ids?: number[] | null;
  /** New attachment flow: list of uploads.File UUIDs */
  attachment_file_ids?: string[] | null;
  /** New attachment flow: file summary per message (from list API, so attachments show after reload) */
  attachments?: FileModel[] | null;
}

export interface FileModel {
  /** Legacy: number; new uploads: UUID string */
  id?: number | string;
  file_name?: string;
  size?: any;
  extension?: string;

  file?: File;
}

/** Response from POST /v1/attachments/init when new upload is required */
export interface UploadInitResponse {
  upload_id: string;
  file_id: string;
  /** Presigned URL for upload — use PUT only (GET e.g. in browser returns 403). */
  upload_url: string;
  upload_method: 'PUT';
  required_headers: Record<string, string>;
}

/** Response from POST /v1/attachments/init when file is reused (dedupe) */
export interface UploadReusedResponse {
  file_id: string;
  reused: true;
}

/** Response from POST /v1/attachments/complete */
export interface UploadCompleteResponse {
  file_id: string;
  status: string;
}
