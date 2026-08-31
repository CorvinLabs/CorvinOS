"""Comprehensive tests for Learned Task Complexity Classifier (ADR-0393).

18 tests covering:
  1-7. Feature extraction (text, context, user, combined, empty, concat)
  8-14. Model training (convergence, CV, confusion, min samples, imbalance)
  15-18. Inference, serialization, feedback, integration
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from core.learning.active_feedback import ActiveFeedbackCollector, FeedbackRecord
from core.learning.classifier_model import LearnedClassifier
from core.learning.classifier_serving import ClassifierService
from core.learning.classifier_trainer import ClassifierTrainer
from core.learning.task_features import TaskFeatureExtractor


class TestFeatureExtraction(unittest.TestCase):
    """Tests for feature extraction."""

    def setUp(self):
        self.extractor = TaskFeatureExtractor(tfidf_max_features=50)
        self.extractor.fit(["fix typo", "refactor system", "update readme"])

    def test_1_text_features(self):
        """Text features extraction (50 dims)."""
        features = self.extractor.extract_text_features("refactor")
        self.assertEqual(len(features), 50)
        self.assertTrue(np.all(features >= 0) and np.all(features <= 1))

    def test_2_context_features(self):
        """Context features extraction (32 dims)."""
        features = self.extractor.extract_context_features(
            "refactor auth", task_type="refactor", prior_complexity="complex"
        )
        self.assertEqual(len(features), 32)

    def test_3_user_features(self):
        """User features extraction (32 dims)."""
        features = self.extractor.extract_user_features(
            user_complexity_history={"complex": 5},
            user_success_rate=0.8, tasks_completed=10
        )
        self.assertEqual(len(features), 32)

    def test_4_combined_features(self):
        """Combined feature vector (114 dims)."""
        fv = self.extractor.extract_all_features("refactor auth")
        self.assertEqual(len(fv.to_array()), 114)

    def test_5_empty_text_handling(self):
        """Handle empty text gracefully."""
        features = self.extractor.extract_text_features("")
        self.assertTrue(np.all(features == 0))

    def test_6_feature_vector_concatenation(self):
        """FeatureVector concatenation works."""
        fv = self.extractor.extract_all_features("test")
        arr = fv.to_array()
        self.assertEqual(len(arr), 114)


class TestModelTraining(unittest.TestCase):
    """Tests for model training."""

    def setUp(self):
        self.extractor = TaskFeatureExtractor(tfidf_max_features=50)
        self.classifier = LearnedClassifier(self.extractor)

    def _get_balanced_data(self, n=60):
        """Generate balanced training data."""
        texts, labels = [], []
        for i in range(n):
            if i < n//3:
                texts.append(f"fix typo {i}")
                labels.append("simple")
            elif i < 2*n//3:
                texts.append(f"update {i}")
                labels.append("moderate")
            else:
                texts.append(f"refactor {i}")
                labels.append("complex")
        return texts, labels

    def test_7_convergence(self):
        """Model trains and converges."""
        texts, labels = self._get_balanced_data()
        metrics = self.classifier.train(texts, labels, n_estimators=10)
        self.assertGreater(metrics.f1_score, 0.0)
        self.assertGreater(metrics.accuracy, 0.0)

    def test_8_cv_scores(self):
        """Cross-validation scores produced."""
        texts, labels = self._get_balanced_data()
        metrics = self.classifier.train(texts, labels, cv_folds=3)
        self.assertGreater(metrics.cv_mean_score, 0.0)

    def test_9_confusion_matrix(self):
        """Confusion matrix generated."""
        texts, labels = self._get_balanced_data()
        metrics = self.classifier.train(texts, labels)
        self.assertIsNotNone(metrics.confusion_matrix)

    def test_10_min_samples(self):
        """Rejects training with too few samples."""
        with self.assertRaises(ValueError):
            self.classifier.train(["test"]*5, ["simple"]*5)

    def test_11_imbalanced_classes(self):
        """Handles imbalanced data."""
        texts = ["fix"]*80 + ["update"]*15 + ["refactor"]*5
        labels = ["simple"]*80 + ["moderate"]*15 + ["complex"]*5
        metrics = self.classifier.train(texts, labels, n_estimators=10)
        self.assertGreater(metrics.f1_score, 0.0)


class TestInferenceAndSerialization(unittest.TestCase):
    """Tests for inference and model save/load."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.extractor = TaskFeatureExtractor(tfidf_max_features=50)
        self.classifier = LearnedClassifier(self.extractor, model_dir=self.temp_dir)
        
        texts, labels = [], []
        for i in range(60):
            if i < 20:
                texts.append(f"fix {i}")
                labels.append("simple")
            elif i < 40:
                texts.append(f"update {i}")
                labels.append("moderate")
            else:
                texts.append(f"refactor {i}")
                labels.append("complex")
        
        self.classifier.train(texts, labels, n_estimators=10)

    def test_12_prediction(self):
        """Predict returns result."""
        fv = self.extractor.extract_all_features("refactor system")
        result = self.classifier.predict(fv)
        self.assertIsNotNone(result.complexity)
        self.assertGreaterEqual(result.confidence, 0.0)
        self.assertLessEqual(result.confidence, 1.0)

    def test_13_save_and_load(self):
        """Save and load model."""
        self.classifier.save("1.0.0")
        classifier2 = LearnedClassifier(self.extractor, model_dir=self.temp_dir)
        classifier2.load("1.0.0")
        self.assertIsNotNone(classifier2.model)
        self.assertEqual(classifier2.model_version, "1.0.0")

    def test_14_metadata_saved(self):
        """Metadata is saved."""
        self.classifier.save("1.5.0")
        metadata_path = self.temp_dir / "classifier_v1.5.0_metadata.json"
        self.assertTrue(metadata_path.exists())


