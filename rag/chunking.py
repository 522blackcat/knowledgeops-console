"""
文档分块。

优先按照段落打包，再对超长段落做窗口切分。

chunk_size 与 overlap 的单位都是 Token，
按 Embedding 模型的 tokenizer 计数，
切分点落在 Token 边界上，不按字符硬截。
"""

from dataclasses import dataclass

from rag.parser import ParsedSection
from rag.tokenizer import (
    token_length,
    token_offsets,
)


# 题库式小节过去的口径是 3000 字符窗口。
# 实测 BGE-M3 约 0.556 Token/字符，
# 这里换成等长的 Token 预算，只换单位不换策略。
QUESTION_SECTION_CHUNK_TOKENS = 1600


@dataclass(frozen=True)
class TextChunk:
    """准备写入向量数据库的 Chunk。"""

    chunk_index: int
    text: str
    token_count: int
    source_page: int | None
    metadata: dict


def split_long_text(
    text: str,
    *,
    chunk_size: int,
    overlap: int,
) -> list[str]:
    """对超长文本使用重叠窗口切分。"""

    if chunk_size <= 0:
        raise ValueError(
            "chunk_size 必须大于零"
        )

    if not 0 <= overlap < chunk_size:
        raise ValueError(
            "overlap 必须小于 chunk_size"
        )

    normalized = text.strip()

    if not normalized:
        return []

    offsets = token_offsets(normalized)

    total_tokens = len(offsets)

    if total_tokens <= chunk_size:
        return [normalized]

    step = chunk_size - overlap

    chunks: list[str] = []

    for start in range(
        0,
        total_tokens,
        step,
    ):
        end = min(
            start + chunk_size,
            total_tokens,
        )

        part = normalized[
            offsets[start][0]:offsets[end - 1][1]
        ]

        # 按 Token 下标取出的字符区间重新计数可能多出 1 个：
        # 切点落在词内时 BPE 合并结果会变。
        # 所以以实测值为准回退到预算内，
        # 被回退的那个 Token 会由下一块的overlap 接住。
        while True:
            inner_offsets = token_offsets(part)

            if len(inner_offsets) <= chunk_size:
                break

            part = part[: inner_offsets[chunk_size][0]]

        part = part.strip()

        if part:
            chunks.append(part)

        if end >= total_tokens:
            break

    return chunks


def split_sections(
    sections: list[ParsedSection],
    *,
    chunk_size: int,
    overlap: int,
) -> list[TextChunk]:
    """
    将解析结果转换为连续编号的 Chunk。

    不跨 PDF 页面合并，
    以保留准确的来源页码。
    """

    chunks = []

    for section in sections:
        section_metadata = (
            section.metadata or {}
        )
        section_chunk_size = (
            max(
                chunk_size,
                QUESTION_SECTION_CHUNK_TOKENS,
            )
            if section_metadata.get("heading")
            else chunk_size
        )

        paragraphs = [
            paragraph.strip()
            for paragraph in (
                section.text
                .replace("\r\n", "\n")
                .split("\n\n")
            )
            if paragraph.strip()
        ]

        current = ""

        def flush_current():
            nonlocal current

            if not current:
                return

            for part in split_long_text(
                current,
                chunk_size=section_chunk_size,
                overlap=overlap,
            ):
                chunks.append(
                    TextChunk(
                        chunk_index=len(chunks),
                        text=part,
                        token_count=token_length(
                            part
                        ),
                        source_page=(
                            section.source_page
                        ),
                        metadata=(
                            section_metadata
                        ),
                    )
                )

            current = ""

        for paragraph in paragraphs:
            candidate = (
                f"{current}\n\n{paragraph}"
                if current
                else paragraph
            )

            if (
                token_length(candidate)
                <= section_chunk_size
            ):
                current = candidate
                continue

            flush_current()

            if (
                token_length(paragraph)
                > section_chunk_size
            ):
                for part in split_long_text(
                    paragraph,
                    chunk_size=section_chunk_size,
                    overlap=overlap,
                ):
                    chunks.append(
                        TextChunk(
                            chunk_index=len(chunks),
                            text=part,
                            token_count=token_length(
                                part
                            ),
                            source_page=(
                                section.source_page
                            ),
                            metadata=(
                                section_metadata
                            ),
                        )
                    )

            else:
                current = paragraph

        flush_current()

    return chunks
