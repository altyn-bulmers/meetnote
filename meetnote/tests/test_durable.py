from meetnote.durable import JsonlTail, JsonlWriter, atomic_write, read_jsonl, repair_tail


def test_atomic_write_replaces_content(tmp_path):
    path = tmp_path / "notes.md"
    atomic_write(path, "first")
    atomic_write(path, "second")
    assert path.read_text() == "second"
    assert [p.name for p in tmp_path.iterdir()] == ["notes.md"]  # no temp file left


def test_partial_last_line_is_cut_and_skipped(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_bytes(b'{"a": 1}\n{"a": 2}\n{"a": 3, "tru')  # a crash cut the last append
    assert list(read_jsonl(path)) == [{"a": 1}, {"a": 2}]
    repair_tail(path)
    assert path.read_bytes() == b'{"a": 1}\n{"a": 2}\n'


def test_writer_repairs_then_appends(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_bytes(b'{"a": 1}\n{"broken')
    with JsonlWriter(path) as w:
        w.append({"a": 2})
    assert list(read_jsonl(path)) == [{"a": 1}, {"a": 2}]


def test_repair_of_file_with_only_a_partial_line(tmp_path):
    path = tmp_path / "log.jsonl"
    path.write_bytes(b'{"never finished"')
    repair_tail(path)
    assert path.read_bytes() == b""


def test_tail_returns_only_complete_new_lines(tmp_path):
    path = tmp_path / "log.jsonl"
    tail = JsonlTail(path)
    assert tail.poll() == []
    path.write_bytes(b'{"a": 1}\n{"a": 2')
    assert tail.poll() == [{"a": 1}]
    with open(path, "ab") as f:
        f.write(b'}\n{"a": 3}\n')
    assert tail.poll() == [{"a": 2}, {"a": 3}]
    assert tail.poll() == []
