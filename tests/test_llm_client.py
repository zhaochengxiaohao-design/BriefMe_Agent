from agent.llm_client import parse_chat_completion


def test_parse_plain_reply() -> None:
    result = parse_chat_completion(
        {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "你好"},
                    "finish_reason": "stop",
                }
            ]
        }
    )
    assert result.choices[0].message.content == "你好"
    assert result.choices[0].message.tool_calls is None


def test_parse_tool_calls() -> None:
    result = parse_chat_completion(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "get_daily_stats",
                                    "arguments": '{"date":"2026-04-15"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
    )
    tool = result.choices[0].message.tool_calls[0]
    assert tool.id == "call_1"
    assert tool.function.name == "get_daily_stats"
    assert "2026-04-15" in tool.function.arguments
