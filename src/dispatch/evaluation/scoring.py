"""Deterministic classification scores, never mixed with judged draft quality."""

from collections import Counter

from dispatch.agent.models import Labels, Urgency
from dispatch.domain import TicketCategory, TicketPriority

LABEL_VALUES = {
    "category": [item.value for item in TicketCategory],
    "priority": [item.value for item in TicketPriority],
    "urgency": [item.value for item in Urgency],
    "security_flag": ["false", "true"],
}


def value(label) -> str:
    return str(label).lower()


def classification_scores(expected: list[Labels], predictions: list[Labels | None]) -> dict:
    """Accuracy includes all attempted cases; invalid predictions are a missing class."""
    if len(expected) != len(predictions) or not expected:
        raise ValueError("Expected and predicted labels must have equal, nonzero lengths")
    total = len(expected)
    fields = {}
    exact = 0
    for target, prediction in zip(expected, predictions, strict=True):
        exact += prediction is not None and all(
            getattr(target, field) == getattr(prediction, field) for field in LABEL_VALUES
        )
    for field, labels in LABEL_VALUES.items():
        pairs = Counter(
            (
                value(getattr(target, field)),
                value(getattr(prediction, field)) if prediction else "missing",
            )
            for target, prediction in zip(expected, predictions, strict=True)
        )
        correct = sum(
            count for (target, prediction), count in pairs.items() if target == prediction
        )
        f1s = []
        per_class = {}
        for label in labels:
            tp = pairs[label, label]
            fn = sum(
                count
                for (target, prediction), count in pairs.items()
                if target == label and prediction != label
            )
            fp = sum(
                count
                for (target, prediction), count in pairs.items()
                if target != label and prediction == label
            )
            denominator = 2 * tp + fp + fn
            f1 = 2 * tp / denominator if denominator else 0.0
            f1s.append(f1)
            per_class[label] = {"support": tp + fn, "f1": f1}
        fields[field] = {
            "accuracy": correct / total,
            "macro_f1": sum(f1s) / len(f1s),
            "per_class": per_class,
            "confusion_matrix": {
                target: {
                    prediction: pairs[target, prediction] for prediction in [*labels, "missing"]
                }
                for target in labels
            },
        }
    recalls = {}
    for name, field, positive in [
        ("urgent", "priority", "urgent"),
        ("security", "security_flag", True),
    ]:
        positives = [
            prediction
            for target, prediction in zip(expected, predictions, strict=True)
            if getattr(target, field) == positive
        ]
        recalls[name] = {
            "support": len(positives),
            "recall": sum(
                prediction is not None and getattr(prediction, field) == positive
                for prediction in positives
            )
            / len(positives)
            if positives
            else None,
        }
    return {
        "total": total,
        "valid_predictions": sum(item is not None for item in predictions),
        "invalid_or_failed": sum(item is None for item in predictions),
        "exact_match_accuracy": exact / total,
        "fields": fields,
        "critical_case_recall": recalls,
    }
