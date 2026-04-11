import json
import re
import time
import uuid
import logging


from django.db import transaction
from django.db.models import Q
from langchain_core.documents import Document
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langgraph.constants import START, END
from langgraph.graph import StateGraph
from pydantic import BaseModel, Field
from typing_extensions import TypedDict, Literal, Any
from src.chats.attachment_flow import load_attached_docs_context_for_chat

from src.chats.domain import (
    rephrase_user_input_using_history,
    rephrase_user_input_using_summary,
    find_ref_document_ids_by_description,
    translate_question,
    create_initial_summary,
    update_conversation_summary,
)
from src.common.retrievers import find_rag_source_document_ids_by_description
from src.chats.models import Message, MessageLog, MessageStepLog, Chat
from src.chats.utils import create_legal_advice_llm, detect_language, create_llm
from src.prompts.enums import PromptType
from src.prompts.utils import get_prompt_value_by_name
from src.common.retrievers import FilteredRetriever
from src.gibberish import GibberishConfig, classify_input, InputVerdict
from src.reference_documents.models import RagSourceDocument

_LEGAL_RESPONSE_METADATA_KEYS = (
    'citations',
    'dates_mentioned',
    'numbers_and_percentages',
    'statistics_from_context',
)


def _format_numbered_context_for_rag(*, documents):
    parts = []
    for i, doc in enumerate(documents, 1):
        title = (doc.metadata.get('title') or '').strip() or 'Source document'
        ref_bits = []
        ref_id = doc.metadata.get('reference_document_id')
        if ref_id is not None:
            ref_bits.append(f"reference_document_id={ref_id}")
        rsd = doc.metadata.get('rag_source_document_id')
        if rsd is not None:
            ref_bits.append(f"rag_source_document_id={rsd}")
        header = f"[{i}] {title}"
        if ref_bits:
            header += f" ({', '.join(ref_bits)})"
        parts.append(f"{header}\n{doc.page_content}")
    return "\n\n---\n\n".join(parts)


def _normalize_legal_response_payload(*, response):
    if not isinstance(response, dict):
        return response
    normalized = dict(response)
    for key in _LEGAL_RESPONSE_METADATA_KEYS:
        value = normalized.get(key)
        if not isinstance(value, list):
            normalized[key] = []
    return normalized


def _message_metadata_from_response(*, response):
    if not isinstance(response, dict):
        return None
    if not any(k in response for k in _LEGAL_RESPONSE_METADATA_KEYS):
        return None
    payload = {}
    for key in _LEGAL_RESPONSE_METADATA_KEYS:
        val = response.get(key)
        payload[key] = val if isinstance(val, list) else []
    if not any(payload.values()):
        return None
    return payload


def _deduplicate_and_renumber_citations(*, response):
    """
    Post-process the LLM response dict to:
    1. Deduplicate citations that reference the same source
       (matched on source_title + article_or_clause + law_number; falls back to reference).
    2. Renumber surviving citations [1], [2], ... in order of first appearance in the answer.
    3. Rewrite [n] markers in the answer text to match the new labels.
    Returns a shallow-copied response dict with 'answer' and 'citations' updated.
    """
    if not isinstance(response, dict):
        return response

    citations = response.get('citations')
    answer = response.get('answer') or ''

    if not citations or not isinstance(citations, list):
        return response

    def _dedup_key(c):
        title = (c.get('source_title') or '').strip().lower()
        article = (c.get('article_or_clause') or '').strip().lower()
        law_num = (c.get('law_number') or '').strip().lower()
        key = f'{title}|{article}|{law_num}'
        if key == '||':
            key = (c.get('reference') or '').strip().lower()
        return key

    # Pass 1: deduplicate — keep first occurrence of each key.
    # old_to_canonical maps each original label to the label of the citation it merges into.
    seen_keys: dict = {}        # dedup_key -> canonical old_label
    canonical_citations = []    # unique citations (copies)
    old_to_canonical: dict = {} # old_label -> canonical old_label

    for c in citations:
        old_label = (c.get('label') or '').strip()
        key = _dedup_key(c)
        if key and key in seen_keys:
            old_to_canonical[old_label] = seen_keys[key]
        else:
            if key:
                seen_keys[key] = old_label
            old_to_canonical[old_label] = old_label
            canonical_citations.append(dict(c))

    # Pass 2: determine order of first appearance of canonical labels in the answer.
    markers_seen: list = []
    markers_set: set = set()
    for m in re.finditer(r'\[(\d+)\]', answer):
        raw = f'[{m.group(1)}]'
        canonical = old_to_canonical.get(raw, raw)
        if canonical not in markers_set:
            markers_seen.append(canonical)
            markers_set.add(canonical)

    # Build canonical_old_label -> new sequential label (only citations used in the text)
    canonical_to_new: dict = {lbl: f'[{i}]' for i, lbl in enumerate(markers_seen, 1)}

    # Build raw old_label -> new_label (routed through canonical)
    final_map: dict = {
        raw: canonical_to_new.get(canonical, canonical)
        for raw, canonical in old_to_canonical.items()
    }

    # Rewrite [n] markers in the answer
    updated_answer = re.sub(
        r'\[(\d+)\]',
        lambda m: final_map.get(f'[{m.group(1)}]', f'[{m.group(1)}]'),
        answer,
    )

    # Keep only citations referenced in the answer; update their labels
    canonical_citations = [
        c for c in canonical_citations
        if (c.get('label') or '').strip() in markers_set
    ]
    for c in canonical_citations:
        old_label = (c.get('label') or '').strip()
        c['label'] = canonical_to_new.get(old_label, old_label)

    canonical_citations.sort(
        key=lambda c: int(c['label'][1:-1]) if c.get('label', '')[1:-1].isdigit() else 9999
    )

    result = dict(response)
    result['answer'] = updated_answer
    result['citations'] = canonical_citations
    return result


class State(TypedDict):
    input: str
    query: str
    uuid: str
    input_translation: str
    message: Message
    chat_id: int
    history: list
    summary: str
    unsummarized_messages: list
    attached_docs_context: str  # Summaries of files uploaded in this chat (for follow-up questions)
    decision: str
    system_message: Message
    response: Any
    rag_response: Any
    used_languages: Any
    show_translation_disclaimer: str
    answer_language: str
    output: str
    is_gibberish: bool
    is_related_to_history: bool
    web_search_results: list  # Raw web search results captured for logging/debugging
    url_context: str  # Extracted content from URLs shared by the user


