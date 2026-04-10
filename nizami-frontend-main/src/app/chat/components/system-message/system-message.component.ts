import {Component, ElementRef, input, signal} from '@angular/core';
import {CopyButtonComponent} from "../copy-button/copy-button.component";
import {FileModel, MessageModel} from '../../models/message.model';
import {ChatSystemProfileComponent} from '../chat-system-profile/chat-system-profile.component';
import {NgxTypedWriterComponent} from 'ngx-typed-writer';
import {marked} from 'marked';
import {IsTypingService} from '../../services/is-typing.service';
import {UserMessageFileComponent} from "../user-message-file/user-message-file.component";
import {MessagesService} from '../../services/messages.service';
import {UntilDestroy, untilDestroyed} from '@ngneat/until-destroy';
import {take} from 'rxjs';
import {SafeHtmlPipe} from '../../../common/pipes/safe-html.pipe';
import {AnswerMetadataComponent} from '../answer-metadata/answer-metadata.component';

@UntilDestroy()
@Component({
  selector: 'app-system-message',
  imports: [
    CopyButtonComponent,
    ChatSystemProfileComponent,
    NgxTypedWriterComponent,
    UserMessageFileComponent,
    SafeHtmlPipe,
    AnswerMetadataComponent,
  ],
  templateUrl: './system-message.component.html',
  styleUrl: './system-message.component.scss'
})
export class SystemMessageComponent {
  message = input.required<MessageModel>();
  showCursor = signal(false);
  isLast = input<boolean>(false);
  activeCitation = signal<number | null>(null);

  constructor(
    public elementRef: ElementRef,
    private isTypingService: IsTypingService,
    private messagesService: MessagesService,
  ) {
  }

  get isTyping() {
    return this.isTypingService.value;
  }

  get text(): string {
    const html = marked(this.message().text.trim(), {async: false}) as string;
    if (!this.hasAnswerMetadata()) return html;
    // Wrap inline citation markers like [8] with a clickable button so the
    // user can tap them to expand the matching entry in the metadata panel.
    return html.replace(/\[(\d+)\]/g, '<button class="cite-ref" data-ref="$1">[$1]</button>');
  }

  onAnswerClick(event: MouseEvent): void {
    const target = event.target as HTMLElement;
    if (target.classList.contains('cite-ref')) {
      const ref = parseInt(target.getAttribute('data-ref') ?? '', 10);
      if (!isNaN(ref)) {
        // Toggle: clicking the same ref again closes the panel entry.
        this.activeCitation.set(this.activeCitation() === ref ? null : ref);
      }
    }
  }

  hasAnswerMetadata(): boolean {
    const m = this.message().metadata_json;
    if (!m) {
      return false;
    }
    return (
      (m.citations?.length ?? 0) > 0 ||
      (m.dates_mentioned?.length ?? 0) > 0 ||
      (m.numbers_and_percentages?.length ?? 0) > 0 ||
      (m.statistics_from_context?.length ?? 0) > 0
    );
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
