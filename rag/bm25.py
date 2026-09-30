"""
BM25 词法检索。

当前实现：
    从 PostgreSQL 分批读取当前知识库的 Chunk，
    在进程内构建 BM25 索引。

适合：
    小型知识库、教学和本地实验。

不适合：
    百万级 Chunk 的生产环境。

大规模部署应将词法检索迁移到
OpenSearch / Elasticsearch 等倒排索引服务。
"""

import re
import uuid

from dataclasses import dataclass

import jieba

from rank_bm25 import BM25Okapi

from sqlalchemy import select

from sqlalchemy.ext.asyncio import (
    AsyncSession,
)

from infrastructure.models import (
    DocumentChunk,
    KnowledgeDocument,
)


CODE_TOKEN_PATTERN = re.compile(
    r"\*\*?[a-zA-Z_]\w*|"
    r"__[a-zA-Z_]\w*__|"
    r"[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)?"
)

STOP_TOKENS = frozenset({
    "python",
    "什么",
    "怎么",
    "如何",
    "应该",
    "回答",
    "解释",
    "讲",
    "说",
    "区别",
    "为什么",
    "是什么",
    "有什么",
    "中",
    "的",
    "和",
    "与",
    "及",
    "对",
    "在",
    "用",
    "该",
})

DOMAIN_TERMS = (
    "位置参数",
    "关键字参数",
    "可变参数",
    "默认参数",
    "可变默认参数",
    "对象共享传参",
    "闭包",
    "自由变量",
    "列表推导式",
    "生成器表达式",
    "引用计数",
    "垃圾回收",
    "循环引用",
    "实例方法",
    "类方法",
    "静态方法",
    "方法解析顺序",
    "哈希冲突",
    "上下文管理器",
    "类型注解",
    "依赖注入",
    "解释型",
    "编译型",
    "字节码",
    "时间复杂度",
    "空间复杂度",
)

for term in DOMAIN_TERMS:
    jieba.add_word(term, freq=100000)


def tokenize(
    text: str,
) -> list[str]:
    """
    基础中英文分词。

    中文使用 jieba 搜索分词，英文与代码符号
    保留为完整 token。
    """

    normalized = text.lower()
    tokens = list(
        jieba.cut_for_search(
            normalized
        )
    )

    tokens.extend(
        CODE_TOKEN_PATTERN.findall(
            normalized
        )
    )

    return [
        token
        for token in tokens
        if token
        and token.strip()
        and token not in STOP_TOKENS
        and not (
            len(token) == 1
            and "\u4e00" <= token <= "\u9fff"
        )
    ]


@dataclass(frozen=True)
class BM25Hit:
    chunk_id: uuid.UUID
    score: float


def searchable_text(
    text: str,
    metadata: dict | None,
) -> str:
    """构造 BM25 使用的加权文本。"""

    metadata = metadata or {}
    heading = str(
        metadata.get("heading") or ""
    ).strip()
    first_line = (
        text.strip().splitlines() or [""]
    )[0].strip()

    weighted_parts = [
        text,
    ]

    for value in (
        heading,
        first_line if first_line.startswith("#") else "",
    ):
        if value:
            weighted_parts.extend(
                [value] * 4
            )

    return "\n".join(weighted_parts)


async def bm25_search(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    knowledge_base_id: uuid.UUID,
    query: str,
    limit: int = 20,
    max_chunks: int = 20000,
) -> list[BM25Hit]:
    """
    对当前知识库构建临时 BM25 索引。

    max_chunks 是显式安全上限。
    超出上限时拒绝执行，而不是静默遗漏
    后面的文档并返回不完整检索结果。
    """

    result = await db.execute(
        select(
            DocumentChunk.id,
            DocumentChunk.text,
            DocumentChunk.metadata_json,
        )
        .join(
            KnowledgeDocument,
            DocumentChunk.document_id
            == KnowledgeDocument.id,
        )
        .where(
            DocumentChunk.tenant_id
            == tenant_id,
            KnowledgeDocument.tenant_id
            == tenant_id,
            KnowledgeDocument.knowledge_base_id
            == knowledge_base_id,
            KnowledgeDocument.status == "ready",
            KnowledgeDocument.deleted_at.is_(None),
            DocumentChunk.document_version
            == KnowledgeDocument.current_version,
        )
        .limit(max_chunks + 1)
    )

    rows = result.all()

    if len(rows) > max_chunks:
        raise RuntimeError(
            "知识库已超过内存 BM25 上限，"
            "请接入独立倒排索引服务"
        )

    if not rows:
        return []

    corpus = [
        tokenize(
            searchable_text(
                row.text,
                row.metadata_json,
            )
        )
        for row in rows
    ]

    query_tokens = tokenize(query)

    if not query_tokens:
        return []

    index = BM25Okapi(
        corpus
    )

    scores = index.get_scores(
        query_tokens
    )

    ranked = sorted(
        enumerate(scores),
        key=lambda item: item[1],
        reverse=True,
    )

    return [
        BM25Hit(
            chunk_id=rows[index].id,
            score=float(score),
        )
        for index, score in ranked[:limit]
        if score > 0
    ]
