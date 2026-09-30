"""
工具参数校验。

目前支持项目内使用的 JSON Schema 子集：
    type
    properties
    required
    additionalProperties
    enum
    items
    minLength / maxLength
    minimum / maximum

不支持的 Schema 关键字不会被当作
已经完成的校验。部署复杂 MCP 工具时，
应接入完整 JSON Schema 校验器。
"""

import json

from typing import Any


class ToolValidationError(ValueError):
    """工具参数不符合服务端 Schema。"""


def parse_tool_arguments(
    raw_arguments: str | dict,
) -> dict:
    """解析模型返回的工具参数。"""

    if isinstance(
        raw_arguments,
        dict,
    ):
        return raw_arguments

    if not isinstance(
        raw_arguments,
        str,
    ):
        raise ToolValidationError(
            "工具参数必须是 JSON 对象"
        )

    try:
        parsed = json.loads(
            raw_arguments
        )

    except json.JSONDecodeError as exc:
        raise ToolValidationError(
            "工具参数不是有效 JSON"
        ) from exc

    if not isinstance(
        parsed,
        dict,
    ):
        raise ToolValidationError(
            "工具参数必须是 JSON 对象"
        )

    return parsed


def validate_value(
    value: Any,
    schema: dict,
    *,
    path: str = "$",
) -> None:
    """递归校验一个 JSON 值。"""

    expected_type = schema.get(
        "type"
    )

    if expected_type == "object":
        if not isinstance(
            value,
            dict,
        ):
            raise ToolValidationError(
                f"{path} 必须是对象"
            )

        properties = schema.get(
            "properties",
            {},
        )

        required = schema.get(
            "required",
            [],
        )

        for field in required:
            if field not in value:
                raise ToolValidationError(
                    f"{path}.{field} 为必填项"
                )

        allow_extra = schema.get(
            "additionalProperties",
            True,
        )

        for field, field_value in (
            value.items()
        ):
            field_schema = (
                properties.get(
                    field
                )
            )

            if field_schema is None:
                if allow_extra is False:
                    raise ToolValidationError(
                        f"{path}.{field} "
                        "不是允许的参数"
                    )

                continue

            validate_value(
                field_value,
                field_schema,
                path=f"{path}.{field}",
            )

    elif expected_type == "array":
        if not isinstance(
            value,
            list,
        ):
            raise ToolValidationError(
                f"{path} 必须是数组"
            )

        item_schema = schema.get(
            "items",
            {},
        )

        for index, item in enumerate(
            value
        ):
            validate_value(
                item,
                item_schema,
                path=f"{path}[{index}]",
            )

    elif expected_type == "string":
        if not isinstance(
            value,
            str,
        ):
            raise ToolValidationError(
                f"{path} 必须是字符串"
            )

        minimum = schema.get(
            "minLength"
        )

        maximum = schema.get(
            "maxLength"
        )

        if (
            minimum is not None
            and len(value) < minimum
        ):
            raise ToolValidationError(
                f"{path} 长度不足"
            )

        if (
            maximum is not None
            and len(value) > maximum
        ):
            raise ToolValidationError(
                f"{path} 长度超过限制"
            )

    elif expected_type == "integer":
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
        ):
            raise ToolValidationError(
                f"{path} 必须是整数"
            )

    elif expected_type == "number":
        if (
            not isinstance(
                value,
                (int, float),
            )
            or isinstance(value, bool)
        ):
            raise ToolValidationError(
                f"{path} 必须是数字"
            )

    elif expected_type == "boolean":
        if not isinstance(
            value,
            bool,
        ):
            raise ToolValidationError(
                f"{path} 必须是布尔值"
            )

    elif expected_type == "null":
        if value is not None:
            raise ToolValidationError(
                f"{path} 必须是 null"
            )

    elif expected_type is not None:
        raise ToolValidationError(
            f"{path} 使用了不支持的 "
            f"Schema 类型：{expected_type}"
        )

    if (
        "enum" in schema
        and value not in schema["enum"]
    ):
        raise ToolValidationError(
            f"{path} 不在允许的枚举值中"
        )

    if (
        expected_type in {
            "integer",
            "number",
        }
    ):
        minimum = schema.get(
            "minimum"
        )

        maximum = schema.get(
            "maximum"
        )

        if (
            minimum is not None
            and value < minimum
        ):
            raise ToolValidationError(
                f"{path} 小于最小值"
            )

        if (
            maximum is not None
            and value > maximum
        ):
            raise ToolValidationError(
                f"{path} 大于最大值"
            )


def validate_tool_arguments(
    arguments: dict,
    schema: dict,
) -> None:
    """按照工具声明校验参数。"""

    validate_value(
        arguments,
        schema,
    )
