"""Tests for the cross-layer evaluation suite (README section 15)."""

from __future__ import annotations

from rxauth_ai.evaluation_suite import Metric, collect_metrics, render_report


def test_a_floor_metric_fails_below_its_threshold_and_passes_at_it():
    assert Metric("l", "m", 0.999, floor=1.0).passed is False
    assert Metric("l", "m", 1.0, floor=1.0).passed is True
    assert Metric("l", "m", 1.0, floor=0.9).passed is True


def test_a_ceiling_metric_fails_above_its_threshold():
    """False-support rate is a ceiling: more of it is worse, not better."""
    assert Metric("matching", "false support", 0.01, ceiling=0.0).passed is False
    assert Metric("matching", "false support", 0.0, ceiling=0.0).passed is True


def test_the_report_names_every_breach_rather_than_only_counting_them():
    metrics = [
        Metric("extraction", "field F1", 0.80, floor=1.0),
        Metric("matching", "false support", 0.25, ceiling=0.0),
        Metric("criteria", "criterion F1", 1.0, floor=1.0),
    ]

    report = render_report(metrics)

    assert "**Result: FAIL**" in report
    assert "(1/3 within threshold)" in report
    assert "`extraction` / field F1: 0.800" in report
    assert "`matching` / false support: 0.250" in report


def test_a_clean_report_says_so_explicitly():
    report = render_report([Metric("criteria", "criterion F1", 1.0, floor=1.0)])

    assert "**Result: PASS**" in report
    assert "None." in report


def test_the_suite_scores_every_layer_and_stays_within_threshold():
    """The published claim in reports/evaluation_suite.md, enforced as a test.

    Classification is skipped here because it retrains a model; the `reports`
    CI job runs the full suite including that layer.
    """
    metrics = collect_metrics(include_classification=False)

    layers = {metric.layer for metric in metrics}
    assert layers == {"extraction", "retrieval", "criteria", "matching", "generation"}

    breaches = [
        f"{metric.layer}/{metric.name}={metric.value:.3f} wanted {metric.bound}"
        for metric in metrics
        if not metric.passed
    ]
    assert not breaches, breaches


def test_the_matcher_is_gated_on_never_claiming_unearned_support():
    metrics = collect_metrics(include_classification=False)

    false_support = [metric for metric in metrics if "false-support" in metric.name]

    assert false_support, "false-support rate must be gated, not merely reported"
    assert all(metric.ceiling == 0.0 for metric in false_support)


def test_generation_is_gated_on_producing_no_unsupported_claims():
    metrics = collect_metrics(include_classification=False)

    unsupported = next(metric for metric in metrics if metric.name == "unsupported-claim rate")

    assert unsupported.ceiling == 0.0
    assert unsupported.value == 0.0