# Schema for structured output to use as routing logic
class Route(BaseModel):
    step: Literal["legal_question", "translation", "other"] = Field(
        None, description="The next step in the routing process"
    )


# Schema for input relevance to chat history
class InputRelevance(BaseModel):
    is_related_to_history: bool = Field(
        description="True if the current input is related to or asking about topics already discussed in the chat history. False if it's a completely new question or topic."
    )


def router(state: State):
    t1 = time.time()
    logger = logging.getLogger(__name__)
    
    try:
        message = state.get('message')
        input_text = state.get('input')
        
        if message is None:
            logger.error("router: message is None in state")
            raise ValueError("Message is None in state")
        
        if input_text is None:
            logger.error("router: input is None in state")
            raise ValueError("Input is None in state")
        
        llm = create_llm('gpt-5-nano', reasoning_effort="minimal")
        router_llm = llm.with_structured_output(Route)
        template = get_prompt_value_by_name(PromptType.ROUTER)

        decision = router_llm.invoke([
            SystemMessage(content=template),
            HumanMessage(content=input_text),
        ])

        t2 = time.time()
        MessageStepLog.objects.create(
            step_name='router',
            message_id=message.id,
            time_sec=t2 - t1,
            input=None,
            output={
                'decision': decision.step,
            }
        )

        return {
            'decision': decision.step,
        }
    except Exception as e:
        logger.error(f"Error in router: {str(e)}", exc_info=True)
        t2 = time.time()
        message = state.get('message')
        if message:
            try:
                MessageStepLog.objects.create(
                    step_name='router',
                    message_id=message.id,
                    time_sec=t2 - t1,
                    input={'error': str(e)},
                    output={
                        'decision': 'legal_question',
                        'error': True,
                    }
                )
            except Exception as e:
                logger.error(f"Error in router: {str(e)}", exc_info=True)
                pass
        # Default to legal_question to continue processing
        return {
            'decision': 'legal_question',
        }


def has_answer(state: State):
    t1 = time.time()
    logger = logging.getLogger(__name__)
    
    try:
        message = state.get('message')
        if message is None:
            logger.error("has_answer: message is None in state")
            raise ValueError("Message is None in state")
        
        child = message.children.first()
        decision = 'yes' if child is not None else 'no'
        
        t2 = time.time()
        MessageStepLog.objects.create(
            step_name='has_answer',
            message_id=message.id,
            time_sec=t2 - t1,
            input=None,
            output={
                'decision': decision,
                'has_child': child is not None,
            }
        )
        
        return {
            'decision': decision,
        }
    except Exception as e:
        logger.error(f"Error in has_answer: {str(e)}", exc_info=True)
        # Default to 'no' to continue processing
        return {
            'decision': 'no',
        }


def first_or_create_message(state: State):
    t1 = time.time()
    
    user_message = Message.objects.filter(uuid=state['uuid']).first()

    if user_message is None:
        question_language = detect_language(state['input'])
        user_message = Message.objects.create(
            role='user',
            language=question_language,
            used_query=state['input'],
            chat_id=state['chat_id'],
            text=state['input'],
            uuid=state['uuid'],
        )
        
    t2 = time.time()
    MessageStepLog.objects.create(
        step_name='first_or_create_message',
        message=user_message,
        time_sec=t2 - t1,
        input={
            'uuid': state['uuid'],
            'input': state['input'],
        },
        output=None
    )

    return {
        'message': user_message,
    }


def retrieve_history(state: State):
    t1 = time.time()
    logger = logging.getLogger(__name__)
    
    try:
        # Get the chat object to access summary
        chat_id = state.get('chat_id')
        message = state.get('message')
        
        if chat_id is None:
            logger.error("retrieve_history: chat_id is None in state")
            raise ValueError("chat_id is None in state")
        
        if message is None:
            logger.error("retrieve_history: message is None in state")
            raise ValueError("message is None in state")
        
        try:
            chat = Chat.objects.get(id=chat_id)
        except Chat.DoesNotExist:
            logger.error(f"retrieve_history: Chat with id {chat_id} does not exist")
            raise
        
        # Get recent messages (last 5 for immediate context, summary handles the rest)
        recent_messages = list(
            reversed(
                Message.objects.filter(Q(chat_id=chat_id) & ~Q(id=message.id)).order_by('-created_at')[
                :5]))
        
        # Get or create summary
        summary = chat.summary
        summary_last_message_id = chat.summary_last_message_id
        
        # Get messages that might not be in summary yet (created after last summarized message)
        unsummarized_messages = []
        if summary_last_message_id:
            # Get messages created after the last summarized message
            unsummarized_messages = list(
                Message.objects.filter(
                    Q(chat_id=chat_id) & 
                    Q(id__gt=summary_last_message_id) & 
                    ~Q(id=message.id)
                ).order_by('created_at')
            )
        
        if not summary and recent_messages:
            # Create initial summary if it doesn't exist
            all_messages = list(
                Message.objects.filter(
                    Q(chat_id=chat_id) & 
                    ~Q(id=message.id) &
                    ~Q(text__isnull=True) &
                    ~Q(text='')
                ).order_by('created_at')
            )
            if all_messages:
                summary = create_initial_summary(all_messages)
                chat.summary = summary
                # Track the last message ID included in summary
                chat.summary_last_message_id = all_messages[-1].id
                chat.save(update_fields=['summary', 'summary_last_message_id'])

        # Load summaries of files uploaded in this chat so follow-up questions can use them
        attached_docs_context = load_attached_docs_context_for_chat(
            chat_id=chat_id,
            user_id=chat.user_id,
        )

        t2 = time.time()
        MessageStepLog.objects.create(
            step_name='retrieve_history',
            message_id=message.id,
            time_sec=t2 - t1,
            input=None,
            output={
                'history_count': len(recent_messages),
                'has_summary': bool(summary),
                'summary_length': len(summary) if summary else 0,
                'unsummarized_count': len(unsummarized_messages),
                'attached_docs_length': len(attached_docs_context),
            }
        )

        return {
            'history': recent_messages,
            'summary': summary or '',
            'unsummarized_messages': unsummarized_messages,  # Messages not yet in summary
            'attached_docs_context': attached_docs_context or '',
        }
    except Exception as e:
        logger.error(f"Error in retrieve_history: {str(e)}", exc_info=True)
        # Return empty history to allow processing to continue
        return {
            'history': [],
            'summary': '',
            'unsummarized_messages': [],
            'attached_docs_context': '',
        }


