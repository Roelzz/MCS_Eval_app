"""Reflex app entry point — registers all pages."""

import reflex as rx

from web.pages.dashboard import dashboard_page  # noqa: F401
from web.pages.dataset_detail import dataset_detail_page  # noqa: F401
from web.pages.datasets import datasets_page  # noqa: F401
from web.pages.run_detail import run_detail_page  # noqa: F401
from web.pages.runs import runs_page  # noqa: F401
from web.pages.settings import settings_page  # noqa: F401
from web.pages.setup import setup_page  # noqa: F401

app = rx.App(
    theme=rx.theme(
        appearance="dark",
        accent_color="teal",
        gray_color="slate",
        radius="small",
    ),
    stylesheets=["/custom.css"],
)
