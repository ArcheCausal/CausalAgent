import os
import unittest
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from Agent.knowledge_base.rag.operation_datasets import candidate_generation


class _ControlledEmbeddings:
    def embed_documents(self, texts):
        return [[float(len(text)), 1.0, 0.0] for text in texts]

    def embed_query(self, text):
        return [float(len(text)), 1.0, 0.0]


class _ControlledSynthesizer:
    name = "controlled_synthesizer"

    async def generate_scenarios(self, n, knowledge_graph, persona_list, callbacks=None):
        del persona_list, callbacks
        return [{"node": knowledge_graph.nodes[index % len(knowledge_graph.nodes)]} for index in range(n)]

    async def generate_sample(self, scenario, callbacks=None):
        del callbacks
        context = scenario["node"].properties["page_content"]
        return {
            "user_input": "受控 Ragas 生成的问题是什么？",
            "reference": f"受控答案：{context}",
            "reference_contexts": [context],
        }


class RagasGenerateWithChunksEngineTests(unittest.TestCase):
    def test_candidate_path_invokes_installed_ragas_engine_without_network(self):
        fake_llm = FakeListChatModel(responses=["{}"])
        records = [
            {"content": "第一段可供检索的证据", "metadata": {"unit_id": "u1"}},
            {"content": "第二段可供检索的证据", "metadata": {"unit_id": "u2"}},
        ]

        from Agent.knowledge_base.rag.rag_eval.ragas_eval import _install_ragas_vertexai_import_shim

        with patch.dict(
            os.environ,
            {
                "LANGCHAIN_TRACING_V2": "false",
                "LANGCHAIN_API_KEY": "",
                "LANGSMITH_TRACING": "false",
                "LANGSMITH_API_KEY": "",
            },
        ):
            _install_ragas_vertexai_import_shim()
            with patch.object(candidate_generation, "_embeddings", return_value=_ControlledEmbeddings()), \
                    patch("ragas.testset.synthesizers.generate.default_transforms_for_prechunked", return_value=[]), \
                    patch("ragas.testset.synthesizers.generate.default_query_distribution", return_value=[(_ControlledSynthesizer(), 1.0)]), \
                    patch("ragas.testset.synthesizers.generate.generate_personas_from_kg", return_value=[]):
                rows = candidate_generation._generate_ragas_rows(
                    records,
                    testset_size=2,
                    llm=fake_llm,
                    max_workers=1,
                )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["user_input"], "受控 Ragas 生成的问题是什么？")
        self.assertTrue(rows[0]["reference_contexts"])


if __name__ == "__main__":
    unittest.main()
