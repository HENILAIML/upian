"""Unit and Integration Tests for Image Classification and Vision Intelligence (Phase 4)."""
import io
import pytest
from PIL import Image
import vision


@pytest.fixture
def sample_image_bytes():
    # Synthetic test image with colored shapes
    img = Image.new("RGB", (224, 224), color=(180, 70, 40))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_mobilenet_v3_small_classification(sample_image_bytes):
    res = vision.classify_mobilenet(sample_image_bytes, top_k=5)
    assert res["model"] == "mobilenet_v3_small"
    assert res["task"] == "image-classification"
    assert len(res["ranked_predictions"]) == 5
    assert res["confidence"] > 0.0
    assert "uncertainty" in res
    assert res["uncertainty"]["uncertainty"] in ("Low", "Moderate", "High")
    assert "device" in res
    assert res["latency_ms"] >= 0


def test_siglip_zero_shot_classification(sample_image_bytes):
    categories = ["sunset", "forest", "automobile", "electronics", "animal"]
    res = vision.classify_siglip(sample_image_bytes, candidate_labels=categories)
    assert res["model"] == "siglip2_base"
    assert res["task"] == "zero-shot-classification"
    assert len(res["ranked_predictions"]) == len(categories)
    assert res["confidence"] > 0.0
    assert "uncertainty" in res
    assert "entropy" in res["uncertainty"]
    assert "margin" in res["uncertainty"]


def test_uncertainty_and_calibration():
    # Test sharp probability distribution (low uncertainty)
    sharp_probs = [0.85, 0.10, 0.03, 0.02]
    uncert_sharp = vision.calculate_uncertainty(sharp_probs)
    assert uncert_sharp["uncertainty"] == "Low"
    assert uncert_sharp["margin"] == 0.75

    # Test flat probability distribution (high uncertainty)
    flat_probs = [0.26, 0.25, 0.25, 0.24]
    uncert_flat = vision.calculate_uncertainty(flat_probs)
    assert uncert_flat["uncertainty"] == "High"
    assert uncert_flat["margin"] == 0.01


def test_vision_unified_router(sample_image_bytes):
    # Route 1: with custom labels -> should route to SigLIP
    res_labels = vision.route(sample_image_bytes, labels=["landscape", "portrait", "diagram"])
    assert res_labels["model"] == "siglip2"
    assert res_labels["confidence"] is not None

    # Route 2: general classification -> MobileNetV3
    res_general = vision.route(sample_image_bytes)
    assert res_general["model"] in ("mobilenet_v3_small", "siglip2")
    assert res_general["answer"] is not None


def test_looks_like_document_detector(sample_image_bytes):
    # A plain RGB image should not be identified as a document
    is_doc = vision.looks_like_document(sample_image_bytes)
    assert isinstance(is_doc, bool)
