"""Copy a legacy GLM-OCR GGUF, adding its missing EOT metadata. Weights are unchanged."""

import argparse
import hashlib
import shutil
import struct
from pathlib import Path


def string(stream):
    length = struct.unpack("<Q", stream.read(8))[0]
    return stream.read(length).decode("utf-8")


def value(stream, kind):
    formats = {
        0: "B",
        1: "b",
        2: "H",
        3: "h",
        4: "I",
        5: "i",
        6: "f",
        7: "?",
        10: "Q",
        11: "q",
        12: "d",
    }
    if kind == 8:
        return string(stream)
    if kind == 9:
        element, count = struct.unpack("<IQ", stream.read(12))
        if element == 8:
            return [string(stream) for _ in range(count)]
        if element in formats:
            stream.seek(struct.calcsize(formats[element]) * count, 1)
            return None
        for _ in range(count):
            value(stream, element)
        return None
    fmt = "<" + formats[kind]
    return struct.unpack(fmt, stream.read(struct.calcsize(fmt)))[0]


def fix(source, destination):
    if destination.exists():
        raise FileExistsError(destination)
    with source.open("rb") as stream:
        magic, version, tensors, count = struct.unpack("<4sIQQ", stream.read(24))
        if magic != b"GGUF" or version != 3:
            raise ValueError("Expected GGUF v3")
        metadata = {}
        for _ in range(count):
            key = string(stream)
            kind = struct.unpack("<I", stream.read(4))[0]
            metadata[key] = value(stream, kind)
        metadata_end = stream.tell()
        if metadata.get("general.architecture") != "glmocr":
            raise ValueError("Expected GLM-OCR architecture")
        tokens = metadata.get("tokenizer.ggml.tokens", [])
        if len(tokens) <= 59253 or tokens[59253] != "<|user|>":
            raise ValueError("Unexpected GLM-OCR token vocabulary")
        if "tokenizer.ggml.eot_token_id" in metadata:
            raise ValueError("EOT already exists; no repair needed")
        # Legacy GLM-OCR uses <|user|> (59253) to end its turn.
        key = b"tokenizer.ggml.eot_token_id"
        entry = struct.pack("<Q", len(key)) + key + struct.pack("<II", 4, 59253)
        for _ in range(tensors):
            string(stream)
            dimensions = struct.unpack("<I", stream.read(4))[0]
            stream.seek(dimensions * 8 + 4 + 8, 1)
        tensor_info_end = stream.tell()
        alignment = metadata.get("general.alignment", 32)
        data_start = ((tensor_info_end + alignment - 1) // alignment) * alignment
        with destination.open("xb") as target:
            target.write(struct.pack("<4sIQQ", magic, version, tensors, count + 1))
            stream.seek(24)
            target.write(stream.read(metadata_end - 24))
            target.write(entry)
            target.write(stream.read(tensor_info_end - metadata_end))
            target.write(b"\0" * ((-target.tell()) % alignment))
            new_data_start = target.tell()
            stream.seek(data_start)
            shutil.copyfileobj(stream, target, length=8 * 1024 * 1024)

    def tensor_hash(path, offset):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            stream.seek(offset)
            while chunk := stream.read(8 * 1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    before = tensor_hash(source, data_start)
    after = tensor_hash(destination, new_data_start)
    if before != after:
        raise ValueError("Tensor checksum mismatch")
    return {
        "eot_token_id": 59253,
        "tensor_sha256": after,
        "tensor_bytes_unchanged": True,
        "source": str(source),
        "output": str(destination),
    }


if __name__ == "__main__":
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    print(json.dumps(fix(args.source, args.destination), indent=2))
