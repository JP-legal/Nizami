import {Injectable, signal, NgZone, OnDestroy} from '@angular/core';

@Injectable({
  providedIn: 'root'
})
export class IsTypingService implements OnDestroy {
  value = signal(false);
  private visibilityHandler: () => void;
  /** Clears stuck "typing" if completeLoop never runs (typed-writer + HTML edge cases). */
  private typingFailsafeTimer: ReturnType<typeof setTimeout> | null = null;
  private static readonly TYPING_FAILSAFE_MS = 180_000;

  constructor(private ngZone: NgZone) {
    // Listen for visibility changes to handle tab switching
    this.visibilityHandler = () => {
      if (document.hidden && this.value()) {
        // Tab became hidden while typing - complete animation immediately
        this.ngZone.run(() => {
          this.stopTyping();
        });
      }
    };

    document.addEventListener('visibilitychange', this.visibilityHandler);
  }

  ngOnDestroy() {
    document.removeEventListener('visibilitychange', this.visibilityHandler);
    this.clearTypingFailsafe();
  }

  startTyping() {
    // Only start typing if the tab is visible
    if (!document.hidden) {
      this.value.set(true);
      this.armTypingFailsafe();
    }
  }

  stopTyping() {
    this.clearTypingFailsafe();
    this.value.set(false);
  }

  private armTypingFailsafe(): void {
    this.clearTypingFailsafe();
    this.typingFailsafeTimer = setTimeout(() => {
      this.ngZone.run(() => {
        this.typingFailsafeTimer = null;
        this.value.set(false);
      });
    }, IsTypingService.TYPING_FAILSAFE_MS);
  }

  private clearTypingFailsafe(): void {
    if (this.typingFailsafeTimer != null) {
      clearTimeout(this.typingFailsafeTimer);
      this.typingFailsafeTimer = null;
    }
  }
}
