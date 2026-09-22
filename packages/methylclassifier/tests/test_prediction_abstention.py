import numpy as np

from methyl_classifier.utils.prediction_abstention import apply_min_observed_dmp_abstention


def test_abstention_uniform_and_negative_prediction():
    probs = np.array([[0.9, 0.1], [0.2, 0.8]], dtype=np.float64)
    mask = np.array([[True, True, True, False], [True, False, False, False]], dtype=bool)
    out_p, pred, abst = apply_min_observed_dmp_abstention(probs, mask, 0.5, 2)
    assert not abst[0]
    assert abst[1]
    assert pred[0] == 0
    assert pred[1] == -1
    np.testing.assert_allclose(out_p[1], [0.5, 0.5], rtol=1e-10)


def test_abstention_disabled():
    probs = np.array([[0.1, 0.9]], dtype=np.float64)
    mask = np.array([[False, False]], dtype=bool)
    out_p, pred, abst = apply_min_observed_dmp_abstention(probs, mask, 0.0, 2)
    assert not abst[0]
    assert pred[0] == 1
