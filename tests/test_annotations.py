"""Grafana write-back tests.

The separation under test is a security property, not a style preference: the
investigation runs on a read-only credential, and nothing reaches this module
until a conclusion has validated and a human has approved.
"""

from __future__ import annotations

import os
import time

import pytest

from agent.annotations import GrafanaWriter, WriterConfig, annotation_text
from agent.grafana import GrafanaClient, GrafanaConfig

GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://localhost:3000")


def _stack_up() -> bool:
    try:
        return (
            GrafanaClient(GrafanaConfig(url=GRAFANA_URL), timeout=3).health().get("database")
            == "ok"
        )
    except Exception:
        return False


class TestAnnotationText:
    """Pure formatting - no network, runs in the fast tier."""

    def _text(self, **over) -> str:
        args = {
            "asset_id": "ep101_master",
            "failing_stage": "package",
            "preset_id": "pkg_h264_v7",
            "preset_version": 7,
            "measured": -16.8,
            "target": -23.0,
            "resolved": True,
        }
        return annotation_text(**(args | over))

    def test_names_the_preset_version(self):
        assert "pkg_h264_v7 v7" in self._text()

    def test_carries_both_measurement_and_target(self):
        text = self._text()
        assert "-16.8" in text and "-23.0" in text

    def test_states_the_outcome(self):
        assert "repaired and re-validated" in self._text(resolved=True)
        assert "BLOCKED" in self._text(resolved=False)

    def test_is_readable_without_opening_the_run(self):
        text = self._text()
        for token in ("ep101_master", "package", "LUFS"):
            assert token in text


class TestWriterConfig:
    def test_write_token_is_preferred_over_the_read_token(self, monkeypatch):
        monkeypatch.setenv("GRAFANA_SERVICE_ACCOUNT_TOKEN", "read-token")
        monkeypatch.setenv("GRAFANA_WRITE_TOKEN", "write-token")
        assert WriterConfig.from_env().token == "write-token"

    def test_falls_back_to_the_read_token_for_local_dev(self, monkeypatch):
        monkeypatch.delenv("GRAFANA_WRITE_TOKEN", raising=False)
        monkeypatch.setenv("GRAFANA_SERVICE_ACCOUNT_TOKEN", "read-token")
        assert WriterConfig.from_env().token == "read-token"


@pytest.mark.integration
@pytest.mark.skipif(not _stack_up(), reason=f"no Grafana stack at {GRAFANA_URL}")
class TestAgainstLiveGrafana:
    def test_annotation_is_created_and_readable_back(self):
        writer = GrafanaWriter(WriterConfig(url=GRAFANA_URL))
        marker = f"qcic-test-{int(time.time() * 1000)}"
        result = writer.annotate(
            text=f"{marker} integration check",
            tags=["qcic-test"],
            time_ms=int(time.time() * 1000),
        )
        assert result.ok, result.detail
        assert result.remote_id

    def test_incident_degrades_cleanly_when_irm_is_absent(self):
        """Local Grafana has no IRM app. That must not fail the run."""
        writer = GrafanaWriter(WriterConfig(url=GRAFANA_URL))
        result = writer.create_incident(title="qcic-test incident")
        if not result.ok:
            assert "IRM" in result.detail or "HTTP" in result.detail
