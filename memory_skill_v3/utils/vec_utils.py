import struct
import math


def serialize(vec):
    return struct.pack(f"{len(vec)}f", *vec)


def deserialize(data):
    n = len(data) // 4
    return list(struct.unpack(f"{n}f", data))


def cosine_similarity(a, b):
    dot    = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def cosine_distance(a, b):
    return 1.0 - cosine_similarity(a, b)


def keyword_overlap(kw1, kw2):
    if not kw1 or not kw2:
        return 0.0
    s1, s2 = set(kw1), set(kw2)
    return len(s1 & s2) / max(len(s1 | s2), 1)
