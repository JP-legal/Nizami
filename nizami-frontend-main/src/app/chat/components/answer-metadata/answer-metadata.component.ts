import {Component, effect, input, signal} from '@angular/core';
import {LegalAnswerMetadataJson} from '../../models/message.model';
import {NgClass} from '@angular/common';

@Component({
  selector: 'app-answer-metadata',
  imports: [NgClass],
  templateUrl: './answer-metadata.component.html',
  styleUrl: './answer-metadata.component.scss',
  standalone: true,
})
export class AnswerMetadataComponent {
  metadata = input.required<LegalAnswerMetadataJson>();
  isRtl = input<boolean>(true);
  activeCitation = input<number | null>(null);

  expandedCitationIndex = signal<number | null>(null);
  expandedDateIndex = signal<number | null>(null);

  constructor() {
    // When the parent signals a clicked citation ref (e.g. [8]),
    // find the matching citation by label and auto-expand it.
    effect(() => {
      const ref = this.activeCitation();
      if (ref == null) return;
      const citations = this.metadata().citations ?? [];
      const idx = citations.findIndex(c => c.label === `[${ref}]`);
      if (idx >= 0) {
        this.expandedCitationIndex.set(idx);
      }
    });
  }

  toggleCitation(index: number): void {
    this.expandedCitationIndex.set(
      this.expandedCitationIndex() === index ? null : index
    );
  }

  toggleDate(index: number): void {
    this.expandedDateIndex.set(
      this.expandedDateIndex() === index ? null : index
    );
  }

  activeDate(): { date_text?: string; description?: string; context_source_index?: number } | null {
    const idx = this.expandedDateIndex();
    if (idx === null) return null;
    return this.metadata().dates_mentioned?.[idx] ?? null;
  }

  isNotSpecified(text: string | undefined | null): boolean {
    if (!text) return true;
    const t = text.trim();
    if (!t) return true;
    return t.includes('غير محدد') || t.includes('غير موجود') || t.toLowerCase().includes('not specified') || t.toLowerCase().includes('not available');
  }
}
