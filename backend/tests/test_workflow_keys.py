"""Cache-key behaviour: what each parameter is allowed to invalidate."""

import workflow


ROOT = "/artifacts"
INPUT_HASH = "abc123"


def test_layer_balance_invalidates_only_the_render():
    """The whole point of making the balance a render-stage bus gain.

    If it touched the groove key, moving the slider would re-run the drum-score
    stage and the result page's "seconds, not a job" re-render would be a lie.
    """
    a = {"layer_balance": 0.2}
    b = {"layer_balance": 0.8}

    assert workflow.stems_dir(ROOT, INPUT_HASH, a) == workflow.stems_dir(ROOT, INPUT_HASH, b)
    assert workflow.detect_dir(ROOT, INPUT_HASH, a) == workflow.detect_dir(ROOT, INPUT_HASH, b)
    assert workflow.groove_dir(ROOT, INPUT_HASH, a) == workflow.groove_dir(ROOT, INPUT_HASH, b)
    assert workflow.render_dir(ROOT, INPUT_HASH, a) != workflow.render_dir(ROOT, INPUT_HASH, b)


def test_backbone_params_invalidate_groove_and_render_but_not_detect():
    a = {"backbone_snap_ms": 25}
    b = {"backbone_snap_ms": 5}

    assert workflow.detect_dir(ROOT, INPUT_HASH, a) == workflow.detect_dir(ROOT, INPUT_HASH, b)
    assert workflow.groove_dir(ROOT, INPUT_HASH, a) != workflow.groove_dir(ROOT, INPUT_HASH, b)
    assert workflow.render_dir(ROOT, INPUT_HASH, a) != workflow.render_dir(ROOT, INPUT_HASH, b)


def test_detector_params_invalidate_detect_but_not_the_stems():
    a = {"syl_detector": "nucleus"}
    b = {"syl_detector": "flux"}

    assert workflow.stems_dir(ROOT, INPUT_HASH, a) == workflow.stems_dir(ROOT, INPUT_HASH, b)
    assert workflow.detect_dir(ROOT, INPUT_HASH, a) != workflow.detect_dir(ROOT, INPUT_HASH, b)


def test_pipeline_version_is_in_the_detect_and_groove_keys(monkeypatch):
    """A version bump must not serve old-format artifacts to new code."""
    before = (workflow.detect_dir(ROOT, INPUT_HASH, {}),
              workflow.groove_dir(ROOT, INPUT_HASH, {}),
              workflow.render_dir(ROOT, INPUT_HASH, {}))
    monkeypatch.setattr(workflow, "PIPELINE_VERSION", "999")
    after = (workflow.detect_dir(ROOT, INPUT_HASH, {}),
             workflow.groove_dir(ROOT, INPUT_HASH, {}),
             workflow.render_dir(ROOT, INPUT_HASH, {}))
    for b, a in zip(before, after):
        assert b != a


def test_groove_key_tracks_the_drums_stem_and_events_hashes():
    base = workflow.groove_dir(ROOT, INPUT_HASH, {}, "hash-one")
    other = workflow.groove_dir(ROOT, INPUT_HASH, {}, "hash-two")
    assert base != other
    assert workflow.render_dir(ROOT, INPUT_HASH, {}, "hash-one") != \
        workflow.render_dir(ROOT, INPUT_HASH, {}, "hash-two")


def test_separate_default_is_baked_into_the_stems_key():
    """The -8 -> -14 dB duck change has to invalidate cached stems.

    ``_filter_params`` drops ``None`` so the pipeline default applies; if the
    key hashed ``None`` instead of the value in use, an already-processed source
    would keep serving the old, louder instrumental.
    """
    implicit = workflow.stems_dir(ROOT, INPUT_HASH, {})
    explicit = workflow.stems_dir(
        ROOT, INPUT_HASH, {"drums_duck_db": workflow.SEPARATE_PARAM_DEFAULTS["drums_duck_db"]})
    assert implicit == explicit

    old = workflow.stems_dir(ROOT, INPUT_HASH, {"drums_duck_db": -8.0})
    assert implicit != old


def test_removed_groove_params_are_gone():
    assert "groove_enabled" not in workflow.GROOVE_PARAM_KEYS
    assert "groove_temperature" not in workflow.GROOVE_PARAM_KEYS
    assert "layer_balance" in workflow.RENDER_PARAM_KEYS
    assert "layer_balance" not in workflow.GROOVE_PARAM_KEYS


def test_stage_ids_and_labels_are_unchanged_in_shape():
    assert workflow.STAGES == ["ingest", "separate", "detect", "groove",
                               "render", "finalize"]
    assert workflow.STAGE_LABELS["detect"] == "Detecting syllables"
    assert workflow.STAGE_LABELS["groove"] == "Building the groove"
