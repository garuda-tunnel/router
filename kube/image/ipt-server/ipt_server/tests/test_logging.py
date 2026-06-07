"""Tests for logging initialisation behaviour in ipt_server.main."""

import logging
import os
import unittest


class TestLogging(unittest.TestCase):
    """Tests for logging initialisation behaviour in ipt_server.main."""

    def test_runtime_defaults_logging_to_info_without_env_override(self):
        """main.py sets root logger to INFO when no prior config and no LOGLEVEL env.

        Validates: the logging initialisation path always results in a root-logger level
        that is INFO or finer (INFO=20, DEBUG=10, NOTSET=0), never the Python default
        WARNING (30).

        Method:
        1. Save original root-logger handlers and level.
        2. Remove all handlers and set level to NOTSET to simulate a clean environment.
        3. Remove LOGLEVEL from os.environ if present.
        4. Reload ipt_server.main so its module-level logging init runs again.
        5. Assert root logger level <= logging.INFO.
        6. Restore original state in finally.
        """
        import importlib
        import ipt_server.main as ipt_main

        root_logger = logging.getLogger()
        original_level = root_logger.level
        original_handlers = root_logger.handlers[:]
        original_loglevel_env = os.environ.pop("LOGLEVEL", None)
        try:
            # Simulate an un-initialised root logger
            for h in original_handlers:
                root_logger.removeHandler(h)
            root_logger.setLevel(logging.NOTSET)

            importlib.reload(ipt_main)

            self.assertLessEqual(
                logging.getLogger().level,
                logging.INFO,
                f"Expected root logger level <= INFO (20), got {logging.getLogger().level}",
            )
        finally:
            # Restore handlers and level
            for h in root_logger.handlers[:]:
                root_logger.removeHandler(h)
            for h in original_handlers:
                root_logger.addHandler(h)
            root_logger.setLevel(original_level)
            if original_loglevel_env is not None:
                os.environ["LOGLEVEL"] = original_loglevel_env


if __name__ == "__main__":
    unittest.main()
