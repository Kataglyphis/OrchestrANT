import numpy as np
from loguru import logger


class SimpleMLPreprocessor:
    def __init__(self, n_samples: int):
        self.n_samples = n_samples
        self.features = np.array([])
        self.labels = np.array([])
        self.normalized = np.array([])
        self.stats = {}

    def generate_synthetic_data(self) -> tuple:
        logger.debug(
            f"Generating {self.n_samples} samples of synthetic features and labels..."
        )
        self.features = np.random.normal(loc=5.0, scale=2.0, size=(self.n_samples, 3))
        self.labels = (self.features.sum(axis=1) > 15).astype(int)
        logger.info(f"First 5 feature vectors: {self.features[:5]}")
        logger.info(f"First 5 labels: {self.labels[:5]}")
        return self.features, self.labels

    def normalize_features(self) -> np.ndarray:
        if self.features.size == 0:
            logger.warning("No features to normalize.")
            return np.array([])

        mean = self.features.mean(axis=0)
        std = self.features.std(axis=0)
        self.normalized = (self.features - mean) / std

        self.stats = {"mean": mean.tolist(), "std": std.tolist()}
        logger.debug(f"Feature normalization stats: {self.stats}")
        return self.normalized

    def apply_joke_labeling(self) -> np.ndarray:
        if self.labels.size == 0:
            logger.warning("No labels to convert into jokes.")
            return np.array([])

        jokes = np.where(self.labels == 1, "Definitely ML", "Possibly Not")
        logger.info(f"First 5 joke labels: {jokes[:5]}")
        return jokes

    def run_pipeline(self) -> dict:
        logger.info(
            f"Running ML preprocessing pipeline for {self.n_samples} samples..."
        )
        self.generate_synthetic_data()
        self.normalize_features()
        jokes = self.apply_joke_labeling()

        result = {
            "features": self.features,
            "labels": self.labels,
            "normalized": self.normalized,
            **self.stats,
            "joke_labels": jokes,
        }
        logger.success("ML pipeline complete!")
        return result