def rephrase_user_input(state: State):
    t1 = time.time()
    logger = logging.getLogger(__name__)
    
    try:
        user_message = state.get('message')
        if user_message is None:
            logger.error("rephrase_user_input: message is None in state")
            raise ValueError("Message is None in state")
        
        query = user_message.text or state.get('input', '')
        if not query:
            logger.warning("rephrase_user_input: query is empty, using input")
            query = state.get('input', '')
        
        summary = state.get('summary', '')
        attached_docs_context = (state.get('attached_docs_context') or '').strip()
        # Include uploaded doc context so follow-ups like "give me more details" are rephrased with document topic
        rephrase_context = summary or ''
        if attached_docs_context:
            rephrase_context += (
                f"\n\nUploaded documents in this chat (user may ask for more details or refer to these):\n"
                f"{attached_docs_context[:3000]}{'...' if len(attached_docs_context) > 3000 else ''}"
            )

        # Use summary + doc context for rephrasing if available, otherwise fall back to history
        if rephrase_context.strip():
            query = rephrase_user_input_using_summary(query, rephrase_context)
            user_message.used_query = query
            user_message.save()
        else:
            # Fallback to old method if no summary or doc context
            history = state.get('history', [])
            if len(history) > 0:
                used_queries = list(filter(None, [msg.used_query if msg.role != 'ai' else None for msg in history]))
                if len(used_queries) > 0:
                    query = rephrase_user_input_using_history(query, used_queries)
                    user_message.used_query = query
                    user_message.save()

        t2 = time.time()
        MessageStepLog.objects.create(
            step_name='rephrase_user_input',
            message=user_message,
            time_sec=t2 - t1,
            input=None,
            output={
                'query': query,
                'used_summary': bool(summary),
            }
        )

        return {
            'query': query,
        }
    except Exception as e:
        logger.error(f"Error in rephrase_user_input: {str(e)}", exc_info=True)
        t2 = time.time()
        user_message = state.get('message')
        # Use original input as fallback
        fallback_query = state.get('input', state.get('query', ''))
        
        if user_message:
            try:
                MessageStepLog.objects.create(
                    step_name='rephrase_user_input',
                    message=user_message,
                    time_sec=t2 - t1,
                    input={'error': str(e)},
                    output={
                        'query': fallback_query,
                        'used_summary': False,
                        'error': True,
                    }
                )
            except Exception as e:
                logger.error(f"Error in rephrase_user_input: {str(e)}", exc_info=True)
                pass
        
        return {
            'query': fallback_query,
        }


def translate_user_input(state: State):
    t1 = time.time()
    logger = logging.getLogger(__name__)
    
    try:
        user_message = state.get('message')
        if user_message is None:
            logger.error("translate_user_input: message is None in state")
            raise ValueError("Message is None in state")
        
        if not user_message.text:
            logger.warning("translate_user_input: message text is empty")
            # Return empty translation if no text
            t2 = time.time()
            MessageStepLog.objects.create(
                step_name='translate_user_input',
                message=user_message,
                time_sec=t2 - t1,
                input=None,
                output={
                    'input_translation': '',
                }
            )
            return {
                'input_translation': '',
            }
        
        input_translation = translate_question(user_message.text, user_message.language)

        t2 = time.time()
        MessageStepLog.objects.create(
            step_name='translate_user_input',
            message=user_message,
            time_sec=t2 - t1,
            input=None,
            output={
                'input_translation': input_translation,
            }
        )

        return {
            'input_translation': input_translation,
        }
    except Exception as e:
        logger.error(f"Error in translate_user_input: {str(e)}", exc_info=True)
        t2 = time.time()
        user_message = state.get('message')
        
        if user_message:
            try:
                MessageStepLog.objects.create(
                    step_name='translate_user_input',
                    message=user_message,
                    time_sec=t2 - t1,
                    input={'error': str(e)},
                    output={
                        'input_translation': '',
                        'error': True,
                    }
                )
            except Exception as e:
                logger.error(f"Error in translate_user_input: {str(e)}", exc_info=True)
                pass
        
        # Return empty translation as fallback
        return {
            'input_translation': '',
        }


def calculate_disclaimer(state: State):
    t1 = time.time()

    response = state['response']
    used_languages = state['used_languages']
    user_message = state['message']
    question_language = user_message.language

    is_context_used = response.get('is_context_used', False)
    is_answer = response.get('is_answer', False)

    # Determine response language based on user's input (not detected from answer)
    answer_language = detect_language(user_message.text)

    context_multi_language = len(used_languages) > 1
    context_different_lang_from_question = len(used_languages) == 1 and list(used_languages)[0] != question_language
    answer_different_lang_from_question = answer_language != question_language
    is_different_language = (
            context_multi_language or context_different_lang_from_question or answer_different_lang_from_question)

    show_translation_disclaimer = is_different_language and is_context_used and is_answer

    t2 = time.time()
    MessageStepLog.objects.create(
        step_name='calculate_disclaimer',
        message=user_message,
        time_sec=t2 - t1,
        input=None,
        output={
            'answer_language': answer_language,
            'context_different_lang_from_question': context_different_lang_from_question,
            'answer_different_lang_from_question': answer_different_lang_from_question,
            'is_different_language': is_different_language,
            'show_translation_disclaimer': show_translation_disclaimer,
        }
    )

    return {
        'answer_language': answer_language,
        'show_translation_disclaimer': show_translation_disclaimer,
    }


@transaction.atomic
def _update_chat_summary_sync(chat_id: int, message_ids: list):
    """
    Synchronous function to update chat summary (called in background).
    Updates summary and tracks the last message ID included.
    Uses database locking to prevent race conditions.
    
    Args:
        chat_id: The chat ID
        message_ids: List of message IDs to add to summary
    """
    logger = logging.getLogger(__name__)
    
    try:
        if not message_ids:
            return
        
        # Use select_for_update to lock the row and prevent concurrent updates
        chat = Chat.objects.select_for_update().get(id=chat_id)
        existing_summary = chat.summary or ""
        
        # Ensure we're not processing messages already in summary
        if chat.summary_last_message_id:
            message_ids = [mid for mid in message_ids if mid > chat.summary_last_message_id]
        
        if not message_ids:
            logger.debug(f"No new messages to add to summary for chat {chat_id}")
            return
        
        # Get messages
        new_messages = list(Message.objects.filter(id__in=message_ids).order_by('created_at'))
        if not new_messages:
            return
        
        # Update summary with new messages
        updated_summary = update_conversation_summary(existing_summary, new_messages)
        chat.summary = updated_summary
        # Track the last message ID included in summary (the most recent one)
        chat.summary_last_message_id = max(message_ids)
        chat.save(update_fields=['summary', 'summary_last_message_id'])
        
        logger.info(f"Summary updated for chat {chat_id} with {len(new_messages)} messages (last message ID: {chat.summary_last_message_id})")
    except Exception as e:
        logger.error(f"Error updating summary for chat {chat_id}: {str(e)}", exc_info=True)


