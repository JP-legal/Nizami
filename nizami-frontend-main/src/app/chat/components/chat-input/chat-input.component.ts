import {Component, effect, ElementRef, input, OnDestroy, OnInit, output, signal, viewChild, WritableSignal} from '@angular/core';
import {FormControl, FormGroup, ReactiveFormsModule, Validators} from '@angular/forms';
import {IsTypingService} from '../../services/is-typing.service';
import {ChatInputService} from '../../services/chat-input.service';
import {NgClass} from '@angular/common';
import {FileModel, MessageModel, UploadInitResponse} from '../../models/message.model';
import {MessagesService} from '../../services/messages.service';
import {catchError, EMPTY, switchMap} from 'rxjs';
import {UntilDestroy, untilDestroyed} from '@ngneat/until-destroy';
import {FileUploadingProgressComponent} from '../file-uploading-progress/file-uploading-progress.component';
import {IconButtonComponent} from '../../../common/components/icon-button/icon-button.component';
import {NgIcon} from '@ng-icons/core';
import {ChatSideBarService} from '../../services/chat-side-bar.service';
import {TranslatePipe, TranslateService} from '@ngx-translate/core';
import {ToastrService} from 'ngx-toastr';
import {marker} from '@colsen1991/ngx-translate-extract-marker';
import {SpeechToTextErrorCode, SpeechToTextService} from '../../services/speech-to-text.service';
import {AuthService} from '../../../auth/services/auth.service';

@UntilDestroy()
@Component({
  imports: [
    ReactiveFormsModule,
    NgClass,
    FileUploadingProgressComponent,
    IconButtonComponent,
    NgIcon,
    TranslatePipe
  ],
  selector: 'app-chat-input',
  standalone: true,
  styleUrl: './chat-input.component.scss',
  templateUrl: './chat-input.component.html'
})
export class ChatInputComponent implements OnInit, OnDestroy {
  textarea = viewChild<ElementRef<HTMLTextAreaElement>>('input');
  filesInput = viewChild<ElementRef<HTMLInputElement>>('filesInput')

  disabled = input(false);

  uploadingFilesCount = 0;

  files: WritableSignal<{
    file: FileModel;
    progress: number;
    error?: any;
  }>[] = [];

  onNewMessage = output<MessageModel>();
  form = new FormGroup({
    text: new FormControl<string>('', [Validators.required, Validators.minLength(1)]),
  });

  /** Last recognition-only segment applied; removed from the end of the field before each new update when it still matches. */
  private speechLastLiveSession = '';

  constructor(
    private isTypingService: IsTypingService,
    private chatInputService: ChatInputService,
    private messageService: MessagesService,
    public sidebar: ChatSideBarService,
    private toastr: ToastrService,
    private translate: TranslateService,
    private auth: AuthService,
    protected speechToText: SpeechToTextService,
  ) {
    this.chatInputService.textareaControl = this.form.controls.text;

    effect(() => {
      const isDisabled = this.disabled();

      if (isDisabled) {
        this.form.disable();
      } else {
        this.form.enable();
      }
    });
  }

  get isTyping() {
    return this.isTypingService.value;
  }

  ngOnInit() {
    this.chatInputService.textarea = this.textarea;
    if(this.filesInput()) {
      this.chatInputService.filesInput.set(this.filesInput()!.nativeElement);
    }

    this.speechToText.liveSessionTranscript$
      .pipe(untilDestroyed(this))
      .subscribe((sessionLive) => {
        this.applySpeechSessionLive(sessionLive);
      });

    this.speechToText.errors$
      .pipe(untilDestroyed(this))
      .subscribe((code) => {
        const key = this.voiceErrorTranslationKey(code);
        if (key != null) {
          this.toastr.error(this.translate.instant(key));
        }
      });
  }

  ngOnDestroy() {
    this.speechLastLiveSession = '';
    this.speechToText.stop();
  }

  get micDisabled(): boolean {
    // Do not gate the mic on isTyping(): if ngx-typed-writer never fires completeLoop
    // (e.g. HTML edge cases in production), isTyping can stay true and the mic stays dead.
    return (
      this.disabled() ||
      this.form.disabled ||
      this.uploadingFilesCount > 0
    );
  }

  toggleDictation() {
    if (this.micDisabled) {
      return;
    }
    if (!this.speechToText.isSupported()) {
      this.toastr.error(this.translate.instant(marker('voice_input.not_supported')));
      return;
    }
    if (this.speechToText.isListening()) {
      this.speechLastLiveSession = '';
      this.speechToText.stop();
      return;
    }
    this.speechLastLiveSession = '';
    const speechLang = this.speechToText.mapAppLangToSpeechLang(this.resolveSpeechAppLangCode());
    this.speechToText.start(speechLang);
  }

