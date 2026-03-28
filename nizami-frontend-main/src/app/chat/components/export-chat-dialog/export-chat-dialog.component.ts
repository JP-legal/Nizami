import {Component, input, output, signal} from '@angular/core';
import {TranslatePipe} from '@ngx-translate/core';

@Component({
  selector: 'app-export-chat-dialog',
  standalone: true,
  imports: [TranslatePipe],
  templateUrl: './export-chat-dialog.component.html',
  styleUrl: './export-chat-dialog.component.scss',
})
export class ExportChatDialogComponent {
  pdfUrl = input.required<string>();
  shareUrl = input.required<string>();

  onClose = output();

  linkCopied = signal(false);

  downloadPdf() {
    window.open(this.pdfUrl(), '_blank');
  }

  copyShareLink() {
    navigator.clipboard.writeText(this.shareUrl()).then(() => {
      this.linkCopied.set(true);
      setTimeout(() => this.linkCopied.set(false), 2000);
    });
  }

  close() {
    this.onClose.emit();
  }
}
