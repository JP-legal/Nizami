import {Injectable} from '@angular/core';
import {HttpClient} from '@angular/common/http';
import {Observable} from 'rxjs';
import {environment} from '../../environments/environment';

export interface SharedChatMessage {
  role: 'user' | 'assistant';
  content: string;
  timestamp?: string;
}

export interface SharedChatSummary {
  overview?: string;
  problem?: string;
  root_cause?: string;
  solution?: string;
  next_steps?: string[];
}

export interface SharedChatExport {
  export_id: string;
  created_at: string | null;
  chat: SharedChatMessage[];
  summary: SharedChatSummary;
  pdf_url: string | null;
}

@Injectable({providedIn: 'root'})
export class SharedChatService {
  constructor(private http: HttpClient) {}

  getExport(exportId: string): Observable<SharedChatExport> {
    return this.http.get<SharedChatExport>(
      `${environment.apiUrl}/v1/chats/exports/${exportId}`,
    );
  }
}
