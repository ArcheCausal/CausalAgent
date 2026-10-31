import unittest

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from app.agent.worker.usage_collector import UsageCollector


def _result(*, message_usage=None, token_usage=None):
    generations = []
    if message_usage is not None:
        # model_construct bypasses pydantic validation so the malformed-value
        # case (which real AIMessage would reject at construction) can be built.
        message = AIMessage.model_construct(content="", usage_metadata=message_usage)
        generations = [[ChatGeneration(message=message)]]
    llm_output = {"token_usage": token_usage} if token_usage is not None else {}
    return LLMResult(generations=generations, llm_output=llm_output)


class UsageCollectorTests(unittest.TestCase):
    def test_reads_message_usage_without_double_counting(self):
        collector = UsageCollector()
        collector.on_llm_end(
            _result(
                message_usage={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
                token_usage={"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            )
        )
        self.assertEqual(
            collector.snapshot(),
            {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        )

    def test_falls_back_to_llm_output(self):
        collector = UsageCollector()
        collector.on_llm_end(
            _result(
                token_usage={
                    "prompt_tokens": 7,
                    "completion_tokens": 1,
                    "total_tokens": 8,
                }
            )
        )
        self.assertEqual(
            collector.snapshot(),
            {"input_tokens": 7, "output_tokens": 1, "total_tokens": 8},
        )

    def test_accumulates_and_snapshot_is_copy(self):
        collector = UsageCollector()
        collector.on_llm_end(
            _result(message_usage={"input_tokens": 3, "output_tokens": 1, "total_tokens": 4})
        )
        collector.on_llm_end(
            _result(message_usage={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7})
        )
        snapshot = collector.snapshot()
        snapshot["input_tokens"] = 999
        self.assertEqual(
            collector.snapshot(),
            {"input_tokens": 8, "output_tokens": 3, "total_tokens": 11},
        )

    def test_ignores_malformed_values(self):
        collector = UsageCollector()
        collector.on_llm_end(
            _result(
                message_usage={
                    "input_tokens": True,
                    "output_tokens": "x",
                    "total_tokens": -1,
                }
            )
        )
        self.assertEqual(
            collector.snapshot(),
            {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        )


if __name__ == "__main__":
    unittest.main()
