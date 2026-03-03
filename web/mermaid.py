"""Mermaid diagram rendering helpers."""
import reflex as rx

# JavaScript to reset and re-run Mermaid on all diagram elements.
# Strip data-processed so Mermaid re-renders even on subsequent selections.
# Use double-rAF to ensure React has flushed DOM updates before we run.
MERMAID_RENDER_JS = """
(function() {
    function run() {
        document.querySelectorAll('pre.mermaid').forEach(function(el) {
            el.removeAttribute('data-processed');
        });
        if (typeof mermaid !== 'undefined') {
            mermaid.run({ querySelector: 'pre.mermaid' });
        }
    }
    requestAnimationFrame(function() { requestAnimationFrame(run); });
})();
"""


def mermaid_script() -> rx.Component:
    """Load Mermaid CDN and initialize. Include once per page."""
    return rx.fragment(
        rx.script(src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"),
        rx.script("""
            (function() {
                function init() {
                    if (typeof mermaid === 'undefined') {
                        setTimeout(init, 100);
                        return;
                    }
                    mermaid.initialize({ startOnLoad: false, theme: 'dark' });
                }
                init();
            })();
        """),
    )


def mermaid_diagram(content: rx.Var) -> rx.Component:
    """Render a mermaid diagram from a reactive state var."""
    return rx.box(
        rx.el.pre(content, class_name="mermaid"),
        width="100%",
        overflow_x="auto",
    )