  /**
   * Prefer the signed-in user's profile language so dictation matches French/Hindi/Urdu
   * even when the UI translator is still on ar/en only.
   */
  private resolveSpeechAppLangCode(): string {
    const fromProfile = this.auth.user()?.language?.trim();
    if (fromProfile) {
      return fromProfile;
    }
    return this.translate.currentLang || this.translate.getDefaultLang() || 'ar';
  }

  /**
   * Re-applies the live transcript on top of whatever is in the textarea now: if the previous
   * session text is still at the end, it is replaced; otherwise the whole field is treated as
   * the prefix so edits while speaking stay in place.
   */
  private applySpeechSessionLive(sessionLive: string): void {
    const ctrl = this.form.controls.text;
    const current = ctrl.value ?? '';
    let prefix = current;

    if (this.speechLastLiveSession.length > 0 && current.endsWith(this.speechLastLiveSession)) {
      prefix = current.slice(0, current.length - this.speechLastLiveSession.length);
    }

    const next = sessionLive
      ? this.combineDictationPrefixAndLive(prefix, sessionLive)
      : prefix;

    this.speechLastLiveSession = sessionLive;
    ctrl.setValue(next);
    this.editingText();
  }

  /** Joins text that was already in the field with the live recognition stream (Arabic + Latin). */
  private combineDictationPrefixAndLive(prefix: string, live: string): string {
    if (!live) {
      return prefix;
    }
    if (!prefix) {
      return live;
    }
    const prefixEndsSpace = /\s$/u.test(prefix);
    const liveStartsSpace = /^\s/u.test(live);
    if (prefixEndsSpace || liveStartsSpace) {
      return prefix + live;
    }
    return `${prefix} ${live}`;
  }

  private voiceErrorTranslationKey(code: SpeechToTextErrorCode): string | null {
    switch (code) {
      case 'not_supported':
        return marker('voice_input.not_supported');
      case 'not_allowed':
        return marker('voice_input.permission_denied');
      case 'no_speech':
        return marker('voice_input.no_speech');
      case 'network':
        return marker('voice_input.network_error');
      case 'service_not_allowed':
        return marker('voice_input.service_not_allowed');
      case 'generic':
        return marker('voice_input.recognition_error');
      default:
        return null;
    }
  }

  sendMessage() {
    if (this.form.invalid) {
      return;
    }
    const text = this.form.controls.text.value;
    if (text == null || text.trim().length < 1) {
      return;
    }

    if (this.uploadingFilesCount > 0) {
      return;
    }

    this.onNewMessage.emit({
      id: null,
      text: this.form.value.text!,
      messageFiles: this.files?.map((item) => item().file),
    });

    this.form.reset();
    this.files = [];
    if (this.filesInput()) {
      this.filesInput()!.nativeElement!.value = '';
    }

    this.editingText();
  }

  editingText() {
    const textarea = this.textarea()!.nativeElement;

    const rowHeight = parseInt(window.getComputedStyle(textarea).lineHeight, 10);
    if (!textarea.value) {
      textarea.style.height = `${rowHeight}px`;
      return;
    }

    const scrollHeight = textarea.scrollHeight;

    const maxRows = 5;
    const maxHeight = rowHeight * maxRows;

    textarea.style.height = `${Math.min(scrollHeight, maxHeight)}px`;
  }

  focus() {
    this.chatInputService.focusTextArea();
  }

  addAttachment(filesInput: HTMLInputElement) {
    this.chatInputService.filesInput?.set(filesInput!);
    this.chatInputService.addAttachment();
  }

  stopTyping() {
    this.isTypingService.stopTyping();
  }