def update_chat_summary(chat_id: int, new_messages: list):
    """
    Update the chat summary with new messages synchronously.
    
    Args:
        chat_id: The chat ID
        new_messages: List of new Message objects to add to summary
    """
    if not new_messages:
        return
    
    # Extract message IDs for processing
    message_ids = [msg.id for msg in new_messages]
    
    # Update summary synchronously
    _update_chat_summary_sync(chat_id, message_ids)


def store_system_message(state: State):
    t1 = time.time()

    response = _deduplicate_and_renumber_citations(response=state['response'])
    answer = response['answer']
    user_message = state['message']
    answer_language = state['answer_language']
    show_translation_disclaimer = state['show_translation_disclaimer']
    question_language = user_message.language
    chat_id = user_message.chat_id

    metadata_json = _message_metadata_from_response(response=response)

    system_message = Message.objects.create(
        chat_id=chat_id,
        parent=user_message,
        language=answer_language,
        text=answer,
        show_translation_disclaimer=show_translation_disclaimer,
        translation_disclaimer_language=question_language,
        role='ai',
        uuid=uuid.uuid4(),
        metadata_json=metadata_json,
    )
    
    update_chat_summary(chat_id, [user_message, system_message])
    
    t2 = time.time()
    MessageStepLog.objects.create(
        step_name='store_system_message',
        message=user_message,
        time_sec=t2 - t1,
        input=None,
        output={
            'system_message_id': system_message.id,
            'summary_updated': True,
        }
    )

    return {
        'system_message': system_message,
    }


# Matches http/https URLs; stops at whitespace and common trailing punctuation.
_URL_RE = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+')


def extract_url_content(state: State):
    """
    Detect URLs in the user's message, fetch their content, and store it in
    state so answer_legal_question can inject it into the LLM prompt.

    Up to 3 URLs are fetched concurrently via asyncio. If none are found or
    all fetches fail the node returns an empty string and the rest of the
    flow is unaffected.
    """
    import asyncio
    from src.chats.web_search.url_extractor import fetch_urls

    logger = logging.getLogger(__name__)
    input_text = state.get('input', '')
    urls = list(dict.fromkeys(_URL_RE.findall(input_text)))[:3]  # dedupe, cap at 3

    if not urls:
        return {'url_context': ''}

    results = asyncio.run(fetch_urls(urls))

    if not results:
        logger.warning("extract_url_content: all URL fetches failed for input: %s", input_text[:120])
        return {'url_context': ''}

    parts = []
    for i, r in enumerate(results, 1):
        header = f"[Link{i}] {r.title}\n     Source: {r.url}"
        parts.append(f"{header}\n{r.content}")

    logger.info("extract_url_content: extracted content from %d URL(s)", len(results))
    return {'url_context': "\n\n---\n\n".join(parts)}


def _format_web_context(web_results) -> str:
    """
    Format web search results into a numbered block for the LLM system prompt.

    Each result shows its index, title, URL, and cleaned content so the model
    can cite sources and reason about freshness.
    """
    if not web_results:
        return ""
    parts = []
    for i, r in enumerate(web_results, 1):
        header = f"[W{i}] {r.title}"
        if r.url:
            header += f"\n     Source: {r.url}"
        parts.append(f"{header}\n{r.content}")
    return "\n\n---\n\n".join(parts)


