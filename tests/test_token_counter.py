import unittest

from agent_runtime.eval.token_counter import TokenCounter


class TokenCounterTest(unittest.TestCase):
    def test_token_counter_counts_chinese_text(self) -> None:
        counter = TokenCounter()
        result = counter.count("结构化通信可以降低多智能体协作开销")
        self.assertGreater(result.token_count, 0)
        self.assertGreater(result.char_count, 0)
        self.assertIn(result.token_count_method, {"actual", "compatible"})

    def test_token_counter_counts_english_text(self) -> None:
        counter = TokenCounter()
        result = counter.count("structured handoff reduces communication overhead")
        self.assertGreater(result.token_count, 0)
        self.assertGreater(result.char_count, 0)

    def test_token_counter_estimate_requires_explicit_opt_in(self) -> None:
        counter = TokenCounter(allow_estimate=True)
        result = counter.count("结构化通信")
        self.assertGreater(result.token_count, 0)


if __name__ == "__main__":
    unittest.main()