  onFilesSelected($event: Event) {
    const input = $event.target as HTMLInputElement;
    if (input.files) {
      const incomingFiles = Array.from(input.files);
      const remainingSlots = 5 - this.files.length;

      if (remainingSlots <= 0) {
        this.toastr.error(
          this.translate.instant(
            marker('errors.max_files_count_reached'),
          ),
        );
        return;
      }

      const filesToProcess = incomingFiles.slice(0, remainingSlots);

      if (incomingFiles.length > remainingSlots) {
        this.toastr.error(
          this.translate.instant(
            marker('errors.max_files_count_reached'),
          ),
        );
      }

      filesToProcess.forEach((rawFile) => {
        const maxSizeBytes = 20 * 1024 * 1024; // 20 MB
        const allowedExtensions = ['pdf', 'doc', 'docx'];
        const allowedMimeTypes = [
          'application/pdf',
          'application/msword',
          'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        ];

        const fileName = rawFile.name ?? '';
        const extension = fileName.split('.').pop()?.toLowerCase() ?? '';
        const mimeType = rawFile.type ?? '';

        if (rawFile.size > maxSizeBytes) {
          this.toastr.error(
            this.translate.instant(
              marker('errors.file_too_large'),
            ),
          );
          return;
        }

        if (!allowedExtensions.includes(extension) && !allowedMimeTypes.includes(mimeType)) {
          this.toastr.error(
            this.translate.instant(
              marker('errors.file_type_not_allowed'),
            ),
          );
          return;
        }

        const file: FileModel = {
          file: rawFile,
        };

        this.uploadingFilesCount++;

        this.uploadFile(file);
      });
    }
  }

  removeFile(index: number, file: FileModel) {
    this.files.splice(index, 1);
    this.uploadingFilesCount--;

    // Legacy uploads (numeric id) have a server-side file to remove
    if (file.id != null && typeof file.id === 'number') {
      this.messageService
        .removeMessageFile(file.id)
        .pipe(untilDestroyed(this))
        .subscribe();
    }
  }

  tryAgain(i: number, file: FileModel) {
    const file$ = this.files[i];

    file$.update((f) => {
      return {
        ...f,
        error: null,
        progress: 0,
      };
    });

    this.uploadRawFile(file, file$);
  }

  private uploadFile(file: FileModel) {
    const file$ = signal({
      file: file,
      progress: 0,
      error: null,
    });

    this.uploadRawFile(file, file$);

    this.files.push(file$);
  }

  private static readonly ALLOWED_UPLOAD_MIME_TYPES = [
    'application/pdf',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  ] as const;

  private static readonly EXT_TO_MIME: Record<string, string> = {
    'pdf': 'application/pdf',
    'doc': 'application/msword',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  };

  private getUploadMimeType(file: File): string {
    if (ChatInputComponent.ALLOWED_UPLOAD_MIME_TYPES.includes(file.type as any)) {
      return file.type;
    }
    const ext = file.name.split('.').pop()?.toLowerCase();
    if (ext && ext in ChatInputComponent.EXT_TO_MIME) {
      return ChatInputComponent.EXT_TO_MIME[ext];
    }
    throw new Error('Unsupported file type. Allowed: PDF, DOC, DOCX.');
  }

  /**
   * New attachment flow: init (on file select) -> PUT to presigned URL -> complete.
   * Reused files (dedupe) skip PUT and complete.
   */
  private uploadRawFile(file: FileModel, file$: WritableSignal<{ file: FileModel; progress: number; error?: any }>) {
    const rawFile = file.file!;
    let mimeType: string;
    try {
      mimeType = this.getUploadMimeType(rawFile);
    } catch (e) {
      file$.update((f) => ({ ...f, progress: 0, error: e }));
      this.uploadingFilesCount--;
      return;
    }
    const metadata = {
      file_name: rawFile.name,
      file_size: rawFile.size,
      mime_type: mimeType,
      store_in_library: false,
    };

    this.messageService
      .initUpload(metadata)
      .pipe(
        untilDestroyed(this),
        switchMap((initResponse) => {
          if ('reused' in initResponse && initResponse.reused) {
            file$.set({
              file: {
                id: initResponse.file_id,
                file_name: rawFile.name,
                size: rawFile.size,
              },
              progress: 100,
              error: null,
            });
            this.uploadingFilesCount--;
            return EMPTY;
          }
          const { upload_id, upload_url, required_headers } = initResponse as UploadInitResponse;
          file$.update((f) => ({ ...f, progress: 10 }));
          return this.messageService
            .uploadToPresignedUrl(upload_url, rawFile, required_headers || {})
            .pipe(
              switchMap(() => {
                file$.update((f) => ({ ...f, progress: 80 }));
                return this.messageService.completeUpload(upload_id);
              }),
            );
        }),
        catchError((e) => {
          file$.update((f) => ({ ...f, progress: 0, error: e }));
          this.uploadingFilesCount--;
          return [];
        }),
      )
      .subscribe((completeResponse: any) => {
        if (completeResponse && completeResponse.file_id) {
          file$.set({
            file: {
              id: completeResponse.file_id,
              file_name: rawFile.name,
              size: rawFile.size,
            },
            progress: 100,
            error: null,
          });
          this.uploadingFilesCount--;
        }
      });
  }

  prevent($event: any) {
    $event.preventDefault();
    $event.stopPropagation();
  }
}