class TestActiveFeedback(unittest.TestCase):
    """Tests for active learning feedback loop."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.collector = ActiveFeedbackCollector(feedback_dir=self.temp_dir)

    def test_15_record_feedback(self):
        """Record operator feedback."""
        record = FeedbackRecord(
            turn_id="turn_1", task_text="fix",
            predicted_complexity="simple", actual_complexity="simple",
            confidence_score=0.9
        )
        self.collector.record_feedback(record)
        self.assertTrue(self.collector.feedback_log_path.exists())

    def test_16_feedback_metrics(self):
        """Calculate feedback metrics."""
        self.collector.record_feedback(FeedbackRecord(
            turn_id="1", task_text="fix", predicted_complexity="simple",
            actual_complexity="simple", confidence_score=0.9
        ))
        self.collector.record_feedback(FeedbackRecord(
            turn_id="2", task_text="refactor", predicted_complexity="simple",
            actual_complexity="complex", confidence_score=0.6
        ))
        
        metrics = self.collector.get_feedback_metrics()
        self.assertEqual(metrics.total_feedback_records, 2)
        self.assertEqual(metrics.correct_predictions, 1)
        self.assertAlmostEqual(metrics.accuracy, 0.5)

    def test_17_confusion_matrix_report(self):
        """Generate confusion matrix."""
        self.collector.record_feedback(FeedbackRecord(
            turn_id="1", task_text="fix", predicted_complexity="simple",
            actual_complexity="simple", confidence_score=0.9
        ))
        report = self.collector.get_confusion_matrix_report()
        self.assertIn("Confusion Matrix", report)


class TestClassifierTrainerAndService(unittest.TestCase):
    """Tests for training data collection and model serving."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp())
        self.trainer = ClassifierTrainer(data_dir=self.temp_dir)

    def test_18a_bootstrap(self):
        """Bootstrap from keyword classifier."""
        dataset = self.trainer.bootstrap_from_keyword_classifier([
            "fix typo", "refactor system"
        ])
        self.assertEqual(len(dataset.training_points), 2)

    def test_18b_training_split(self):
        """Split dataset into train/test."""
        tasks = ["fix"]*20 + ["update"]*20 + ["refactor"]*20
        dataset = self.trainer.bootstrap_from_keyword_classifier(tasks)
        train, test = self.trainer.create_training_split(dataset, test_ratio=0.2)
        self.assertGreater(len(train.training_points), 0)
        self.assertGreater(len(test.training_points), 0)

    def test_18c_classifier_service(self):
        """Classifier service works."""
        extractor = TaskFeatureExtractor(tfidf_max_features=50)
        service = ClassifierService(
            model_dir=self.temp_dir, feature_extractor=extractor
        )
        
        # Train a model
        classifier = LearnedClassifier(extractor, model_dir=self.temp_dir)
        texts = ["fix"]*20 + ["update"]*20 + ["refactor"]*20
        labels = ["simple"]*20 + ["moderate"]*20 + ["complex"]*20
        classifier.train(texts, labels, n_estimators=10)
        classifier.save("test_v1.0.0")
        
        # Use service
        service.load_model("test_v1.0.0")
        result = service.predict("refactor system")
        self.assertIsNotNone(result.complexity)
        
        metrics = service.get_inference_metrics()
        self.assertEqual(metrics["total_predictions"], 1)

    def test_18d_integration_with_adaptive_budget(self):
        """Integration with adaptive budget."""
        from operator.context_engineering.adaptive_budget import (
            AdaptiveBudget, TokenBudget
        )
        
        extractor = TaskFeatureExtractor(tfidf_max_features=50)
        classifier = LearnedClassifier(extractor)
        
        texts = ["fix"]*20 + ["update"]*20 + ["refactor"]*20
        labels = ["simple"]*20 + ["moderate"]*20 + ["complex"]*20
        classifier.train(texts, labels, n_estimators=10)
        
        fv = extractor.extract_all_features("refactor")
        prediction = classifier.predict(fv)
        
        budget = AdaptiveBudget.allocate_for_task(
            prediction.complexity,
            base_budget=TokenBudget(2000, 800, 600, 1600)
        )
        self.assertGreater(budget.total(), 0)


if __name__ == "__main__":
    unittest.main()
