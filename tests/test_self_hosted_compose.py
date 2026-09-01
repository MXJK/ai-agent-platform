from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_compose_exposes_only_the_single_node_product_topology() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    assert set(services) == {"app", "migrate", "postgres", "qdrant"}
    assert "ports" not in services["postgres"]
    assert "ports" not in services["qdrant"]
    assert services["app"]["ports"] == [
        "127.0.0.1:${SELF_HOSTED_PORT:-8000}:8000"
    ]
    assert services["app"]["depends_on"]["migrate"]["condition"] == (
        "service_completed_successfully"
    )


def test_compose_locks_reused_single_process_backends_and_workspace_boundary() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    environment = compose["x-app-environment"]
    app = compose["services"]["app"]

    assert environment["RUNTIME_PROFILE"] == "custom"
    assert environment["SESSION_REPOSITORY"] == "postgres"
    assert environment["AGENT_RUN_STORE"] == "postgres"
    assert environment["CHANGE_SET_STORE"] == "postgres"
    assert environment["DOCUMENT_STORE"] == "postgres"
    assert environment["WORKSPACE_STORE"] == "postgres"
    assert environment["LANGGRAPH_CHECKPOINTER"] == "postgres"
    assert environment["MODEL_REGISTRY_STORE"] == "postgres"
    assert environment["MODEL_SECRET_BACKEND"] == "encrypted_file"
    assert environment["MODEL_PROBE_INTERVAL_SECONDS"] == (
        "${MODEL_PROBE_INTERVAL_SECONDS:-0}"
    )
    assert environment["RAG_VECTOR_STORE"] == "qdrant"
    assert environment["PROJECT_MEMORY_STORE"] == "postgres"
    assert environment["PROJECT_MEMORY_VECTOR_STORE"] == "qdrant"
    assert environment["TASK_QUEUE_BACKEND"] == "in_process"
    assert environment["AUTH_MODE"] == "single_user"
    assert environment["NATIVE_DIRECTORY_PICKER_MODE"] == "disabled"
    assert environment["WORKSPACE_ALLOWED_ROOTS"] == "/workspaces"
    assert environment["SANDBOX_MODE"] == "local"
    assert environment["LIVE_WORKSPACE_WRITES_ENABLED"] == "true"
    assert environment["AGENT_WORKSPACE_DEFAULT_MODE"] == "direct"
    assert environment["AGENT_WORKSPACE_ALLOWED_MODES"] == "direct"
    assert environment["MCP_ENABLED"] == "true"
    assert environment["MCP_CONFIG_PATH"] == "/home/app/.ai-agent-platform/mcp.json"
    assert environment["SKILLS_ENABLED"] == "true"
    assert environment["SKILLS_DIRECTORY_PATH"] == "/home/app/.ai-agent-platform/skills"
    assert "${WORKSPACE_HOST_PATH:-./workspaces}:/workspaces" in app["volumes"]
    assert "${HOME}/.ai-agent-platform:/home/app/.ai-agent-platform" in app["volumes"]
    assert all("docker.sock" not in volume for volume in app["volumes"])


def test_application_image_runs_as_a_non_root_user() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "USER app" in dockerfile
    assert "--uid 1000" in dockerfile
    assert "requirements.self-hosted.txt" in dockerfile
    assert "import ai_agent_platform.api.entrypoint" in dockerfile
    assert "nodejs npm" in dockerfile
    assert "/home/app/.cache/huggingface" in dockerfile
    assert "/home/app/.cache" in dockerfile


def test_self_hosted_image_omits_compatibility_only_dependencies() -> None:
    requirements = "\n".join(
        line
        for line in (ROOT / "requirements.self-hosted.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )

    assert "celery" not in requirements.lower()
    assert "chromadb" not in requirements.lower()
    assert "keyring" not in requirements.lower()
    assert "cryptography" in requirements.lower()
    assert "sentence-transformers" in requirements.lower()
    assert "pytest" not in requirements.lower()


def test_self_hosted_bge_m3_embedding_has_persistent_model_cache() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "EMBEDDING_PROVIDER=sentence_transformer" in example
    assert "EMBEDDING_MODEL=BAAI/bge-m3" in example
    assert "SENTENCE_TRANSFORMER_EMBEDDING_DEVICE=cpu" in example
    assert "model_cache:/home/app/.cache/huggingface" in compose
    assert "model_cache:" in compose


def test_provider_api_keys_are_not_dotenv_configuration() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")

    for name in (
        "OPENAI_API_KEY",
        "DEEPSEEK_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
    ):
        assert name not in example
    assert "MODEL_PROBE_INTERVAL_SECONDS=0" in example
