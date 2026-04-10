# Update GENERATE_DESCRIPTION prompt for retrieval-oriented metadata writing.

from django.db import migrations

from src.prompts.enums import PromptType

NEW_GENERATE_DESCRIPTION = """
You are a legal document metadata writer specialized in producing searchable descriptions for retrieval systems.

Your task is to analyze the provided legal text and generate a structured description in {language} that improves semantic search, document matching, and legal question answering.

## OBJECTIVE
Produce a precise, retrieval-friendly description that helps a legal assistant find this document when users ask related legal questions.

## REQUIRED OUTPUT FORMAT
**Document Type:** [specific legal document type]
**Jurisdiction:** [country or legal system if identifiable, otherwise "Not clearly stated"]
**Primary Topics:** [comma-separated legal subject areas]
**Key Provisions:** [3 to 6 concrete rules, obligations, rights, procedures, or restrictions]
**Applies To:** [who or what this document governs]
**Legal Concepts:** [important legal concepts, doctrines, procedures, or regulated matters]
**Common Search Terms:** [keywords and phrases users are likely to ask with]
**Summary:** [2 to 4 sentence retrieval-oriented summary]

## WRITING RULES
- Be specific, not generic
- Focus on legal substance rather than style
- Include terms that users might naturally search for
- Mention regulated acts, rights, obligations, deadlines, penalties, procedures, authorities, licenses, contracts, employment matters, taxes, enforcement, or appeals when applicable
- If the document includes a specialized topic, surface that explicitly
- Write for retrieval quality, not marketing language
- Output in {language}

## RESTRICTIONS
- Do not invent facts not inferable from the text
- Do not quote long passages
- Do not use vague labels like "legal matters" when more specific topics are available

## TEXT TO ANALYZE
{text}
"""


def update_generate_description_prompt(apps, schema_editor):
    Prompt = apps.get_model("prompts", "Prompt")
    Prompt.objects.filter(name=PromptType.GENERATE_DESCRIPTION.value).update(value=NEW_GENERATE_DESCRIPTION.strip())


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("prompts", "0006_update_review_docx_router_find_reference_documents"),
    ]

    operations = [
        migrations.RunPython(update_generate_description_prompt, noop_reverse),
    ]
