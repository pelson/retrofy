"""
Test walrus operator in if statements and while loops.
"""

import io


def test_walrus_in_if():
    """Test walrus operator in if statements."""
    data = ["hello", "hi", "world"]
    results = []

    for item in data:
        if (processed := item.upper()) and len(processed) > 2:
            results.append(processed)

    assert results == ["HELLO", "WORLD"]


def test_walrus_in_while():
    """Test walrus operator in while loops."""
    content = "hello world"
    stream = io.StringIO(content)
    chunks = []

    while chunk := stream.read(5):
        chunks.append(chunk)

    assert "".join(chunks) == content
