"""对话中的流程删除确认：列出真实目标，用户确认一次后按原目标执行。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import re
import unicodedata
from typing import Any

from sqlalchemy.orm import Session

from sheetmate.db.models import Conversation, RecipeRecord, User, new_id
from sheetmate.services.conversations import service as conversation_service
from sheetmate.services.workflows import service as recipe_service
from sheetmate.services.identity.auth import require_customer
from sheetmate.core.errors import NotFound, ServiceError


def _conversation(db: Session, user: User, conversation_id: str) -> Conversation:
    conversation = conversation_service.get_conversation(db, user, conversation_id)
    if conversation.customer_id:
        require_customer(db, user, conversation.customer_id)
    return conversation


def _in_scope(conversation: Conversation, record: RecipeRecord) -> bool:
    return record.conversation_id == conversation.id or bool(
        conversation.customer_id and record.customer_id == conversation.customer_id
    )


def _snapshot(record: RecipeRecord) -> dict[str, Any]:
    return {
        "recipe_id": record.recipe_id,
        "name": record.name,
        "version": record.version,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
        "customer_id": record.customer_id,
        "conversation_id": record.conversation_id,
    }


def prepare_confirmation(db: Session, user: User, conversation_id: str, recipe_ids: list[str]) -> dict[str, Any]:
    """固定这次实际展示的目标，不在用户确认后扩大到后来新增的流程。"""
    conversation = _conversation(db, user, conversation_id)
    targets = list(dict.fromkeys(str(recipe_id) for recipe_id in recipe_ids if recipe_id))
    if not targets:
        raise ServiceError("当前没有需要删除的流程。")
    recipes = []
    for recipe_id in targets:
        record = recipe_service.get_record(db, recipe_id)
        if not _in_scope(conversation, record):
            raise ServiceError("这个流程不属于当前客户或会话")
        if record.customer_id:
            require_customer(db, user, record.customer_id)
        recipes.append(_snapshot(record))
    lines = [f"{index}. 「{recipe['name']}」v{recipe['version']}" for index, recipe in enumerate(recipes, 1)]
    confirmation = {
        "confirmation_id": new_id("rdc"),
        "recipe_ids": targets,
        "recipes": recipes,
        "question": f"确认删除以下 {len(recipes)} 个流程及其全部版本吗？\n" + "\n".join(lines),
        "options": ["确认删除", "取消"],
        "created_at": datetime.now().isoformat(),
    }
    conversation.pending_recipe_deletion = confirmation
    return deepcopy(confirmation)


def peek_pending(db: Session, user: User, conversation_id: str) -> dict[str, Any] | None:
    conversation = _conversation(db, user, conversation_id)
    pending = conversation.pending_recipe_deletion
    return deepcopy(pending) if isinstance(pending, dict) and pending.get("recipe_ids") else None


def _normalize_reply(message: str) -> str:
    normalized = unicodedata.normalize("NFKC", message).strip().lower()
    return re.sub(r"[\s，。！？、,;；.!?：:]", "", normalized)


def confirmation_reply(message: str) -> bool | None:
    """确认入口接受常用短答和明确的自然语句，不让用户再开启额外授权。"""
    normalized = _normalize_reply(message)
    if normalized in {"取消", "不用了", "算了", "保留", "保留流程", "不", "否", "不了", "no", "n", "cancel", "2"}:
        return False
    if re.search(r"(?:先别|别|不要|不用|不必|暂不|暂时不|先不|不再|不想|不打算|不准备|不希望|不是要|不是说|没有要|不)(?:继续|再|确认)?(?:删除|删|清空)|取消(?:删除|确认)|不确认", normalized):
        return False
    # 改成删除另一条或只删部分目标，需要由执行助手重新列出实际目标；
    # 不能把旧目标列表的确认扩大或套用到用户刚改过的范围。
    if re.search(r"(?:保留|留下)(?:第|其中|这|某|另|其余|其他|一[个条]|部分)|除了|(?:只|仅)(?:删除|删|清空)|另[一外]|改为|改成|换成|换为|第[一二三四五六七八九十\d]+[个条]", normalized):
        return None
    if re.search(r"是什么意思|什么含义|怎么|如何|为什么|会不会|能不能|如果|假如|确认(?:后|之前|以后)|[吗么]$", normalized):
        return None
    if normalized in {
        "确认删除", "确认", "确定", "确定删除", "是", "是的", "对", "对的", "好", "好的", "好啊", "好吧",
        "可以", "行", "嗯", "嗯嗯", "同意", "没问题", "删吧", "删除吧", "删掉吧", "执行", "继续", "yes", "y", "ok", "okay", "1",
    }:
        return True
    # 明确换成处理文件、数据或排班行的要求，不沿用待删除流程的确认。
    if "流程" not in normalized and re.search(r"(?:删除|删掉|清空).*(?:文件|表格|数据|记录|行|排班)", normalized):
        return None
    if re.search(r"(?:确认|确定|同意)(?:就|现在|直接|全部|都)?(?:删除|删掉|删去|清空)", normalized):
        return True
    if re.search(r"(?:删除|删掉|删去|清空).*(?:流程|配方)|(?:流程|配方).*(?:删除|删掉|删去|清空)", normalized):
        return True
    if re.search(r"^(?:是的|好的|可以|同意|好|对|没问题).*(?:删除|删掉|删了|删吧|删|清空)$", normalized):
        return True
    if re.search(r"^(?:把|将)?(?:它们|它|这些|这个|全部|所有|都|这两个|这两条).*(?:删除|删掉|删了|删吧|删|清空)$", normalized):
        return True
    return None


def _changes_pending_targets(message: str, pending: dict[str, Any]) -> bool:
    """确认原清单可以直接执行；新的名称或范围交给助手重新列出。"""
    normalized = _normalize_reply(message)
    names = [
        (_normalize_reply(str(recipe.get("name", ""))), str(recipe.get("recipe_id", "")))
        for recipe in pending.get("recipes", [])
    ]
    mentioned = set()
    spans: list[tuple[int, int]] = []
    # 先识别长名称，避免「月结汇总」被误当作也点名了另一个「汇总」。
    for name, recipe_id in sorted(names, key=lambda item: len(item[0]), reverse=True):
        if not name or not recipe_id:
            continue
        for match in re.finditer(re.escape(name), normalized):
            if any(match.start() < end and start < match.end() and (start, end) != match.span()
                   for start, end in spans):
                continue
            mentioned.add(recipe_id)
            if match.span() not in spans:
                spans.append(match.span())
    # 「删除全部流程，包括月结汇总」仍是确认全部。先去掉名称，
    # 防止名称本身含「全部」等字样而扩大确认范围。
    scope_text = normalized
    for start, end in sorted(spans, reverse=True):
        scope_text = scope_text[:start] + "#" + scope_text[end:]
    scope_text = re.sub(r'''[「」『』“”"'《》()\[\]【】]''', "", scope_text)
    delete = r"(?:删除|删掉|删去|删了|删吧|删|清空)"
    collective = r"(?:以上|上述|前述|这批|这些|它们|全部|所有)"
    covers_all = re.search(
        delete + collective + "|" + collective + r"(?:的)?(?:流程|配方)?(?:都|全|全部)?" + delete,
        scope_text,
    )
    # 删除语句中剩余的内容只能是常用确认、指代、操作和连接词。
    # 这样「确认删除另一个新名称流程」不会被宽松的确认正则误套到旧清单。
    count = r"[一二两三四五六七八九十\d]+"
    reference_count = r"(?:这|那)?" + count + r"(?:个|条)(?:流程|配方)?"
    confirmation_words = (
        r"确认|确定|是的|对的|好的|可以|没问题|同意|好啊|好吧|嗯嗯|嗯|是|对|好|行|yes|y|okay|ok|1|"
        r"请|麻烦|帮我|把|将|就|现在|直接|继续|执行|一下|一并|一起|都|全|也|并|掉|了|吧|啊|的|"
        r"流程|配方|版本|其|它们|它|这些|这个|这条|那个|那条|以上|上述|前述|这批|全部|所有|"
        r"之前列出|刚才列出|之前提到|刚才提到|上面|所列|之前|刚才|"
        r"包括|包含|和|与|以及|及|还有|跟|连同|谢谢你|谢谢|辛苦了|#"
    )
    if re.fullmatch(r"(?:" + reference_count + "|" + delete + "|" + confirmation_words + ")+", scope_text) is None:
        return True
    counts = re.findall(r"(?:这|那)(" + count + r")(?:个|条)|(" + count + r")(?:个|条)(?:流程|配方)", scope_text)
    for leading, trailing in counts:
        raw_count = leading or trailing
        digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
                  "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        if raw_count.isdigit():
            target_count = int(raw_count)
        elif raw_count in digits:
            target_count = digits[raw_count]
        else:
            tens, separator, units = raw_count.partition("十")
            target_count = ((digits.get(tens, 1) * 10) + digits.get(units, 0)) if separator else None
        if target_count != len(pending["recipe_ids"]):
            return True
    if mentioned and mentioned != set(pending["recipe_ids"]):
        return covers_all is None and not counts
    if len(pending["recipe_ids"]) > 1 and re.search(r"这个|这条|那个|那条", scope_text):
        return True
    return False


def resolve_confirmation(db: Session, user: User, conversation_id: str, message: str) -> set[str] | None:
    """消费一次确认。None 为普通消息，空集为取消或目标已不存在。"""
    conversation = _conversation(db, user, conversation_id)
    pending = conversation.pending_recipe_deletion
    if not isinstance(pending, dict) or not pending.get("recipe_ids"):
        return None
    decision = confirmation_reply(message)
    # 用户开始别的任务或取消后，后续单独的“确认”不能再删除旧目标。
    conversation.pending_recipe_deletion = {}
    if decision is not True:
        return set() if decision is False else None
    if _changes_pending_targets(message, pending):
        return None
    confirmed = set()
    for recipe_id in pending["recipe_ids"]:
        try:
            record = recipe_service.get_record(db, str(recipe_id))
        except NotFound:
            continue
        if not _in_scope(conversation, record):
            continue
        if record.customer_id:
            require_customer(db, user, record.customer_id)
        confirmed.add(record.recipe_id)
    return confirmed
