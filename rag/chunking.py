"""
文档分块。

优先按照段落切分，再对超长段落做字符窗口切分。

当前 chunk_size 使用字符数，不是精确 Token 数。
后续可以根据不同 Embedding 模型的 tokenizer
替换长度计算函数。
"""

from dataclasses import dataclass

from rag.parser import ParsedSection


QUESTION_SECTION_CHUNK_SIZE = 3000


@dataclass(frozen=True)
class TextChunk:
    """准备写入向量数据库的 Chunk。"""

    chunk_index: int
    text: str
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

    if len(normalized) <= chunk_size:
        return [normalized]

    step = chunk_size - overlap

    chunks = []

    for start in range(
        0,
        len(normalized),
        step,
    ):
        part = normalized[
            start:start + chunk_size
        ].strip()

        if part:
            chunks.append(part)

        if (
            start + chunk_size
            >= len(normalized)
        ):
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
                QUESTION_SECTION_CHUNK_SIZE,
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

            if len(candidate) <= section_chunk_size:
                current = candidate
                continue

            flush_current()

            if len(paragraph) > section_chunk_size:
                for part in split_long_text(
                    paragraph,
                    chunk_size=section_chunk_size,
                    overlap=overlap,
                ):
                    chunks.append(
                        TextChunk(
                            chunk_index=len(chunks),
                            text=part,
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
