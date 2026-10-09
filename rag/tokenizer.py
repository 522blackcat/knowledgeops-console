"""
Token 计数与 Token 边界。

分块长度以 Embedding 模型自己的 tokenizer 为准，
字符数只是近似，会把预算算歪：
中文按字符切会切得比模型允许的更碎，
英文按字符切又会超出模型窗口。
"""

from functools import lru_cache

from transformers import (
    AutoTokenizer,
)

from infrastructure.config import (
    get_settings,
)


@lru_cache(maxsize=1)
def get_tokenizer():
    """进程内复用 Tokenizer，与 Embedding 模型同源。"""

    settings = get_settings()

    return AutoTokenizer.from_pretrained(
        settings.embedding_model,
        local_files_only=(
            settings.rag_model_local_files_only
        ),
    )


def token_offsets(text: str) -> list[tuple[int, int]]:
    """返回每个 Token 在原字符串中的字符区间。"""

    encoded = get_tokenizer()(
        text,
        add_special_tokens=False,
        return_offsets_mapping=True,
        truncation=False,
    )

    return list(
        encoded["offset_mapping"]
    )


def token_length(text: str) -> int:
    """字符串按模型 tokenizer 计出的 Token 数。"""

    if not text:
        return 0

    return len(token_offsets(text))
