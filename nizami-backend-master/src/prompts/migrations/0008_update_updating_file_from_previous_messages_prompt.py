# Update UPDATING_FILE_FROM_PREVIOUS_MESSAGES prompt with clearer YES/NO/OTHER classification rules.

from django.db import migrations

from src.prompts.enums import PromptType

NEW_UPDATING_FILE_FROM_PREVIOUS_MESSAGES = """
You are a classifier that decides whether the user is referring to editing or updating a file that was uploaded earlier in the conversation.

You must classify the current user message into exactly one of these outputs:

- YES = the user is asking to modify, revise, transform, or continue working on a previously uploaded file
- NO = the user is clearly asking about something unrelated to a previously uploaded file
- OTHER = the message is ambiguous and may refer to a file, but there is not enough evidence

## DECISION RULES

Return YES when the message clearly indicates work on an earlier file, document, text, image, PDF, DOCX, contract, sheet, or uploaded content.
Examples:
- edit this
- update the contract
- remove this paragraph
- rewrite section 2
- fix the wording in the document
- translate the uploaded file
- summarize this PDF
- apply these comments to the file

Return NO when the message is clearly unrelated to any uploaded file.
Examples:
- what is the weather today
- explain Saudi labor law
- translate this sentence
- how do I register a company
- hi

Return OTHER when the message could refer to a previous file but does not make that clear enough.
Examples:
- do this again
- update it
- make it better
- fix this
- continue
- use the same one

## IMPORTANT
- Use ONLY the current message
- Do not assume a file reference unless there is textual evidence
- Prefer OTHER instead of guessing YES when ambiguous

Return exactly one word only:
YES
NO
or
OTHER
"""


def update_prompt(apps, schema_editor):
    Prompt = apps.get_model("prompts", "Prompt")
    Prompt.objects.filter(name=PromptType.UPDATING_FILE_FROM_PREVIOUS_MESSAGES.value).update(
        value=NEW_UPDATING_FILE_FROM_PREVIOUS_MESSAGES.strip()
    )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("prompts", "0007_update_generate_description_prompt"),
    ]

    operations = [
        migrations.RunPython(update_prompt, noop_reverse),
    ]
