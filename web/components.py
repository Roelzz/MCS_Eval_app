"""Layout shell, sidebar, and reusable components."""

import reflex as rx


def sidebar_link(
    text: str, href: str, icon: str
) -> rx.Component:
    is_active = rx.State.router.page.path == href
    return rx.link(
        rx.hstack(
            rx.box(
                width="2px",
                height="20px",
                background=rx.cond(
                    is_active, "var(--accent-9)", "transparent"
                ),
                border_radius="1px",
                flex_shrink="0",
            ),
            rx.icon(
                icon,
                size=16,
                color=rx.cond(
                    is_active, "var(--accent-9)", "var(--gray-a9)"
                ),
            ),
            rx.text(
                text,
                size="2",
                weight=rx.cond(is_active, "medium", "regular"),
                color=rx.cond(
                    is_active,
                    "var(--gray-12)",
                    "var(--gray-a10)",
                ),
            ),
            spacing="3",
            align="center",
            width="100%",
            padding="8px 12px",
            border_radius="var(--radius-2)",
            background=rx.cond(
                is_active,
                "var(--accent-a3)",
                "transparent",
            ),
            _hover={"background": "var(--accent-a2)"},
            transition="background 0.15s ease",
        ),
        href=href,
        underline="none",
        width="100%",
    )


def sidebar() -> rx.Component:
    return rx.box(
        rx.vstack(
            # Logo area
            rx.hstack(
                rx.box(
                    rx.icon(
                        "flask-conical",
                        size=18,
                        color="var(--accent-9)",
                    ),
                    padding="6px",
                    border_radius="var(--radius-2)",
                    background="var(--accent-a3)",
                ),
                rx.vstack(
                    rx.text(
                        "CS Evals",
                        size="2",
                        weight="bold",
                        letter_spacing="-0.02em",
                    ),
                    rx.text(
                        "Copilot Studio",
                        size="1",
                        color="var(--gray-a8)",
                    ),
                    spacing="0",
                ),
                spacing="3",
                align="center",
                padding="16px 16px 12px",
            ),
            rx.box(
                height="1px",
                width="100%",
                background=(
                    "linear-gradient(90deg, transparent, "
                    "var(--gray-a4), transparent)"
                ),
            ),
            # Navigation
            rx.vstack(
                sidebar_link("Dashboard", "/", "layout-dashboard"),
                sidebar_link("Datasets", "/datasets", "database"),
                sidebar_link("Eval Runs", "/runs", "play"),
                sidebar_link("Setup", "/setup", "rocket"),
                sidebar_link("Settings", "/settings", "settings"),
                spacing="1",
                width="100%",
                padding="12px 8px",
            ),
            rx.spacer(),
            # Footer
            rx.box(
                height="1px",
                width="100%",
                background=(
                    "linear-gradient(90deg, transparent, "
                    "var(--gray-a4), transparent)"
                ),
            ),
            rx.hstack(
                rx.box(
                    width="6px",
                    height="6px",
                    border_radius="50%",
                    background="var(--score-green, #34d399)",
                    flex_shrink="0",
                ),
                rx.text("Platform v1.0", size="1", color="var(--gray-a7)"),
                spacing="2",
                align="center",
                padding="12px 16px",
            ),
            spacing="0",
            height="100%",
        ),
        width="240px",
        min_width="240px",
        height="100vh",
        border_right="1px solid rgba(255, 255, 255, 0.06)",
        background="rgba(255, 255, 255, 0.01)",
    )


def page_header(
    title: str, description: str = ""
) -> rx.Component:
    items = [
        rx.heading(
            title,
            size="6",
            letter_spacing="-0.03em",
            weight="bold",
        )
    ]
    if description:
        items.append(
            rx.text(description, size="2", color="var(--gray-a9)")
        )
    return rx.vstack(*items, spacing="1", padding_bottom="16px")


def layout(page_content: rx.Component) -> rx.Component:
    return rx.hstack(
        sidebar(),
        rx.box(
            page_content,
            padding="28px 36px",
            flex="1",
            height="100vh",
            overflow_y="auto",
        ),
        spacing="0",
        height="100vh",
        background="#0a0a0f",
    )


def status_badge(status: str) -> rx.Component:
    color_map = {
        "pending": "gray",
        "running": "blue",
        "completed": "green",
        "failed": "red",
    }
    return rx.badge(
        status,
        color_scheme=color_map.get(status, "gray"),
        variant="soft",
    )


def stat_card(
    label: str,
    value: rx.Var | str,
    icon: str,
    color: str = "teal",
) -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.hstack(
                rx.box(
                    rx.icon(
                        icon,
                        size=18,
                        color=f"var(--{color}-9)",
                    ),
                    padding="8px",
                    border_radius="var(--radius-2)",
                    background=f"var(--{color}-a3)",
                ),
                rx.spacer(),
                align="center",
                width="100%",
            ),
            rx.vstack(
                rx.text(
                    label,
                    size="1",
                    color="var(--gray-a8)",
                    weight="medium",
                    text_transform="uppercase",
                    letter_spacing="0.05em",
                ),
                rx.text(
                    value,
                    size="6",
                    weight="bold",
                    letter_spacing="-0.03em",
                    font_family="var(--font-mono)",
                ),
                spacing="1",
            ),
            spacing="3",
            width="100%",
        ),
        width="100%",
    )


def empty_state(
    message: str, icon: str = "inbox"
) -> rx.Component:
    return rx.center(
        rx.vstack(
            rx.box(
                rx.icon(
                    icon, size=32, color="var(--gray-a6)"
                ),
                padding="16px",
                border_radius="50%",
                background="var(--gray-a2)",
            ),
            rx.text(
                message, size="2", color="var(--gray-a8)"
            ),
            align="center",
            spacing="4",
            padding="80px 0",
        ),
        width="100%",
    )


def score_color_badge(
    label: str, score_text: str, color: str
) -> rx.Component:
    return rx.badge(
        label + ": " + score_text,
        color_scheme=color,
        variant="soft",
        size="1",
    )
