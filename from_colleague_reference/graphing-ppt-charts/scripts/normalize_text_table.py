#!/usr/bin/env python3
import csv
import io
import json
import re
from typing import List, Tuple


def parse_markdown_table(text: str) -> Tuple[List[str], List[List[str]]]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    table_lines = [line for line in lines if "|" in line]
    if len(table_lines) < 2:
        raise ValueError("Markdown table not detected")

    def split_row(row: str) -> List[str]:
        row = row.strip().strip("|")
        return [cell.strip() for cell in row.split("|")]

    header = split_row(table_lines[0])
    separator = split_row(table_lines[1])
    if not separator or not all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in separator):
        raise ValueError("Markdown table separator not detected")
    rows = [split_row(line) for line in table_lines[2:]]
    return header, rows


def parse_delimited_text(text: str) -> Tuple[List[str], List[List[str]]]:
    sample = "\n".join(line for line in text.splitlines() if line.strip())[:4096]
    dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    reader = csv.reader(io.StringIO(text), dialect)
    rows = [[cell.strip() for cell in row] for row in reader if any(cell.strip() for cell in row)]
    if len(rows) < 2:
        raise ValueError("Delimited table not detected")
    return rows[0], rows[1:]


def parse_fixed_width_text(text: str) -> Tuple[List[str], List[List[str]]]:
    lines = [line.rstrip("\n") for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        raise ValueError("Not enough lines for fixed-width table")
    if not any(re.search(r"\s{2,}", line) for line in lines[:3]):
        raise ValueError("Fixed-width delimiter not detected")

    def split_line(line: str) -> List[str]:
        return [cell.strip() for cell in re.split(r"\s{2,}", line.strip())]

    rows = [split_line(line) for line in lines]
    width = len(rows[0])
    if width < 2 or any(len(row) != width for row in rows[1:]):
        raise ValueError("Inconsistent fixed-width columns")
    return rows[0], rows[1:]


def parse_key_value_blocks(text: str) -> Tuple[List[str], List[List[str]]]:
    blocks = [block.strip() for block in re.split(r"\n\s*\n", text) if block.strip()]
    records = []
    keys = []
    for block in blocks:
        record = {}
        for line in block.splitlines():
            if ":" not in line and "：" not in line:
                continue
            key, value = re.split(r"[:：]", line, maxsplit=1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            record[key] = value
            if key not in keys:
                keys.append(key)
        if record:
            records.append(record)
    if len(records) < 2 or len(keys) < 2:
        raise ValueError("Key-value blocks not detected")
    rows = [[record.get(key, "") for key in keys] for record in records]
    return keys, rows


def parse_text_table(text: str) -> Tuple[List[str], List[List[str]], str]:
    parsers = [
        ("markdown", parse_markdown_table),
        ("delimited", parse_delimited_text),
        ("fixed-width", parse_fixed_width_text),
        ("key-value", parse_key_value_blocks),
    ]
    last_error = None
    for parser_name, parser in parsers:
        try:
            header, rows = parser(text)
            if len(header) >= 2 and len(rows) >= 1:
                return header, rows, parser_name
        except Exception as exc:
            last_error = exc
    raise ValueError(f"Unable to parse structured text table: {last_error}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    header, rows, parser_name = parse_text_table(args.input)
    payload = {"header": header, "rows": rows, "parser": parser_name}
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
