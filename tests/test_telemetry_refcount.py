"""Telemetry survives concurrent deliveries.

init/shutdown are process-global and the server runs deliveries concurrently.
Without a reference count the first run to finish tore down the providers the
others were still writing through: their spans and log lines went nowhere, and
the investigation then waited the full ingestion ceiling for telemetry that had
already been dropped. It surfaced three times as a 300s "timed out waiting for
3 line(s)" that looked like a Grafana outage.
"""

from __future__ import annotations

import threading

from pipeline import telemetry


def _reset():
    while telemetry.enabled():
        telemetry.shutdown()


class TestReferenceCounting:
    def setup_method(self):
        _reset()

    def teardown_method(self):
        _reset()

    def test_a_second_init_joins_rather_than_rebuilding(self, monkeypatch):
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
        assert telemetry.init() is True
        first = telemetry.tracer()
        assert telemetry.init() is True
        assert telemetry.tracer() is first, "the second caller rebuilt the provider"

    def test_the_first_shutdown_does_not_tear_down_for_the_second(self, monkeypatch):
        """The bug, directly: one run finishing killed the other's telemetry."""
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
        telemetry.init()  # run A
        telemetry.init()  # run B

        telemetry.shutdown()  # A finishes
        assert telemetry.enabled(), "A's shutdown tore down telemetry B was using"

        telemetry.shutdown()  # B finishes
        assert not telemetry.enabled(), "the last caller did not tear down"

    def test_shutdown_without_init_is_harmless(self):
        telemetry.shutdown()
        assert not telemetry.enabled()

    def test_extra_shutdowns_do_not_drive_the_count_negative(self, monkeypatch):
        """Otherwise the next init would never actually tear down again."""
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
        telemetry.init()
        telemetry.shutdown()
        telemetry.shutdown()
        telemetry.shutdown()

        telemetry.init()
        assert telemetry.enabled()
        telemetry.shutdown()
        assert not telemetry.enabled(), "the count went negative and never reset"

    def test_concurrent_init_and_shutdown_leaves_it_closed(self, monkeypatch):
        """Twenty overlapping deliveries, matched pairs, must end closed."""
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
        errors: list[Exception] = []

        def one_delivery():
            try:
                telemetry.init()
                telemetry.shutdown()
            except Exception as exc:  # noqa: BLE001 - collected, then asserted
                errors.append(exc)

        threads = [threading.Thread(target=one_delivery) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, errors
        assert not telemetry.enabled(), "providers left open after every run finished"