def answer_legal_question(state: State):
    t1 = time.time()
    logger = logging.getLogger(__name__)

    user_message = state['message']
    translation = state['input_translation']
    query = state['query']

    # ------------------------------------------------------------------
    # 1. Identify relevant document IDs (unchanged from original logic)
    # ------------------------------------------------------------------
    from src.settings import RAG_SOURCE
    if RAG_SOURCE == 'new':
        ids_all = find_rag_source_document_ids_by_description(query)

        # Prefer non-MOJ docs. Only use MOJ if filtering leaves us with zero docs.
        moj_prefix = "processed/MOJ/"
        ids_non_moj = list(
            RagSourceDocument.objects
            .filter(id__in=ids_all)
            .exclude(s3_key__istartswith=moj_prefix)
            .values_list("id", flat=True)
        )
        ids = ids_non_moj if ids_non_moj else ids_all
    else:
        ids = find_ref_document_ids_by_description(query)

    llm = create_legal_advice_llm()
    template = get_prompt_value_by_name(PromptType.LEGAL_ADVICE)
    # Retrieve broadly so the reranker has enough candidates to work with.
    # The reranker will trim this down to ~6 high-quality chunks before the LLM.
    # 25 (up from 15) gives the reranker a larger pool, improving recall for
    # specific legal articles that may score lower on pure vector similarity.
    RETRIEVE_K = 25
    RERANK_TOP_N = 6
    retriever = FilteredRetriever(ids, k=RETRIEVE_K, logger=logger)

    if RAG_SOURCE == 'new':
        search_kwargs = {
            'k': RETRIEVE_K,
            'rerank_top_n': RERANK_TOP_N,
            'source': 'RagSourceDocumentChunk',
            'filter': {'rag_source_document_id': {'$in': ids}},
        }
    else:
        search_kwargs = {
            'k': RETRIEVE_K,
            'rerank_top_n': RERANK_TOP_N,
            'source': 'langchain_pg_embedding',
            'filter': {'reference_document_id': {'$in': ids}},
        }

    # ------------------------------------------------------------------
    # 2. Build conversation history messages (fast — no I/O)
    #    Run this while the retrieval threads are already running (step 3).
    # ------------------------------------------------------------------
    summary = state.get('summary', '')
    history = state.get('history', [])
    unsummarized_messages = state.get('unsummarized_messages', [])
    attached_docs_context = state.get('attached_docs_context', '') or ''

    history_messages = []
    if summary:
        history_messages.append(SystemMessage(
            content=f"Conversation Summary (for context):\n{summary}"
        ))
    if attached_docs_context.strip():
        history_messages.append(SystemMessage(
            content=(
                "Uploaded documents in this chat (use for questions about the user's files):\n"
                f"{attached_docs_context}\n\n"
                "If the user asks for more details, clarification, or a follow-up about a document they shared "
                "(e.g. 'give me more details', 'tell me more about the document'), answer in detail using the "
                "uploaded document content above. If the user asks about something specific in a document "
                "(e.g. a clause, section, obligation, party, date, or term), find and cite that part of the "
                "document and answer precisely. Do not ask the user to rephrase or be more specific when the "
                "question clearly refers to the document or prior conversation."
            )
        ))
    for msg in unsummarized_messages:
        if msg.role == 'user':
            history_messages.append(HumanMessage(content=msg.text))
        elif msg.role == 'ai':
            history_messages.append(AIMessage(content=msg.text))
    recent_messages = history[-3:] if len(history) > 3 else history
    for msg in recent_messages:
        if msg.role == 'user':
            history_messages.append(HumanMessage(content=msg.text))
        elif msg.role == 'ai':
            history_messages.append(AIMessage(content=msg.text))

    response_language = detect_language(user_message.text)
    languages = {
        'ar': "Arabic",
        'en': "English",
        'fr': "French",
        'hi': "Hindi",
        'ur': "Urdu",
    }
    language_instruction = (
        f"IMPORTANT: You must respond ONLY in {languages[response_language]}. "
        "Do not mix languages. "
    )
    updated_template = language_instruction + template

    # ------------------------------------------------------------------
    # 3. Run RAG retrieval + web search in parallel
    # ------------------------------------------------------------------
    from src.chats.retrieval.orchestrator import RetrievalOrchestrator
    from src.chats.retrieval.reranker import get_reranker
    from src.chats.web_search.service import get_web_search_service
    from django.conf import settings as django_settings

    web_service = get_web_search_service()
    reranker = get_reranker(top_n=RERANK_TOP_N) if getattr(django_settings, 'ENABLE_RERANKING', True) else None
    orchestrator = RetrievalOrchestrator(web_search_service=web_service, reranker=reranker)

    logger.info(
        "answer_legal_question: starting parallel retrieval | "
        "query=%s | web_enabled=%s | reranking=%s",
        query[:80],
        web_service is not None,
        reranker is not None,
    )
    retrieval = orchestrator.run(
        query=query,
        translated_query=translation,
        retriever=retriever,
    )
    logger.info(
        "answer_legal_question: retrieval done | "
        "rag_docs=%d | rag_ok=%s | web_results=%d | web_ok=%s",
        len(retrieval.rag_documents),
        retrieval.rag_success,
        len(retrieval.web_results),
        retrieval.web_success,
    )

    # ------------------------------------------------------------------
    # 4. Decide whether we have enough context to answer
    # ------------------------------------------------------------------
    has_rag = bool(retrieval.rag_documents)
    has_web = bool(retrieval.web_results)

    if not has_rag and not has_web:
        logger.warning(
            "answer_legal_question: both RAG and web search returned no "
            "results — returning safe fallback"
        )
        # Build a minimal response dict that preserves the expected structure
        # so downstream nodes (decode_response_json, etc.) still work.
        from langchain_core.messages import AIMessage as LCAIMessage
        fallback_answer = {
            'answer': (
                "I'm sorry, I could not find relevant information to answer your question. "
                "Please try rephrasing or consult a qualified legal professional."
            )
        }
        import json as _json
        fallback_response = {
            'input': query,
            'translated_input': translation,
            'source_documents': [],
            'context': '',
            'prompt': [],
            'response': LCAIMessage(content=_json.dumps(fallback_answer)),
        }
        t2 = time.time()
        MessageStepLog.objects.create(
            step_name='answer_legal_question',
            message=user_message,
            time_sec=t2 - t1,
            input={'input': query, 'translated_input': translation, 'filters': search_kwargs},
            output={'fallback': True, 'rag_ok': retrieval.rag_success, 'web_ok': retrieval.web_success},
        )
        return {
            'rag_response': fallback_response,
            'web_search_results': [],
        }

    # ------------------------------------------------------------------
    # 5. Format contexts and build the final prompt
    # ------------------------------------------------------------------
    def format_rag_docs(docs):
        if not docs:
            logger.warning('No RAG documents retrieved — context will be empty.')
        return _format_numbered_context_for_rag(documents=docs)

    # The base prompt still receives internal RAG context via {context}.
    # Web results are injected as a separate SystemMessage so the LLM can
    # clearly distinguish the two sources and synthesise them correctly.
    base_prompt = ChatPromptTemplate.from_messages([
        ("system", re.sub(r"\{language}", languages[response_language], updated_template)),
        *history_messages,
        ("human", "{input}"),
    ])

    def build_prompt_messages(inputs):
        """Build the final message list, injecting web and URL context when available."""
        messages = base_prompt.format_messages(
            input=inputs["input"],
            context=inputs["context"],
        )
        if inputs.get("url_context"):
            url_instruction = SystemMessage(
                content=(
                    "=== Content From User-Shared Link(s) ===\n"
                    "The user shared one or more links. The content has been extracted "
                    "and is provided below. Use it to answer any questions the user has "
                    "about those links. Cite the source URL when referencing this content.\n\n"
                    f"{inputs['url_context']}\n"
                    "=== End of Link Content ==="
                )
            )
            messages.insert(len(messages) - 1, url_instruction)
        if inputs.get("web_context"):
            synthesis_instruction = SystemMessage(
                content=(
                    "=== Live Web Search Results ===\n"
                    "The following results were retrieved from the web in real time. "
                    "Use them to:\n"
                    "  1. Validate whether your answer is still current\n"
                    "  2. Fill gaps not covered by the internal documents above\n"
                    "  3. Add recent legal developments relevant to the question\n"
                    "If a web result conflicts with internal knowledge, mention the conflict "
                    "and explain which source appears more recent or authoritative. "
                    "Never fabricate information not present in either source.\n\n"
                    f"{inputs['web_context']}\n"
                    "=== End of Web Search Results ==="
                )
            )
            # Insert before the final HumanMessage so the LLM sees the web
            # context as close to the question as possible.
            messages.insert(len(messages) - 1, synthesis_instruction)
        return messages

    # ------------------------------------------------------------------
    # 6. Build and invoke the answer chain with pre-fetched documents
    # ------------------------------------------------------------------
    pre_fetched_docs = retrieval.rag_documents
    web_context_text = _format_web_context(retrieval.web_results) if has_web else ""
    url_context_text = state.get('url_context', '') or ''

    rag_chain = (
        RunnablePassthrough.assign(source_documents=lambda _: pre_fetched_docs)
        | RunnablePassthrough.assign(
            context=lambda inputs: format_rag_docs(inputs["source_documents"])
        )
        | RunnablePassthrough.assign(web_context=lambda _: web_context_text)
        | RunnablePassthrough.assign(url_context=lambda _: url_context_text)
        | RunnablePassthrough.assign(prompt=RunnableLambda(build_prompt_messages))
        | RunnablePassthrough.assign(response=lambda inputs: llm.invoke(inputs["prompt"]))
    )

    logger.info("answer_legal_question: invoking LLM")
    response = rag_chain.invoke({
        'input': query,
        'translated_input': translation,
    })

    # ------------------------------------------------------------------
    # 7. Log and return (structure unchanged — downstream nodes rely on it)
    # ------------------------------------------------------------------
    MessageLog.logs_objects.create(
        message=user_message,
        response=response,
    )

    t2 = time.time()
    MessageStepLog.objects.create(
        step_name='answer_legal_question',
        message=user_message,
        time_sec=t2 - t1,
        input={
            'input': query,
            'translated_input': translation,
            'filters': search_kwargs,
        },
        output={
            'rag_response': response,
            'rag_docs': len(retrieval.rag_documents),
            'web_results': len(retrieval.web_results),
            'rag_elapsed_sec': retrieval.rag_elapsed_sec,
            'web_elapsed_sec': retrieval.web_elapsed_sec,
        },
    )

    return {
        'rag_response': response,
        'web_search_results': [
            {'title': r.title, 'url': r.url, 'score': r.score}
            for r in retrieval.web_results
        ],
    }


