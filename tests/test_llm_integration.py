import os
import unittest
from pathlib import Path

from kjfwd_bot.config import LLMConfig, load_dotenv
from kjfwd_bot.llm import OpenAIChatClient


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")


@unittest.skipUnless(os.getenv("KJFWD_RUN_LLM_TEST") == "1", "需要显式启用真实 LLM 测试")
class LLMIntegrationTests(unittest.TestCase):
    def test_real_openai_compatible_endpoint(self):
        base_url = os.getenv("BASE_URL") or ""
        client = OpenAIChatClient(
            LLMConfig(
                base_url=base_url,
                model=os.getenv("MODEL", ""),
                api_key=os.getenv("API_KEY", ""),
                # 当前生产模型只接受 temperature=1；冒烟测试使用服务商兼容值。
                temperature=1,
                # 推理模型可能先消耗一部分输出额度；过小会得到空 content。
                max_tokens=700,
                timeout_seconds=60,
                retries=1,
            )
        )
        result = client.complete("你只进行接口连通性测试。", "请只回复：OK")
        self.assertTrue(result.strip())

    def test_real_endpoint_accepts_kjfwd_system_prompt(self):
        client = OpenAIChatClient(
            LLMConfig(
                base_url=os.getenv("BASE_URL") or "",
                model=os.getenv("MODEL", ""),
                api_key=os.getenv("API_KEY", ""),
                temperature=1,
                max_tokens=700,
                timeout_seconds=60,
                retries=1,
            )
        )
        system = (ROOT / "prompts" / "system.md").read_text(encoding="utf-8-sig")
        result = client.complete(
            system,
            "<conversation_transcript></conversation_transcript>\n"
            "<current_request>这是 CI 健康检查，请简短确认服务可用。</current_request>",
        )
        self.assertTrue(result.strip())


if __name__ == "__main__":
    unittest.main()
