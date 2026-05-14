"""Simple tokenizer for Qdrant sparse vectors.

Builds vocabulary from corpus and converts text to sparse vectors
using term frequency. No external NLP dependencies — just lowercase
+ Unicode NFC normalization.
"""
import re
import unicodedata
from collections import Counter


class SparseTokenizer:
    """Builds vocabulary from corpus and converts text to sparse vectors."""

    def __init__(self):
        self.vocab: dict[str, int] = {}  # word → index
        self._next_id = 0

    def tokenize(self, text: str) -> list[str]:
        """Tokenize text into lowercase NFC-normalized words (len >= 3)."""
        text = unicodedata.normalize("NFC", text.lower())
        return [t for t in re.findall(r'[\w]+', text) if len(t) >= 3]

    def fit(self, texts: list[str]):
        """Build vocabulary from corpus."""
        for text in texts:
            for token in self.tokenize(text):
                if token not in self.vocab:
                    self.vocab[token] = self._next_id
                    self._next_id += 1

    def to_sparse(self, text: str) -> tuple[list[int], list[float]]:
        """Convert text to sparse vector (indices, values=term_freq)."""
        counts = Counter(self.tokenize(text))
        indices, values = [], []
        for word, count in counts.items():
            if word in self.vocab:
                indices.append(self.vocab[word])
                values.append(float(count))
        return indices, values
