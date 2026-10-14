from pathlib import Path



def test_compose_files_keep_legacy_langchain_config_without_mode_switch():
    """LangSmith 密钥只来自密钥文件；Compose 只传递非密钥的项目名。"""
    forbidden = (
        "LANGSMITH_TRACING=${",
        "LANGSMITH_API_KEY=${",
        "LANGSMITH_PROJECT=${",
        "LANGSMITH_ENDPOINT=${",
        "LANGCHAIN_API_KEY=${",
    )

    for compose_file in (
        "docker-compose.yml",
        "docker-compose.prod.yml",
    ):
        text = Path(compose_file).read_text(encoding="utf-8")
        assert "LANGCHAIN_PROJECT=${LANGCHAIN_PROJECT:-}" in text
        assert not any(item in text for item in forbidden)
