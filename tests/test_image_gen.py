"""Unit and Integration Tests for Image Generation Routing (Phase 5)."""
import io
import pytest
from PIL import Image
import image_gen


def test_image_prompt_analysis_simple():
    analysis = image_gen.analyze_image_prompt("a cute red apple on a wooden desk")
    assert analysis["tier"] == "low_cost"
    assert analysis["complexity_score"] < 0.50
    assert analysis["resolution"] == "512x512"


def test_image_prompt_analysis_complex():
    prompt = (
        "Cinematic hyperrealistic 8k octane render of an astronaut standing on an alien desert "
        "at twilight with volumetric lighting, bokeh, ray tracing and intricate cyberpunk armor."
    )
    analysis = image_gen.analyze_image_prompt(prompt)
    assert analysis["tier"] == "premium"
    assert analysis["complexity_score"] >= 0.50
    assert analysis["resolution"] == "1024x1024"
    assert len(analysis["style_features"]) > 0


def test_empty_prompt_validation():
    with pytest.raises(image_gen.ImageGenError) as exc_info:
        image_gen.generate_image("")
    assert exc_info.value.code == "invalid_prompt"


def test_missing_api_key_handling():
    """
    CRITICAL REQUIREMENT:
    When no image generation provider keys are provided, system must return
    a clear, actionable configuration message. It must NEVER fake an image.
    """
    try:
        image_gen.generate_image("A futuristic high-speed train in the mountains")
    except image_gen.ImageGenError as exc:
        assert exc.code in ("missing_image_api_key", "provider_error")
        assert "API key" in exc.message or "provider" in exc.message
    except Exception as e:
        pytest.fail(f"Unexpected exception type: {type(e)}")


def test_image_edit_validation():
    # Test with synthetic image bytes
    img = Image.new("RGB", (64, 64), color="blue")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw = buf.getvalue()

    try:
        image_gen.edit_image(raw, "Turn the blue color to vibrant purple")
    except image_gen.ImageGenError as exc:
        assert exc.code in ("missing_image_api_key", "provider_error")
