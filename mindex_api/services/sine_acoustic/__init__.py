"""SINE acoustic analysis — frequency, activity, bird, UAV, visualisation."""

__all__ = ["run_full_analysis", "classify_acoustic_file"]


def classify_acoustic_file(*args, **kwargs):
    from .classifier import classify_acoustic_file as _classify

    return _classify(*args, **kwargs)


def run_full_analysis(*args, **kwargs):
    """Lazy import so API boots without NumPy/SciPy loaded."""
    from .pipeline import run_full_analysis as _run

    return _run(*args, **kwargs)
