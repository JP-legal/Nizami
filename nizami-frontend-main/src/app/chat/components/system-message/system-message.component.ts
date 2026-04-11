import {Component, ElementRef, input, OnDestroy, signal} from '@angular/core';
import {CopyButtonComponent} from "../copy-button/copy-button.component";
import {FileModel, LegalAnswerMetadataJson, MessageModel} from '../../models/message.model';
import {ChatSystemProfileComponent} from '../chat-system-profile/chat-system-profile.component';
import {NgxTypedWriterComponent} from 'ngx-typed-writer';
import {marked} from 'marked';
import {IsTypingService} from '../../services/is-typing.service';
import {UserMessageFileComponent} from "../user-message-file/user-message-file.component";
import {MessagesService} from '../../services/messages.service';
import {UntilDestroy, untilDestroyed} from '@ngneat/until-destroy';
import {take} from 'rxjs';
import {SafeHtmlPipe} from '../../../common/pipes/safe-html.pipe';

interface CitationPopupState {
  citation: NonNullable<LegalAnswerMetadataJson['citations']>[0];
  top: number;
  left: number;
}

@UntilDestroy()
@Component({
  selector: 'app-system-message',
  imports: [
    CopyButtonComponent,
    ChatSystemProfileComponent,
    NgxTypedWriterComponent,
    UserMessageFileComponent,
    SafeHtmlPipe,
  ],
  templateUrl: './system-message.component.html',
  styleUrl: './system-message.component.scss'
})
export class SystemMessageComponent implements OnDestroy {
  message = input.required<MessageModel>();
  showCursor = signal(false);
  isLast = input<boolean>(false);
  citationPopup = signal<CitationPopupState | null>(null);

  private hideTimeout: ReturnType<typeof setTimeout> | null = null;

  constructor(
    public elementRef: ElementRef,
    private isTypingService: IsTypingService,
    private messagesService: MessagesService,
  ) {
  }

  ngOnDestroy(): void {
    if (this.hideTimeout) clearTimeout(this.hideTimeout);
  }

  get isTyping() {
    return this.isTypingService.value;
  }

  get text(): string {
    const html = marked(this.message().text.trim(), {async: false}) as string;
    const hasCitations = (this.message().metadata_json?.citations?.length ?? 0) > 0;
    if (!hasCitations) return html;
    return html.replace(/\[(\d+)\]/g, '<button class="cite-ref" data-ref="$1">[$1]</button>');
  }

  onAnswerMouseOver(event: MouseEvent): void {
    const target = event.target as HTMLElement;
    if (!target.classList.contains('cite-ref')) return;

    if (this.hideTimeout) {
      clearTimeout(this.hideTimeout);
      this.hideTimeout = null;
    }

    const ref = parseInt(target.getAttribute('data-ref') ?? '', 10);
    if (isNaN(ref)) return;

    const citations = this.message().metadata_json?.citations ?? [];
    const citation = citations.find(c => c.label === `[${ref}]`);
    if (!citation) return;

    const rect = target.getBoundingClientRect();
    const tooltipWidth = 300;
    let left = rect.left + rect.width / 2 - tooltipWidth / 2;
    if (left + tooltipWidth > window.innerWidth - 16) left = window.innerWidth - tooltipWidth - 16;
    if (left < 16) left = 16;

    this.citationPopup.set({ citation, top: rect.top, left });
  }

  onAnswerMouseLeave(event: MouseEvent): void {
    const related = event.relatedTarget as HTMLElement | null;
    if (related?.closest?.('.citation-popup')) return;
    this.scheduleHide();
  }

  onPopupMouseEnter(): void {
    if (this.hideTimeout) {
      clearTimeout(this.hideTimeout);
      this.hideTimeout = null;
    }
  }

  onPopupMouseLeave(): void {
    this.scheduleHide();
  }

  private scheduleHide(): void {
    this.hideTimeout = setTimeout(() => {
      this.citationPopup.set(null);
      this.hideTimeout = null;
    }, 120);
  }

  isNotSpecified(text: string | undefined | null): boolean {
    if (!text) return true;
    const t = text.trim();
    if (!t) return true;
    return t.includes('غير محدد') || t.includes('غير موجود') || t.toLowerCase().includes('not specified') || t.toLowerCase().includes('not available');
  }

  writingDone() {
    this.isTypingService.stopTyping();
  }

  clicked(file: FileModel) {
    this
      .messagesService
      .downloadFile(file)
      .pipe(
        untilDestroyed(this),
        take(1),
      )
      .subscribe()
  }
}
