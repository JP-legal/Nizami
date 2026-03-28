import {Component, OnInit} from '@angular/core';
import {ActivatedRoute} from '@angular/router';
import {CommonModule} from '@angular/common';
import {SharedChatService, SharedChatExport} from './shared-chat.service';
import {MessageModel} from '../chat/models/message.model';
import {MessageComponent} from '../chat/components/message/message.component';

@Component({
  selector: 'app-shared-chat',
  standalone: true,
  imports: [CommonModule, MessageComponent],
  templateUrl: './shared-chat.component.html',
  styleUrl: './shared-chat.component.scss',
})
export class SharedChatComponent implements OnInit {
  export: SharedChatExport | null = null;
  loading = true;
  expired = false;
  error = false;

  messages: MessageModel[] = [];

  constructor(
    private route: ActivatedRoute,
    private service: SharedChatService,
  ) {}

  ngOnInit(): void {
    const exportId = this.route.snapshot.paramMap.get('exportId') ?? '';
    this.service.getExport(exportId).subscribe({
      next: (data) => {
        this.export = data;
        this.messages = (data.chat ?? []).map((msg, i) => ({
          id: i,
          text: msg.content,
          role: msg.role === 'assistant' ? 'system' : 'user',
          create_at: msg.timestamp,
        }));
        this.loading = false;
      },
      error: (err) => {
        this.loading = false;
        if (err.status === 410) {
          this.expired = true;
        } else {
          this.error = true;
        }
      },
    });
  }

  downloadPdf() {
    if (this.export?.pdf_url) {
      window.open(this.export.pdf_url, '_blank');
    }
  }
}
