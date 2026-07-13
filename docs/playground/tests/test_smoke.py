from playwright.sync_api import expect, sync_playwright
import pytest

SNIPPET = """def label(x):
    match x:
        case 1:
            return "one"
        case 2:
            return "two"
        case _:
            return "other"
"""


@pytest.mark.slow
def test_playground_converts_match_statement(playground_url):
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(playground_url)
        # Wait for boot (Pyodide cold + retrofy install).
        expect(page.locator("#status.ready")).to_be_visible(timeout=120_000)

        # Replace input editor contents via CodeMirror.
        page.evaluate(
            """([src]) => {
                const { inputView } = window.__playground || {};
                if (!inputView) throw new Error("editor not exposed");
                inputView.dispatch({
                    changes: { from: 0, to: inputView.state.doc.length, insert: src },
                });
            }""",
            [SNIPPET],
        )

        # Wait for the debounced conversion.
        page.wait_for_function(
            """() => {
                const { outputView } = window.__playground || {};
                const text = outputView && outputView.state.doc.toString();
                return text && !text.includes("match ") && text.includes("elif ");
            }""",
            timeout=15_000,
        )

        browser.close()
