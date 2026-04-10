import {Component, input} from '@angular/core';
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
}
