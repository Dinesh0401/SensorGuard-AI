"""Cryptographic Model Manifest schema and helper for offline model governance.

Tracks model metadata, algorithm configuration, dataset lineage hash,
and cryptographic SHA-256 integrity digest for offline-trained serving artifacts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ModelManifest:
    """Cryptographic manifest and provenance metadata for trained serving models.

    Attributes:
        model_id: Unique identifier for the model artifact (e.g. 'sage_pilot_q1_2024').
        version: Semantic or release version string (e.g. '1.0.0').
        sha256: Hexadecimal SHA-256 digest of the serialized model binary.
        algorithm: Model architecture name (e.g. 'SAGE-LIGHTGBM').
        created_at: ISO-8601 UTC creation timestamp.
        dataset_hash: Cryptographic fingerprint of training/calibration dataset.
        nominal_coverage: Nominal interval prediction coverage (e.g. 0.90).
        extra: Optional additional metadata dictionary.
    """

    model_id: str
    version: str
    sha256: str
    algorithm: str
    created_at: str
    dataset_hash: str
    nominal_coverage: float
    extra: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert manifest to JSON-serializable dictionary."""
        d = asdict(self)
        if self.extra is None:
            d.pop("extra", None)
        return d

    def to_json(self, indent: int = 2) -> str:
        """Serialize manifest to formatted JSON string."""
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    def save(self, path: str | Path) -> None:
        """Save manifest to a JSON file on disk."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_json(), encoding="utf-8")
        logger.info("Saved model manifest to %s", target)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModelManifest:
        """Instantiate ModelManifest from dictionary."""
        return cls(
            model_id=str(data["model_id"]),
            version=str(data["version"]),
            sha256=str(data["sha256"]).lower(),
            algorithm=str(data["algorithm"]),
            created_at=str(data["created_at"]),
            dataset_hash=str(data["dataset_hash"]),
            nominal_coverage=float(data["nominal_coverage"]),
            extra=data.get("extra"),
        )

    @classmethod
    def load(cls, path: str | Path) -> ModelManifest:
        """Load and parse ModelManifest from a JSON file."""
        target = Path(path)
        if not target.exists():
            raise FileNotFoundError(f"Model manifest not found at: {target}")
        content = target.read_text(encoding="utf-8")
        data = json.loads(content)
        return cls.from_dict(data)

    def verify_file(self, artifact_path: str | Path) -> bool:
        """Verify whether an artifact file matches the manifest's SHA-256 digest."""
        target = Path(artifact_path)
        if not target.exists() or not target.is_file():
            return False
        content = target.read_bytes()
        computed_hash = hashlib.sha256(content).hexdigest().lower()
        return secrets.compare_digest(computed_hash, self.sha256.lower())