def extract_used_languages(state: State):
    t1 = time.time()
    
    response = state['rag_response']
    source_documents = response['source_documents']

    used_languages = set()
    for source_document in source_documents:
        d: Document = source_document
        lang = d.metadata.get('language')
        if lang:
            used_languages.add(lang)


    t2 = time.time()
    MessageStepLog.objects.create(
        step_name='extract_used_languages',
        message=state['message'],
        time_sec=t2 - t1,
        input={
            'response': response,
            'source_documents': source_documents,
        },
        output={
            'used_languages': used_languages,
        }
    )


    return {
        'used_languages': used_languages,
    }


def decode_response_json(state: State):
    t1 = time.time()
    response = state['rag_response']['response'].content

    if response.startswith('```json') and response.endswith('```'):
        response = response[7:-3].strip()
    try:
        parsed = json.loads(response)
        if isinstance(parsed, dict):
            response = _normalize_legal_response_payload(response=parsed)
        else:
            response = {'answer': str(parsed)}
    except json.decoder.JSONDecodeError:
        response = {
            'answer': response,
        }
    
    t2 = time.time()
    MessageStepLog.objects.create(
        step_name='decode_response_json',
        message=state['message'],
        time_sec=t2 - t1,
        input=None,
        output={
            'response': response,
        }
    )

    return {
        'response': response,
    }


def translate_previous_message(state: State):
    """Translate previous message"""
    history = state['history']
    
    t1 = time.time()

    llm = create_llm('gpt-5-nano', reasoning_effort="low")
    to_langs = {
        'ar': 'English',
        'en': 'Arabic',
        'fr': 'English',
        'hi': 'English',
        'ur': 'English',
    }

    previous_message: Message = history[-1]
    to_lang = to_langs[previous_message.language]

    result = llm.invoke([
        SystemMessage(
            f"Translate the user query to {to_lang} without adding any context or so, just translate as requested"),
        HumanMessage(
            previous_message.text
        ),
    ])

    t2 = time.time()

    MessageStepLog.objects.create(
        step_name="translate_previous_message",
        message=state['message'],
        time_sec=t2 - t1,
        input=None,
        output={
            'output': result.content,
        }
    )
    
    return {"output": result.content}


def store_translation_message(state: State):
    t1 = time.time()

    answer = state['output']
    user_message = state['message']
    answer_language = detect_language(answer)
    chat_id = user_message.chat_id

    system_message = Message.objects.create(
        chat_id=chat_id,
        parent=user_message,
        language=answer_language,
        text=answer,
        role='ai',
        uuid=uuid.uuid4(),
    )
    
    update_chat_summary(chat_id, [user_message, system_message])
    
    t2 = time.time()
    MessageStepLog.objects.create(
        step_name="store_translation_message",
        message=user_message,
        time_sec=t2 - t1,
        input=None,
        output={
            'system_message_id': system_message.id,
            'summary_updated': True,
        }
    )

    return {
        'system_message': system_message,
    }


def legal_question_flow(state: State):
    return state


def return_first_child(state: State):
    return {
        'system_message': state['message'].children.first(),
    }


def validate_input_quality(state: State):
    """Check if the user input is random characters or gibberish using deterministic detection"""
    t1 = time.time()
    logger = logging.getLogger(__name__)
    
    try:
        message = state.get('message')
        input_text = state.get('input')
        
        if message is None:
            logger.error("validate_input_quality: message is None in state")
            raise ValueError("Message is None in state")
        
        if input_text is None:
            logger.error("validate_input_quality: input is None in state")
            raise ValueError("Input is None in state")
        
        gibberishConfig = GibberishConfig()
        gibberishConfig.llm_enabled = True
        # Use our deterministic gibberish detection system
        result = classify_input(input_text, config=gibberishConfig)
        
        # Classify as gibberish if status is GIBBERISH
        is_gibberish = (result.status == InputVerdict.GIBBERISH)
        
        t2 = time.time()
        MessageStepLog.objects.create(
            step_name='validate_input_quality',
            message_id=message.id,
            time_sec=t2 - t1,
            input={
                'input': input_text,
            },
            output={
                'is_gibberish': is_gibberish,
                'classification_status': result.status.value,
                'score': result.score,
                'reasons': result.reasons[:3] if result.reasons else [],  # Store first 3 reasons
            }
        )
        
        return {
            'is_gibberish': is_gibberish,
        }
    except Exception as e:
        logger.error(f"Error in validate_input_quality: {str(e)}", exc_info=True)
        # Default to not gibberish to allow processing to continue
        return {
            'is_gibberish': False,
        }


