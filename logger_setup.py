import logging
from logging.handlers import RotatingFileHandler


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    # Use a private attribute to guard against re-configuration.
    # Checking logger.handlers alone is unreliable: a third-party library may
    # have attached handlers to this logger name before get_logger is called,
    # which would silently skip our file/console handler setup.
    if getattr(logger, "_gold_bot_configured", False):
        return logger

    logger.setLevel(logging.DEBUG)
    # Prevent messages from propagating to the root logger and being handled
    # a second time by any root-level handlers set up by imported libraries.
    logger.propagate = False

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = RotatingFileHandler(
        "bot.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setFormatter(formatter)
    fh.setLevel(logging.DEBUG)

    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    ch.setLevel(logging.DEBUG)

    logger.addHandler(fh)
    logger.addHandler(ch)

    logger._gold_bot_configured = True  # type: ignore[attr-defined]
    return logger
