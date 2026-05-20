import io
import sys

import pytest

from customize_daemonset import (
    InputValidationError,
    load_yaml_doc,
    load_yaml_docs,
    write_output,
)


def test_load_yaml_docs_multi(tmp_path):
    p = tmp_path / "multi.yaml"
    p.write_text("---\nkind: A\nname: one\n---\nkind: B\nname: two\n")
    docs = load_yaml_docs(p)
    assert len(docs) == 2
    assert docs[0]["kind"] == "A"
    assert docs[1]["kind"] == "B"


def test_load_yaml_docs_missing_file(tmp_path):
    with pytest.raises(InputValidationError, match="not found"):
        load_yaml_docs(tmp_path / "missing.yaml")


def test_load_yaml_doc_single(tmp_path):
    p = tmp_path / "one.yaml"
    p.write_text("kind: List\nitems: []\n")
    d = load_yaml_doc(p)
    assert d["kind"] == "List"


def test_load_yaml_doc_rejects_multi(tmp_path):
    p = tmp_path / "multi.yaml"
    p.write_text("---\na: 1\n---\nb: 2\n")
    with pytest.raises(InputValidationError, match="multiple documents"):
        load_yaml_doc(p)


def test_write_output_stdout(capsys):
    write_output(None, [{"kind": "A"}, {"kind": "B"}])
    out = capsys.readouterr().out
    assert "kind: A" in out
    assert "kind: B" in out
    assert "---" in out


def test_write_output_file(tmp_path):
    p = tmp_path / "out.yaml"
    write_output(p, [{"kind": "A"}, {"kind": "B"}])
    text = p.read_text()
    assert "kind: A" in text
    assert "kind: B" in text


def test_write_output_missing_dir(tmp_path):
    p = tmp_path / "nope" / "out.yaml"
    with pytest.raises(InputValidationError, match="output directory"):
        write_output(p, [{"kind": "A"}])