def handle_gibberish_input(state: State):
    """Handle gibberish input by returning a message in the appropriate language"""
    t1 = time.time()
    
    user_message = state['message']
    chat_id = user_message.chat_id
    
    input_text = state['input']
    input_language = detect_language(input_text)
    messages = {
        "en": "I couldn't understand your message. Could you please rephrase it? I'm here to help with legal inquiries.",
        "ar": "لم أتمكن من فهم رسالتك. هل يمكنك إعادة صياغتها؟ أنا هنا للمساعدة في الاستفسارات القانونية.",
        "fr": "Je n'ai pas pu comprendre votre message. Pouvez-vous le reformuler, s'il vous plaît ? Je suis là pour vous aider dans les questions juridiques.",
        "hi": "मैं आपका संदेश समझ नहीं पाया। क्या आप कृपया इसे दोबारा स्पष्ट रूप से लिख सकते हैं? मैं कानूनी प्रश्नों में सहायता के लिए यहां हूं।",
        "ur": "میں آپ کا پیغام سمجھ نہیں سکا۔ کیا آپ براہ کرم اسے دوبارہ واضح انداز میں لکھ سکتے ہیں؟ میں قانونی سوالات میں مدد کے لیے یہاں ہوں۔",
    }
    response_message = messages.get(input_language, messages["en"])
    answer_language = input_language
    
    system_message = Message.objects.create(
        chat_id=chat_id,
        parent=user_message,
        language=answer_language,
        text=response_message,
        role='ai',
        uuid=uuid.uuid4(),
    )
    
    update_chat_summary(chat_id, [user_message, system_message])
    
    t2 = time.time()
    MessageStepLog.objects.create(
        step_name='handle_gibberish_input',
        message=user_message,
        time_sec=t2 - t1,
        input={
            'input': state['input'],
        },
        output={
            'system_message_id': system_message.id,
            'response_language': answer_language,
            'summary_updated': True,
        }
    )
    
    return {
        'system_message': system_message,
    }


def check_input_relevance(state: State):
    """Check if the current input is related to the chat history"""
    t1 = time.time()
    logger = logging.getLogger(__name__)
    
    try:
        message = state.get('message')
        input_text = state.get('input')
        
        if message is None:
            logger.error("check_input_relevance: message is None in state")
            raise ValueError("Message is None in state")
        
        if input_text is None:
            logger.error("check_input_relevance: input is None in state")
            raise ValueError("Input is None in state")
        
        summary = state.get('summary', '')
        history = state.get('history', [])
        unsummarized_messages = state.get('unsummarized_messages', [])
        attached_docs_context = state.get('attached_docs_context', '') or ''
        
        # If no summary, no history, no uploaded docs, it's definitely a new question
        if not summary and (not history or len(history) == 0) and not unsummarized_messages and not attached_docs_context.strip():
            t2 = time.time()
            MessageStepLog.objects.create(
                step_name='check_input_relevance',
                message_id=message.id,
                time_sec=t2 - t1,
                input={
                    'input': input_text,
                    'history_count': 0,
                },
                output={
                    'is_related_to_history': False,
                    'reason': 'no_history',
                }
            )
            return {
                'is_related_to_history': False,
            }
        
        llm = create_llm('gpt-5-nano', reasoning_effort="minimal")
        relevance_llm = llm.with_structured_output(InputRelevance)
        
        # Build conversation context from summary and recent messages
        history_context = ""
        last_ai_message = None
        
        if summary:
            # Use summary as primary context
            history_context = f"\n\nConversation Summary:\n{summary}"
        if attached_docs_context.strip():
            history_context += f"\n\nUploaded documents in this chat:\n{attached_docs_context[:2000]}{'...' if len(attached_docs_context) > 2000 else ''}"
        
        # Add messages that aren't in summary yet (from async update race condition)
        if unsummarized_messages:
            unsummarized_context = "\n\nRecent Messages (not yet in summary):\n"
            for msg in unsummarized_messages:
                role = "User" if msg.role == 'user' else "Assistant"
                unsummarized_context += f"{role}: {msg.text}\n"
            history_context += unsummarized_context
        
        # Add recent messages for immediate context (last 3 messages)
        recent_messages = history[-3:] if len(history) > 3 else history
        if recent_messages:
            recent_context = "\n\nMost Recent Messages:\n"
            for msg in recent_messages:
                role = "User" if msg.role == 'user' else "Assistant"
                recent_context += f"{role}: {msg.text}\n"
            history_context += recent_context
        
        # Find the most recent AI message
        for msg in reversed(recent_messages if recent_messages else history):
            if msg.role == 'ai':
                last_ai_message = msg.text
                break
        
        # Highlight the last AI message if it exists
        last_ai_context = ""
        if last_ai_message:
            last_ai_context = f"\n\nIMPORTANT: The Assistant's last message was:\n{last_ai_message}\n\nIf the user is answering this question (e.g., 'yes', 'no', 'ok', 'sure', etc.), it is RELATED."
        
        prompt = f"""You are analyzing whether the current user input is related to the previous conversation history.

Determine if the current input is:
- RELATED to the chat history: The user is asking about, referring to, or continuing a topic already discussed in the conversation, OR answering a question that the Assistant asked
- NOT RELATED to the chat history: The user is asking a completely new question about a different topic that is unrelated to the conversation

Examples of RELATED (return True):
- User answers "yes" or "no" to a question the Assistant asked
- User responds with short answers like "ok", "sure", "correct", "that's right" to the Assistant's question
- User asks "Can you tell me more about that?" after discussing contracts
- User asks "give me more details" or "more details please" after the Assistant answered about a document or topic
- User asks "can you give me more details about the document I shared before?" (clearly about uploaded document)
- User asks "What about the second point?" referring to a previous answer
- User asks "How does this apply to my case?" after discussing a legal concept
- User asks follow-up questions about the same topic or uploaded document
- User asks for clarification on something mentioned earlier
- User provides additional information related to a previous discussion
- User confirms or denies something the Assistant mentioned

Examples of NOT RELATED (return False):
- User asks "What is a contract?" when previous conversation was about employment law (completely different topic)
- User asks a completely new legal question on a different topic
- User starts a new conversation thread on an unrelated subject
- User asks about something that has no connection to the conversation history

Current user input to check:
{input_text}
{history_context}{last_ai_context}
"""
        
        decision = relevance_llm.invoke([
            SystemMessage(content=prompt),
            HumanMessage(content=input_text),
        ])
        
        t2 = time.time()
        MessageStepLog.objects.create(
            step_name='check_input_relevance',
            message_id=message.id,
            time_sec=t2 - t1,
            input={
                'input': input_text,
                'history_count': len(history),
                'has_summary': bool(summary),
            },
            output={
                'is_related_to_history': decision.is_related_to_history,
            }
        )
        
        return {
            'is_related_to_history': decision.is_related_to_history,
        }
    except Exception as e:
        logger.error(f"Error in check_input_relevance: {str(e)}", exc_info=True)
        # Default to not related to allow processing to continue
        t2 = time.time()
        message = state.get('message')
        input_text = state.get('input', '')
        if message:
            try:
                MessageStepLog.objects.create(
                    step_name='check_input_relevance',
                    message_id=message.id,
                    time_sec=t2 - t1,
                    input={
                        'input': input_text,
                        'error': str(e),
                    },
                    output={
                        'is_related_to_history': False,
                        'error': True,
                    }
                )
            except Exception as e:
                logger.error(f"Error in check_input_relevance: {str(e)}", exc_info=True)
        return {
            'is_related_to_history': False,
        }


