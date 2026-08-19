"""MBU GetOrganized integration: GO client + PDF conversion (Linux-first).

A focused package for talking to Aarhus Kommune's GetOrganized (GO) ESDH and
converting the documents it yields into PDF. The GO primitives are vendored from
``mtm-aarhus/oomtm``; SharePoint, Nova, OCR, and the Windows-only tooling are
deliberately out of scope (OCR runs in the consuming application).
"""

from . import discovery, documents, models, pdf
from .config import GoConfig, go_config_from_env
from .models import GoDocument

__all__ = [
    "discovery",
    "documents",
    "models",
    "pdf",
    "GoConfig",
    "go_config_from_env",
    "GoDocument",
]
