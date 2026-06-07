"""Shared timing helpers used across IPT server components."""

from functools import wraps
import time
import logging
from contextlib import contextmanager


logger = logging.getLogger(__name__)


def timeit(func):
    """Decorate a function and log its execution time with arguments."""

    @wraps(func)
    def timeit_wrapper(*args, **kwargs):
        """Execute wrapped function and emit timing log entry."""
        start_time = time.perf_counter()
        result = func(*args, **kwargs)
        end_time = time.perf_counter()
        total_time = end_time - start_time
        s_ars = [a.__repr__() for a in args]
        s_kws = [f"{k}={v.__repr__()}" for k, v in kwargs.items()]
        logger.info(
            f"Function {func.__name__}{s_ars} {s_kws} Took {total_time:.4f} seconds"
        )
        return result

    return timeit_wrapper


@contextmanager
def TimeMeasure(description: str):
    """Context manager that logs elapsed time for a labeled code block."""
    start_time = time.perf_counter()
    yield
    end_time = time.perf_counter()
    duration = end_time - start_time
    logger.info(f"{description} took {duration:.4f} seconds")
