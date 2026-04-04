import {Injectable, NgZone, inject, signal} from '@angular/core';
import {Subject} from 'rxjs';

export type SpeechToTextErrorCode =
  | 'not_supported'
  | 'not_allowed'
  | 'no_speech'
  | 'network'
  | 'service_not_allowed'
  | 'generic';

@Injectable({
  providedIn: 'root',
})
export class SpeechToTextService {
  private readonly zone = inject(NgZone);

  readonly isListening = signal(false);

  /** Final + interim text for the current listening session only (no prefix from the textarea). */
  private readonly liveSessionTranscriptSubject = new Subject<string>();
  readonly liveSessionTranscript$ = this.liveSessionTranscriptSubject.asObservable();

  /** Accumulates finalized segments since the last `start()`. */
  private sessionAccumulatedFinal = '';

  private readonly errorSubject = new Subject<SpeechToTextErrorCode>();
  readonly errors$ = this.errorSubject.asObservable();

  private recognition: SpeechRecognition | null = null;

  isSupported(): boolean {
    if (typeof window === 'undefined') {
      return false;
    }
    // Chromium only exposes / allows speech recognition in a secure context (HTTPS or localhost).
    if (typeof globalThis.isSecureContext === 'boolean' && !globalThis.isSecureContext) {
      return false;
    }
    return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
  }

  /**
   * Maps app / ngx-translate language codes to BCP-47 tags for the Web Speech API.
   * Supports ar, en, fr, hi, ur (and common regional variants via the same base code).
   */
  mapAppLangToSpeechLang(appLang: string): string {
    const raw = (appLang || 'ar').trim().toLowerCase();
    const base = raw.split(/[-_]/)[0] ?? 'ar';
    return SpeechToTextService.APP_LANG_TO_BCP47[base] ?? 'en-US';
  }

  /**
   * BCP-47 tags for SpeechRecognition.lang. Use generic `fr` / `hi` where engines
   * are picky about regions (French often fails with `fr-FR` in some WebKit builds).
   */
  private static readonly APP_LANG_TO_BCP47: Readonly<Record<string, string>> = {
    ar: 'ar-SA',
    en: 'en-US',
    fr: 'fr',
    hi: 'hi-IN',
    ur: 'ur-PK',
  };

  start(lang: string): void {
    if (!this.isSupported()) {
      this.zone.run(() => this.errorSubject.next('not_supported'));
      return;
    }

    this.stopInternal(false);
    this.sessionAccumulatedFinal = '';

    const Ctor = window.SpeechRecognition ?? window.webkitSpeechRecognition;
    if (!Ctor) {
      this.zone.run(() => this.errorSubject.next('not_supported'));
      return;
    }

    const rec = new Ctor();
    rec.lang = lang;
    rec.continuous = true;
    rec.interimResults = true;

    rec.onstart = () => {
      this.zone.run(() => this.liveSessionTranscriptSubject.next(''));
    };

    rec.onresult = (event: SpeechRecognitionEvent) => {
      this.zone.run(() => {
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const row = event.results[i];
          if (row.isFinal) {
            this.sessionAccumulatedFinal += row[0]?.transcript ?? '';
          }
        }
        let interim = '';
        for (let i = 0; i < event.results.length; i++) {
          if (!event.results[i].isFinal) {
            interim += event.results[i][0]?.transcript ?? '';
          }
        }
        this.liveSessionTranscriptSubject.next(this.sessionAccumulatedFinal + interim);
      });
    };

    rec.onerror = (event: SpeechRecognitionErrorEvent) => {
      this.zone.run(() => {
        const code = event.error;
        if (code === 'aborted') {
          return;
        }
        if (code === 'not-allowed') {
          this.errorSubject.next('not_allowed');
        } else if (code === 'no-speech') {
          this.errorSubject.next('no_speech');
        } else if (code === 'network') {
          this.errorSubject.next('network');
        } else if (code === 'service-not-allowed') {
          this.errorSubject.next('service_not_allowed');
        } else {
          this.errorSubject.next('generic');
        }
        this.isListening.set(false);
        this.recognition = null;
      });
    };

    rec.onend = () => {
      this.zone.run(() => {
        this.isListening.set(false);
        this.recognition = null;
      });
    };

    this.recognition = rec;
    try {
      rec.start();
      this.zone.run(() => this.isListening.set(true));
    } catch {
      this.zone.run(() => {
        this.errorSubject.next('generic');
        this.isListening.set(false);
        this.recognition = null;
      });
    }
  }

  stop(): void {
    this.stopInternal(true);
  }

  private stopInternal(userInitiated: boolean): void {
    const rec = this.recognition;
    if (!rec) {
      return;
    }
    try {
      if (userInitiated) {
        rec.stop();
      } else {
        rec.abort();
      }
    } catch {
      this.recognition = null;
      this.isListening.set(false);
    }
  }
}
