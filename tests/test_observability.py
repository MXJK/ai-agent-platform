import json
import logging
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from ai_agent_platform.core import MetricsRegistry, Settings
from ai_agent_platform.core.observability import (
    ContextFilter,
    JsonLogFormatter,
    log_context,
)
from ai_agent_platform.main import create_app


class MetricsRegistryTests(unittest.TestCase):
    def test_collects_thread_safe_counter_and_timing_summaries(self) -> None:
        metrics = MetricsRegistry()

        metrics.increment("runs_total", 2)
        metrics.set_gauge("queue_depth", 7)
        metrics.observe_ms("run_duration_ms", 10)
        metrics.observe_ms("run_duration_ms", 30)

        snapshot = metrics.snapshot()
        self.assertEqual(snapshot["counters"]["runs_total"], 2)
        self.assertEqual(snapshot["gauges"]["queue_depth"], 7)
        self.assertEqual(snapshot["timings"]["run_duration_ms"]["count"], 2)
        self.assertEqual(snapshot["timings"]["run_duration_ms"]["max_ms"], 30)
        self.assertEqual(
            snapshot["timings"]["run_duration_ms"]["average_ms"],
            20.0,
        )


class LoggingTests(unittest.TestCase):
    def test_json_logs_include_bound_correlation_fields(self) -> None:
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.addFilter(ContextFilter())
        handler.setFormatter(JsonLogFormatter())
        logger = logging.Logger("ai_agent_platform.test")
        logger.addHandler(handler)

        with log_context(request_id="req_test", run_id="run_test"):
            logger.warning(
                "run needs attention",
                extra={"status": "failed", "api_key": "do-not-log"},
            )

        payload = json.loads(stream.getvalue())
        self.assertEqual(payload["request_id"], "req_test")
        self.assertEqual(payload["run_id"], "run_test")
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["api_key"], "[REDACTED]")
        self.assertEqual(payload["message"], "run needs attention")

    def test_json_logs_recursively_redact_config_credentials(self) -> None:
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonLogFormatter())
        logger = logging.Logger("ai_agent_platform.config")
        logger.addHandler(handler)

        logger.warning(
            "resolved config",
            extra={
                "config": {
                    "process_security": {
                        "openai_api_key": "do-not-log-key",
                        "database_url": "postgresql://user:password@db/app",
                    }
                }
            },
        )

        payload = json.loads(stream.getvalue())
        serialized = json.dumps(payload)
        self.assertNotIn("do-not-log-key", serialized)
        self.assertNotIn("user:password", serialized)
        self.assertEqual(
            payload["config"]["process_security"]["openai_api_key"],
            "[REDACTED]",
        )
        self.assertEqual(
            payload["config"]["process_security"]["database_url"],
            "[REDACTED]",
        )


class RequestObservabilityTests(unittest.TestCase):
    def test_propagates_request_id_and_exposes_metrics(self) -> None:
        with TestClient(
            create_app(
                settings=Settings(
                    llm_provider="fake",
                    embedding_provider="local",
                )
            )
        ) as client:
            health_response = client.get(
                "/api/v1/health",
                headers={"X-Request-ID": "req_from_client"},
            )
            invalid_id_response = client.get(
                "/api/v1/health",
                headers={"X-Request-ID": "invalid request id"},
            )
            metrics_response = client.get("/api/v1/metrics")

        self.assertEqual(health_response.headers["x-request-id"], "req_from_client")
        self.assertEqual(health_response.json()["session_storage"], "memory")
        self.assertFalse(health_response.json()["persistent_sessions"])
        self.assertRegex(
            invalid_id_response.headers["x-request-id"],
            r"^req_[a-f0-9]{16}$",
        )
        self.assertEqual(metrics_response.status_code, 200)
        metrics = metrics_response.json()
        self.assertGreaterEqual(metrics["counters"]["http_requests_total"], 2)
        self.assertGreaterEqual(
            metrics["counters"]["http_responses_2xx_total"],
            2,
        )
        self.assertGreaterEqual(
            metrics["timings"]["http_request_duration_ms"]["count"],
            2,
        )

    def test_reports_sqlite_sessions_as_persistent(self) -> None:
        with TemporaryDirectory() as temp_dir:
            settings = Settings(
                llm_provider="fake",
                embedding_provider="local",
                session_repository="sqlite",
                agent_run_store="sqlite",
                local_state_path=str(Path(temp_dir) / "state.sqlite3"),
            )
            with TestClient(create_app(settings=settings)) as client:
                response = client.get("/api/v1/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["session_storage"], "sqlite")
        self.assertTrue(response.json()["persistent_sessions"])


if __name__ == "__main__":
    unittest.main()
