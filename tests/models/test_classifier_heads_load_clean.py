"""PS-18: every pickled classifier head the reference pack ships (the model directories `fetch_reference_models.py`
publishes) loads under the installed scikit-learn without an
`InconsistentVersionWarning`. scikit-learn does not guarantee unpickling across versions and the failure mode is a
changed prediction, not an error, so a version skew between the training image and the runtime must fail here.
Skipped when the weights are not fetched (`scripts/fetch_reference_models.py`)."""
from __future__ import annotations

import warnings

import pytest

from rag_wright.pack_sdk import models_dir


def _heads():
    from scripts.fetch_reference_models import reference_model_dirs

    heads = []
    for d in reference_model_dirs():
        folder = models_dir() / d
        heads += [*folder.rglob("*.pkl"), *folder.rglob("*.joblib")] if folder.is_dir() else []
    return sorted(heads)


@pytest.mark.skipif(not _heads(), reason="reference weights not fetched")
def test_every_shipped_head_loads_without_a_version_skew():
    import joblib
    from sklearn.exceptions import InconsistentVersionWarning

    skewed = []
    for head in _heads():
        with warnings.catch_warnings():
            warnings.simplefilter("error", InconsistentVersionWarning)
            try:
                joblib.load(head)
            except InconsistentVersionWarning as w:
                skewed.append(f"{head.relative_to(models_dir())}: {w}")
    assert skewed == []