def handle_related_input(state: State):
    """Handle input that is related to chat history - ask user to be more specific"""
    t1 = time.time()
    
    user_message = state['message']
    chat_id = user_message.chat_id
    question_language = user_message.language
    
    by_language_message = {
        "en": "Please be more specific in your question. Please rephrase your question more clearly and in more detail.",
        "ar": "يرجى أن تكون أكثر تحديداً في سؤالك. يرجى إعادة صياغة سؤالك بشكل أوضح وأكثر تفصيلاً.",
        "fr": "Veuillez être plus précis dans votre question. Merci de la reformuler de manière plus claire et plus détaillée.",
        "hi": "कृपया अपने प्रश्न में और अधिक स्पष्टता दें। कृपया अपने प्रश्न को अधिक साफ़ और विस्तृत रूप से दोबारा लिखें।",
        "ur": "براہ کرم اپنے سوال میں مزید وضاحت دیں۔ مہربانی کرکے اپنے سوال کو زیادہ واضح اور تفصیل سے دوبارہ لکھیں۔",
    }
    response_text = by_language_message.get(question_language, by_language_message["en"])
    answer_language = question_language
    
    system_message = Message.objects.create(
        chat_id=chat_id,
        parent=user_message,
        language=answer_language,
        text=response_text,
        role='ai',
        uuid=uuid.uuid4(),
    )
    
    update_chat_summary(chat_id, [user_message, system_message])
    
    t2 = time.time()
    MessageStepLog.objects.create(
        step_name='handle_related_input',
        message=user_message,
        time_sec=t2 - t1,
        input={
            'input': state['input'],
        },
        output={
            'system_message_id': system_message.id,
            'summary_updated': True,
        }
    )
    
    return {
        'system_message': system_message,
    }


def build_graph():
    graph_builder = StateGraph(State)

    graph_builder.add_node('first_or_create_message', first_or_create_message)
    graph_builder.add_node('retrieve_history', retrieve_history)
    graph_builder.add_node('router', router)
    graph_builder.add_node('legal_question_flow', legal_question_flow)
    graph_builder.add_node('translate_user_input', translate_user_input)
    graph_builder.add_node('translate_previous_message', translate_previous_message)
    graph_builder.add_node('store_translation_message', store_translation_message)
    graph_builder.add_node('rephrase_user_input', rephrase_user_input)
    graph_builder.add_node('answer_legal_question', answer_legal_question)
    graph_builder.add_node('extract_used_languages', extract_used_languages)
    graph_builder.add_node('decode_response_json', decode_response_json)
    graph_builder.add_node('calculate_disclaimer', calculate_disclaimer)
    graph_builder.add_node('store_system_message', store_system_message)
    graph_builder.add_node('has_answer', has_answer)
    graph_builder.add_node('return_first_child', return_first_child)
    graph_builder.add_node('validate_input_quality', validate_input_quality)
    graph_builder.add_node('handle_gibberish_input', handle_gibberish_input)
    graph_builder.add_node('extract_url_content', extract_url_content)
    graph_builder.add_node('check_input_relevance', check_input_relevance)
    graph_builder.add_node('handle_related_input', handle_related_input)

    graph_builder.add_edge(START, "first_or_create_message")
    graph_builder.add_edge('first_or_create_message', 'validate_input_quality')
    
    graph_builder.add_conditional_edges(
        "validate_input_quality",
        lambda state: "gibberish" if state.get('is_gibberish', False) else "valid",
        {
            "gibberish": 'handle_gibberish_input',
            "valid": 'has_answer',
        },
    )
    
    graph_builder.add_edge("handle_gibberish_input", END)

    graph_builder.add_conditional_edges(
        "has_answer",
        lambda state: state['decision'],
        {
            "yes": 'return_first_child',
            "no": 'retrieve_history',
        },
    )

    graph_builder.add_edge("retrieve_history", "extract_url_content")
    graph_builder.add_edge("extract_url_content", "check_input_relevance")
    
    # When input is related to history or uploaded docs (e.g. "give me more details"), answer using context.
    # Do not ask user to "be more specific"; rephrase and answer in detail.
    graph_builder.add_conditional_edges(
        "check_input_relevance",
        lambda state: "related" if state.get('is_related_to_history', False) else "new_question",
        {
            "related": 'router',
            "new_question": 'router',
        },
    )

    graph_builder.add_conditional_edges(
        "router",
        lambda state: state['decision'],
        {
            "legal_question": 'legal_question_flow',
            "other": 'legal_question_flow',
            "translation": "translate_previous_message",
        },
    )

    graph_builder.add_edge('legal_question_flow', 'translate_user_input')
    graph_builder.add_edge('legal_question_flow', 'rephrase_user_input')

    graph_builder.add_edge('translate_user_input', 'answer_legal_question')
    graph_builder.add_edge('rephrase_user_input', 'answer_legal_question')
    
    graph_builder.add_edge('answer_legal_question', 'extract_used_languages')
    graph_builder.add_edge('answer_legal_question', 'decode_response_json')

    graph_builder.add_edge('extract_used_languages', 'calculate_disclaimer')
    graph_builder.add_edge('decode_response_json', 'calculate_disclaimer')

    graph_builder.add_edge('calculate_disclaimer', 'store_system_message')

    graph_builder.add_edge("store_system_message", END)

    graph_builder.add_edge("translate_previous_message", 'store_translation_message')
    graph_builder.add_edge("store_translation_message", END)

    return graph_builder.compile()
