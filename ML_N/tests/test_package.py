import ml_n
import signal_core


def test_ml_n_is_importable():
    assert ml_n.__version__ == "0.1.0"


def test_signal_core_is_available():
    assert signal_core.__version__ == "0.1.0"